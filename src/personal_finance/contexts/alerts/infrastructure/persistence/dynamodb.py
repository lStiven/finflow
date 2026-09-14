"""Storage for channels, the links that bind them, and what has been sent.

One table, and the partition says how a record is found rather than who owns
it — because two of the four are looked up by something that is not a user:

    USER#<user>  CHANNEL#<id>            the channel and its preferences
    USER#<user>  EVENT#<event>#<channel> this fact already reached that channel
    LINK#<hash>  LINK                    a live invitation, TTL'd in minutes
    CHAT#<chat>  CHAT                    which account a destination belongs to

The webhook arrives holding a token and a chat id and nothing else — working
out whose they are *is* the job — so neither can live under the owner's
partition. What keeps that safe is that the link record names its own scope:
it carries the user and the channel, so redeeming it can only ever touch that
one channel of that one account.

Everything that *is* a user's data does live under `USER#`, and no method here
can answer without being told whose it is. Per-user isolation is the shape of
the key, not a filter somebody has to remember to apply.

The token hash is the partition key, which is why it is SHA-256 and not
bcrypt: a key has to hash the same way twice. Safe here and nowhere near a
password, because the input is 256 random bits.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from decimal import Decimal
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
    ChatAlreadyLinkedError,
)
from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertPreferences,
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
    )


PARTITION_KEY = "alert_key"
SORT_KEY = "record_id"
TTL_ATTRIBUTE = "expires_at"

USER_PREFIX = "USER#"
CHANNEL_PREFIX = "CHANNEL#"
EVENT_PREFIX = "EVENT#"
LINK_PREFIX = "LINK#"
CHAT_PREFIX = "CHAT#"

# The two partitions that hold exactly one record each: the sort key has no
# work to do there, but the table has one, so it gets a constant.
LINK_RECORD = "LINK"
CHAT_RECORD = "CHAT"

CONDITIONAL_CHECK_FAILED = "ConditionalCheckFailed"

# How long a delivery marker outlives the message it is about. SQS keeps a
# message for fourteen days, so anything shorter would let a redrive on day
# fifteen arrive at a marker that had already been swept — and send a second
# copy of a fortnight-old purchase.
DELIVERY_MARKER_DAYS = 30
_SECONDS_PER_DAY = 86_400


class CorruptChannelItemError(Exception):
    """Raised when a stored row cannot be read back as a channel."""


def _string(item: Mapping[str, AttributeValueTypeDef], key: str) -> str | None:
    return item.get(key, {}).get("S")


def _required_string(item: Mapping[str, AttributeValueTypeDef], key: str) -> str:
    value = _string(item, key)

    if value is None:
        raise CorruptChannelItemError(f"Stored channel is missing {key!r}")

    return value


def _number(item: Mapping[str, AttributeValueTypeDef], key: str) -> int:
    raw = item.get(key, {}).get("N")

    return int(raw) if raw is not None else 0


def _optional_number(
    item: Mapping[str, AttributeValueTypeDef],
    key: str,
) -> int | None:
    raw = item.get(key, {}).get("N")

    return int(raw) if raw is not None else None


def _user_key(user_id: UserId, sort_value: str) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": f"{USER_PREFIX}{user_id.value}"},
        SORT_KEY: {"S": sort_value},
    }


def _link_key(token_hash: SecretHash) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": f"{LINK_PREFIX}{token_hash.value}"},
        SORT_KEY: {"S": LINK_RECORD},
    }


def _chat_key(chat_id: ChatId) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": f"{CHAT_PREFIX}{chat_id.value}"},
        SORT_KEY: {"S": CHAT_RECORD},
    }


def _delivery_sort_value(event_id: uuid.UUID, channel_id: ChannelId) -> str:
    return f"{EVENT_PREFIX}{event_id}#{channel_id.value}"


def _money_to_value(amount: Money) -> AttributeValueTypeDef:
    return {
        "M": {
            # A string, like money everywhere else here: a float rounds a
            # cent away, and a floor that drifts silently changes what gets
            # announced.
            "amount": {"S": str(amount.amount)},
            "currency": {"S": amount.currency.value},
        },
    }


def _money_from_value(value: AttributeValueTypeDef) -> Money | None:
    stored = value.get("M")

    if not stored:
        return None

    amount = _string(stored, "amount")
    currency = _string(stored, "currency")

    if amount is None or currency is None:
        return None

    return Money(amount=Decimal(amount), currency=Currency(currency))


def _preferences_to_value(
    preferences: AlertPreferences,
) -> AttributeValueTypeDef:
    entries: list[AttributeValueTypeDef] = []

    for entry in preferences.entries:
        stored: dict[str, AttributeValueTypeDef] = {
            "alert_type": {"S": entry.alert_type.value},
            "enabled": {"BOOL": entry.enabled},
        }

        if entry.minimum_amount is not None:
            stored["minimum_amount"] = _money_to_value(entry.minimum_amount)

        entries.append({"M": stored})

    return {"L": entries}


def _preferences_from_item(
    item: Mapping[str, AttributeValueTypeDef],
) -> AlertPreferences:
    """Read back only the opinions that were actually expressed.

    A type this deploy does not know is dropped rather than refused: a row
    written by a newer deploy must not make a channel unreadable, and an
    unknown preference is answered by the default anyway.
    """
    entries: list[AlertPreference] = []

    for stored in item.get("preferences", {}).get("L", []):
        fields = stored.get("M")

        if not fields:
            continue

        raw_type = _string(fields, "alert_type")

        if raw_type is None:
            continue

        try:
            alert_type = AlertType(raw_type)
        except ValueError:
            continue

        minimum = fields.get("minimum_amount")

        entries.append(
            AlertPreference(
                alert_type=alert_type,
                enabled=bool(fields.get("enabled", {}).get("BOOL", True)),
                minimum_amount=_money_from_value(minimum) if minimum else None,
            ),
        )

    return AlertPreferences(entries=tuple(entries))


def _channel_to_item(
    channel: AlertChannel,
) -> dict[str, AttributeValueTypeDef]:
    item: dict[str, AttributeValueTypeDef] = {
        **_user_key(channel.user_id, f"{CHANNEL_PREFIX}{channel.id.value}"),
        "channel_id": {"S": str(channel.id.value)},
        "user_id": {"S": str(channel.user_id.value)},
        "kind": {"S": channel.kind.value},
        "status": {"S": channel.status.value},
        "created_at": {"N": str(channel.created_at.as_epoch_seconds())},
        "preferences": _preferences_to_value(channel.preferences),
    }

    if channel.chat_id is not None:
        item["chat_id"] = {"S": channel.chat_id.value}

    if channel.label is not None:
        item["label"] = {"S": channel.label}

    if channel.verified_at is not None:
        item["verified_at"] = {"N": str(channel.verified_at.as_epoch_seconds())}

    if channel.pending_link_hash is not None:
        item["link_token_hash"] = {"S": channel.pending_link_hash.value}

    return item


def _channel_from_item(
    item: Mapping[str, AttributeValueTypeDef],
) -> AlertChannel:
    chat_id = _string(item, "chat_id")
    verified_at = _optional_number(item, "verified_at")
    link_hash = _string(item, "link_token_hash")

    return AlertChannel(
        id=ChannelId.from_string(_required_string(item, "channel_id")),
        user_id=UserId.from_string(_required_string(item, "user_id")),
        kind=ChannelKind(_required_string(item, "kind")),
        status=ChannelStatus(_required_string(item, "status")),
        created_at=PosixTime.from_epoch_seconds(_number(item, "created_at")),
        preferences=_preferences_from_item(item),
        chat_id=ChatId(chat_id) if chat_id else None,
        label=_string(item, "label"),
        verified_at=(
            PosixTime.from_epoch_seconds(verified_at)
            if verified_at is not None
            else None
        ),
        pending_link_hash=SecretHash(link_hash) if link_hash else None,
    )


def _cancelled_by_condition(error: Exception, *, index: int) -> bool:
    """Whether *our* condition is what cancelled the transaction.

    DynamoDB cancels for several reasons that look alike from outside — a
    throttle, a conflict, a capacity limit. Reading all of them as "the chat
    is taken" would tell somebody their Telegram belongs to a stranger
    because the table was busy.
    """
    reasons = getattr(error, "response", {}).get("CancellationReasons", [])

    return (
        len(reasons) > index and reasons[index].get("Code") == CONDITIONAL_CHECK_FAILED
    )


class DynamoDBAlertChannelRepository:
    """`AlertChannelRepository` over the table described above."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find(
        self,
        *,
        user_id: UserId,
        channel_id: ChannelId,
    ) -> AlertChannel | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_user_key(user_id, f"{CHANNEL_PREFIX}{channel_id.value}"),
        )
        item = response.get("Item")

        return _channel_from_item(item) if item else None

    def list_by_user(self, user_id: UserId) -> Sequence[AlertChannel]:
        return [_channel_from_item(item) for item in self._query_channels(user_id)]

    def save(self, channel: AlertChannel) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item=_channel_to_item(channel),
        )

    def save_verified(self, channel: AlertChannel) -> None:
        if channel.chat_id is None:
            raise ValueError("A verified channel must have somewhere to send")

        reserve: TransactWriteItemTypeDef = {
            "Put": {
                "TableName": self._table_name,
                "Item": {
                    **_chat_key(channel.chat_id),
                    "user_id": {"S": str(channel.user_id.value)},
                    "channel_id": {"S": str(channel.id.value)},
                },
                # Free, or already *this channel's* — which is what a
                # redelivered webhook looks like. Scoped to the channel and
                # not to the account on purpose: per-account would let one
                # owner bind two channels to one chat, and then deleting
                # either would release a reservation the other still delivers
                # through, leaving the chat free for a different account to
                # claim while the first one keeps sending to it.
                "ConditionExpression": (
                    f"attribute_not_exists({SORT_KEY}) OR channel_id = :channel"
                ),
                "ExpressionAttributeValues": {
                    ":channel": {"S": str(channel.id.value)},
                },
            },
        }
        bind: TransactWriteItemTypeDef = {
            "Put": {
                "TableName": self._table_name,
                "Item": _channel_to_item(channel),
                # The transition happens once. A token replayed against a
                # channel that has already moved on finds this and stops.
                "ConditionExpression": (
                    f"attribute_exists({SORT_KEY}) AND #status = :pending"
                ),
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":pending": {"S": ChannelStatus.PENDING.value},
                },
            },
        }

        try:
            self._client.transact_write_items(TransactItems=[reserve, bind])
        except self._client.exceptions.TransactionCanceledException as error:
            if _cancelled_by_condition(error, index=0):
                raise ChatAlreadyLinkedError(
                    "That Telegram chat is already linked to another account",
                ) from error

            if _cancelled_by_condition(error, index=1):
                raise ChannelAlreadyVerifiedError(
                    "That channel is already linked",
                ) from error

            raise

    def delete(self, *, user_id: UserId, channel_id: ChannelId) -> ChatId | None:
        response = self._client.delete_item(
            TableName=self._table_name,
            Key=_user_key(user_id, f"{CHANNEL_PREFIX}{channel_id.value}"),
            ReturnValues="ALL_OLD",
        )
        previous = response.get("Attributes")

        if not previous:
            return None

        chat_id = _string(previous, "chat_id")

        if chat_id is None:
            return None

        # Only if the reservation still names this channel. A delete arriving
        # late must not free a chat that by now belongs to a newer one.
        with suppress(self._client.exceptions.ConditionalCheckFailedException):
            self._client.delete_item(
                TableName=self._table_name,
                Key=_chat_key(ChatId(chat_id)),
                ConditionExpression="channel_id = :channel_id",
                ExpressionAttributeValues={
                    ":channel_id": {"S": str(channel_id.value)},
                },
            )

        return ChatId(chat_id)

    def _query_channels(
        self,
        user_id: UserId,
    ) -> Iterator[dict[str, AttributeValueTypeDef]]:
        """Every channel of one user, following pagination.

        A query returns at most 1 MB. Nobody holds that many channels, but a
        truncated page would silently hide one of somebody's own destinations
        from them — and from the delivery loop.
        """
        request: QueryInputTypeDef = {
            "TableName": self._table_name,
            "KeyConditionExpression": (
                f"{PARTITION_KEY} = :user AND begins_with({SORT_KEY}, :prefix)"
            ),
            "ExpressionAttributeValues": {
                ":user": {"S": f"{USER_PREFIX}{user_id.value}"},
                ":prefix": {"S": CHANNEL_PREFIX},
            },
        }

        while True:
            response = self._client.query(**request)
            yield from response.get("Items", [])
            start_key = response.get("LastEvaluatedKey")

            if not start_key:
                return

            request["ExclusiveStartKey"] = start_key


class DynamoDBChannelLinkRepository:
    """`ChannelLinkRepository`, keyed by the hash of the token presented."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def issue(self, link: ChannelLink) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item={
                **_link_key(link.token_hash),
                "channel_id": {"S": str(link.channel_id.value)},
                "user_id": {"S": str(link.user_id.value)},
                TTL_ATTRIBUTE: {"N": str(link.expires_at.as_epoch_seconds())},
            },
            # A collision on 256 random bits does not happen; asserting it
            # costs nothing and makes a bug loud instead of letting one link
            # overwrite another account's.
            ConditionExpression=f"attribute_not_exists({SORT_KEY})",
        )

    def spend(self, *, token_hash: SecretHash, now: PosixTime) -> ChannelLink | None:
        """Redeem once, and say whose it was, in a single round trip.

        The expiry is compared here rather than left to the table's own
        sweep, which is eventual and routinely hours late. Unknown, spent and
        expired all arrive as the same cancelled condition — which is exactly
        the answer the caller wants, since it reports all three alike.
        """
        try:
            response = self._client.delete_item(
                TableName=self._table_name,
                Key=_link_key(token_hash),
                ConditionExpression=f"{TTL_ATTRIBUTE} > :now",
                ExpressionAttributeValues={
                    ":now": {"N": str(now.as_epoch_seconds())},
                },
                ReturnValues="ALL_OLD",
            )
        except self._client.exceptions.ConditionalCheckFailedException:
            return None

        previous = response.get("Attributes")

        if not previous:
            return None

        return ChannelLink(
            token_hash=token_hash,
            channel_id=ChannelId.from_string(_required_string(previous, "channel_id")),
            user_id=UserId.from_string(_required_string(previous, "user_id")),
            expires_at=PosixTime.from_epoch_seconds(
                _number(previous, TTL_ATTRIBUTE),
            ),
        )

    def discard(self, token_hash: SecretHash) -> None:
        self._client.delete_item(
            TableName=self._table_name,
            Key=_link_key(token_hash),
        )


class DynamoDBDeliveryLog:
    """`DeliveryLog` in the owner's own partition.

    Asked before the send and written after it — the inverted order the port
    explains. A marker that outlived its purpose is harmless: it can only
    suppress a message that can no longer be delivered anyway.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def was_delivered(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
    ) -> bool:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_user_key(user_id, _delivery_sort_value(event_id, channel_id)),
            # Consistent on purpose: the default read may not see a marker
            # written a second ago, and two copies of the same alert is the
            # one thing this exists to prevent.
            ConsistentRead=True,
        )

        return bool(response.get("Item"))

    def record_delivery(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
        now: PosixTime,
    ) -> None:
        expires_at = now.as_epoch_seconds() + DELIVERY_MARKER_DAYS * _SECONDS_PER_DAY

        # Writing it twice is not an error: whoever got there first was also
        # right, and the message they sent is the one that arrived.
        with suppress(self._client.exceptions.ConditionalCheckFailedException):
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    **_user_key(
                        user_id,
                        _delivery_sort_value(event_id, channel_id),
                    ),
                    "channel_id": {"S": str(channel_id.value)},
                    TTL_ATTRIBUTE: {"N": str(expires_at)},
                },
                ConditionExpression=f"attribute_not_exists({SORT_KEY})",
            )
