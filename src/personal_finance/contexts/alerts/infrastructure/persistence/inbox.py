"""The in-app inbox and the list of recipients, in Alerts' own table.

    USER#<user>  INBOX#<recorded, 12 digits>#<entry>   one alert, TTL'd
    RECIPIENTS   USER#<user>                           somebody Alerts told

The inbox key starts with the moment the fact was recorded, zero-padded, so
the newest entries are one query away — `ScanIndexForward=False` with a
`Limit` — however many a person has. That moment is stable across
redeliveries (the envelope's own time for a movement, a fixed instant for a
week), which is what lets a conditional write make the second copy of the same
fact a no-op instead of a second row.

The recipients live in one partition on purpose. It is small — one row per
person this deployment has ever alerted — and it is the only way a scheduled
job can walk users without a scan and without asking another context.

What an entry holds is stored as JSON in one attribute. Money is a string in
it, never a number, for the reason it is a string on the bus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
import datetime as dt
from decimal import Decimal
import json
import logging
from typing import TYPE_CHECKING, cast
import uuid

from personal_finance.contexts.alerts.application.inbox import InboxEntry, InboxKind
from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    BudgetState,
    CategoryRise,
    MovementAlert,
    MovementDirection,
    MovementOrigin,
    WeeklySummary,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    TTL_ATTRIBUTE,
    USER_PREFIX,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef, QueryInputTypeDef


_logger = logging.getLogger(__name__)

INBOX_PREFIX = "INBOX#"
RECIPIENTS_PARTITION = "RECIPIENTS"

#: How long the app keeps an alert. The inbox answers «what was my last
#: movement», not «what happened in March» — that is Transacciones' job.
INBOX_DAYS = 30
_SECONDS_PER_DAY = 86_400

JsonObject = dict[str, object]


class CorruptInboxItemError(Exception):
    """A stored entry this code cannot read back."""


def _inbox_sort_value(entry: InboxEntry) -> str:
    return f"{INBOX_PREFIX}{entry.created_at.as_epoch_seconds():012d}#{entry.entry_id}"


class DynamoDBInbox:
    """`Inbox` under the owner's own partition."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def record(self, entry: InboxEntry) -> None:
        expires_at = entry.created_at.as_epoch_seconds() + INBOX_DAYS * _SECONDS_PER_DAY

        # Written twice is not an error: the first copy is this copy.
        with suppress(self._client.exceptions.ConditionalCheckFailedException):
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    PARTITION_KEY: {"S": f"{USER_PREFIX}{entry.user_id.value}"},
                    SORT_KEY: {"S": _inbox_sort_value(entry)},
                    "entry_id": {"S": str(entry.entry_id)},
                    "kind": {"S": entry.kind.value},
                    "created_at": {"N": str(entry.created_at.as_epoch_seconds())},
                    "facts": {"S": json.dumps(_facts(entry), separators=(",", ":"))},
                    TTL_ATTRIBUTE: {"N": str(expires_at)},
                },
                ConditionExpression=f"attribute_not_exists({SORT_KEY})",
            )

    def recent(self, *, user_id: UserId, limit: int) -> Sequence[InboxEntry]:
        request: QueryInputTypeDef = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                f"{PARTITION_KEY} = :user AND begins_with({SORT_KEY}, :prefix)"
            ),
            "ExpressionAttributeValues": {
                ":user": {"S": f"{USER_PREFIX}{user_id.value}"},
                ":prefix": {"S": INBOX_PREFIX},
            },
            "ScanIndexForward": False,
            "Limit": limit,
        }
        response = self._client.query(**request)
        now = PosixTime.now().as_epoch_seconds()
        entries: list[InboxEntry] = []

        for item in response.get("Items", []):
            # A time-to-live is eventual and routinely hours late; an expired
            # entry is not shown just because the sweep has not run yet.
            expires = item.get(TTL_ATTRIBUTE, {}).get("N")
            if expires is not None and int(expires) <= now:
                continue

            try:
                entries.append(_entry_from_item(user_id, item))
            except CorruptInboxItemError:
                # One unreadable entry must not take the whole inbox down.
                _logger.warning("skipping an unreadable inbox entry")

        return entries


class DynamoDBRecipients:
    """`Recipients` in one small partition."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def remember(self, *, user_id: UserId, now: PosixTime) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                PARTITION_KEY: {"S": RECIPIENTS_PARTITION},
                SORT_KEY: {"S": f"{USER_PREFIX}{user_id.value}"},
                "last_seen": {"N": str(now.as_epoch_seconds())},
            },
        )

    def everyone(self) -> Sequence[UserId]:
        request: QueryInputTypeDef = {
            "TableName": self._table_name,
            "KeyConditionExpression": f"{PARTITION_KEY} = :recipients",
            "ExpressionAttributeValues": {
                ":recipients": {"S": RECIPIENTS_PARTITION},
            },
        }
        users: list[UserId] = []

        while True:
            response = self._client.query(**request)

            for item in response.get("Items", []):
                value = item.get(SORT_KEY, {}).get("S", "")
                with suppress(ValueError):
                    users.append(UserId.from_string(value.removeprefix(USER_PREFIX)))

            start_key = response.get("LastEvaluatedKey")
            if not start_key:
                return users

            request["ExclusiveStartKey"] = start_key


# ------------------------------------------------------------- serialising


def _facts(entry: InboxEntry) -> JsonObject:
    if entry.movement is not None:
        return _movement_facts(entry.movement)

    if entry.summary is not None:
        return _summary_facts(entry.summary)

    raise ValueError("An inbox entry without facts")  # pragma: no cover


def _movement_facts(alert: MovementAlert) -> JsonObject:
    return {
        "amount": str(alert.amount.amount),
        "currency": alert.amount.currency.value,
        "direction": alert.direction.value,
        "counterparty": alert.counterparty,
        "bank": alert.bank,
        "occurred_at": alert.occurred_at.as_epoch_seconds(),
        "origin": alert.origin.value,
        "unassigned": alert.unassigned,
        "movement_id": alert.movement_id,
        "budgets": [
            {
                "name": budget.name,
                "currency": budget.currency.value,
                "limit": str(budget.limit),
                "spent": str(budget.spent),
                "remaining": str(budget.remaining),
                "state": budget.state.value,
            }
            for budget in alert.budgets
        ],
    }


def _summary_facts(summary: WeeklySummary) -> JsonObject:
    rise = summary.rise

    return {
        "week_start": summary.week_start.isoformat(),
        "week_end": summary.week_end.isoformat(),
        "currency": summary.currency.value,
        "spent": str(summary.spent),
        "movements": summary.movements,
        "typical": None if summary.typical is None else str(summary.typical),
        "rise": (
            None
            if rise is None
            else {
                "category": rise.category,
                "label": rise.label,
                "spent": str(rise.spent),
                "typical": str(rise.typical),
            }
        ),
    }


def _entry_from_item(
    user_id: UserId,
    item: Mapping[str, AttributeValueTypeDef],
) -> InboxEntry:
    try:
        kind = InboxKind(item["kind"]["S"])  # pyright: ignore[reportTypedDictNotRequiredAccess]
        facts = cast("JsonObject", json.loads(item["facts"]["S"]))  # pyright: ignore[reportTypedDictNotRequiredAccess]
        entry_id = uuid.UUID(item["entry_id"]["S"])  # pyright: ignore[reportTypedDictNotRequiredAccess]
        created_at = PosixTime.from_epoch_seconds(
            int(item["created_at"]["N"]),  # pyright: ignore[reportTypedDictNotRequiredAccess]
        )

        if kind is InboxKind.MOVEMENT:
            return InboxEntry(
                user_id=user_id,
                entry_id=entry_id,
                created_at=created_at,
                kind=kind,
                movement=_movement_from(facts),
            )

        return InboxEntry(
            user_id=user_id,
            entry_id=entry_id,
            created_at=created_at,
            kind=kind,
            summary=_summary_from(facts),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise CorruptInboxItemError("An inbox entry could not be read") from error


def _text(facts: JsonObject, key: str) -> str:
    value = facts[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return value


def _movement_from(facts: JsonObject) -> MovementAlert:
    raw_budgets: object = facts.get("budgets", [])
    if not isinstance(raw_budgets, list):
        raise TypeError("budgets")
    budgets = cast("list[JsonObject]", raw_budgets)

    movement_id = facts.get("movement_id")

    return MovementAlert(
        amount=Money(
            amount=Decimal(_text(facts, "amount")),
            currency=Currency(_text(facts, "currency")),
        ),
        direction=MovementDirection(_text(facts, "direction")),
        counterparty=_text(facts, "counterparty"),
        bank=_text(facts, "bank"),
        occurred_at=PosixTime.from_epoch_seconds(
            int(cast("int", facts["occurred_at"]))
        ),
        origin=MovementOrigin(_text(facts, "origin")),
        unassigned=bool(facts.get("unassigned")),
        movement_id=movement_id if isinstance(movement_id, str) else None,
        budgets=tuple(
            BudgetStanding(
                name=_text(budget, "name"),
                currency=Currency(_text(budget, "currency")),
                limit=Decimal(_text(budget, "limit")),
                spent=Decimal(_text(budget, "spent")),
                remaining=Decimal(_text(budget, "remaining")),
                state=BudgetState(_text(budget, "state")),
            )
            for budget in budgets
        ),
    )


def _summary_from(facts: JsonObject) -> WeeklySummary:
    typical = facts.get("typical")
    rise = facts.get("rise")

    return WeeklySummary(
        week_start=dt.date.fromisoformat(_text(facts, "week_start")),
        week_end=dt.date.fromisoformat(_text(facts, "week_end")),
        currency=Currency(_text(facts, "currency")),
        spent=Decimal(_text(facts, "spent")),
        movements=int(cast("int", facts["movements"])),
        typical=None if typical is None else Decimal(cast("str", typical)),
        rise=(
            None
            if not isinstance(rise, dict)
            else CategoryRise(
                category=_text(cast("JsonObject", rise), "category"),
                label=_text(cast("JsonObject", rise), "label"),
                spent=Decimal(_text(cast("JsonObject", rise), "spent")),
                typical=Decimal(_text(cast("JsonObject", rise), "typical")),
            )
        ),
    )
