from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.ingestion.application.ports import (
    NotificationPage,
    NotificationPageRequest,
    NotificationSummary,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef, QueryInputTypeDef


_SECONDS_PER_DAY = 86_400

PARTITION_KEY = "pk"
USER_ID_ATTRIBUTE = "user_id"
RECEIVED_AT_ATTRIBUTE = "received_at"
STATUS_ATTRIBUTE = "status"

# The table is keyed by idempotency key because that is what deduplicates an
# arriving email. Listing one person's mail is the reverse question, and a scan
# would answer it at a cost that grows with everybody else's data — on a path a
# screen refreshes.
#
# Sorted by arrival, because "newest first" is the only order this list is ever
# asked for and a hash-only index has none: answering it meant reading
# everything the account ever received and sorting in memory, so a page of
# twenty cost the same as a page of all of them. The name carries the sort key
# because a global secondary index cannot grow one after the fact — the old
# `by_user` is superseded rather than altered.
NOTIFICATIONS_BY_USER_INDEX = "by_user_received_at"

# What the index carries beyond its keys, and — together with `user_id` and
# `received_at`, which are the keys — everything a summary is built from.
# `raw_content` is deliberately absent: a list never shows a body, and
# projecting one would copy every untrusted email into a second place to answer
# a question that never involves it.
SUMMARY_ATTRIBUTES = (
    "notification_id",
    "message_id",
    "sender",
    "subject",
    STATUS_ATTRIBUTE,
    "deferred_reason",
)


class CorruptNotificationItemError(Exception):
    """Raised when a stored item does not match the expected attribute shape."""


def _read_string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptNotificationItemError(
            f"Missing string attribute {key!r} on stored notification",
        )

    return value


def _read_optional_string(
    item: dict[str, AttributeValueTypeDef], key: str
) -> str | None:
    return item.get(key, {}).get("S")


def _read_int(item: dict[str, AttributeValueTypeDef], key: str) -> int:
    value = item.get(key, {}).get("N")

    if value is None:
        raise CorruptNotificationItemError(
            f"Missing numeric attribute {key!r} on stored notification",
        )

    return int(value)


def to_item(
    notification: BankNotification,
    *,
    retention_days: int,
) -> dict[str, AttributeValueTypeDef]:
    received_at = notification.received_at.as_epoch_seconds()

    item: dict[str, AttributeValueTypeDef] = {
        PARTITION_KEY: {"S": notification.idempotency_key.value},
        "notification_id": {"S": str(notification.id.value)},
        USER_ID_ATTRIBUTE: {"S": str(notification.user_id.value)},
        "message_id": {"S": notification.message_id.value},
        "sender": {"S": notification.sender.value},
        "subject": {"S": notification.subject},
        "raw_content": {"S": notification.raw_content},
        "received_at": {"N": str(received_at)},
        "status": {"S": notification.status.value},
        "expires_at": {"N": str(received_at + retention_days * _SECONDS_PER_DAY)},
    }

    if notification.deferred_reason is not None:
        item["deferred_reason"] = {"S": notification.deferred_reason.value}

    return item


def to_entity(item: dict[str, AttributeValueTypeDef]) -> BankNotification:
    """Rebuild the aggregate from a stored item, with no pending events: what
    a previous attempt already published must never be replayed.
    """
    deferred_reason = _read_optional_string(item, "deferred_reason")

    return BankNotification(
        id=NotificationId(value=uuid.UUID(_read_string(item, "notification_id"))),
        user_id=UserId.from_string(_read_string(item, USER_ID_ATTRIBUTE)),
        message_id=EmailMessageId(_read_string(item, "message_id")),
        idempotency_key=IdempotencyKey(_read_string(item, PARTITION_KEY)),
        sender=EmailAddress(_read_string(item, "sender")),
        subject=_read_string(item, "subject"),
        raw_content=_read_string(item, "raw_content"),
        received_at=PosixTime.from_epoch_seconds(_read_int(item, "received_at")),
        status=ProcessingStatus(_read_string(item, "status")),
        deferred_reason=(
            NotificationDeferredReason(deferred_reason)
            if deferred_reason is not None
            else None
        ),
    )


class DynamoDBBankNotificationRepository:
    """`BankNotificationRepository` backed by a table dedicated to the
    ingestion context, partitioned by idempotency key.

    Deduplication relies on a conditional `PutItem`: the write only lands when
    the key is free, which is what makes at-least-once webhook and SQS
    redelivery safe across process restarts.
    """

    def __init__(
        self,
        *,
        client: DynamoDBClient,
        table_name: str,
        retention_days: int,
    ) -> None:
        self._client = client
        self._table_name = table_name
        self._retention_days = retention_days

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item=self._item(notification),
                ConditionExpression=f"attribute_not_exists({PARTITION_KEY})",
                ReturnValuesOnConditionCheckFailure="ALL_OLD",
            )
        except self._client.exceptions.ConditionalCheckFailedException as error:
            item = error.response.get("Item")

            if item:
                return to_entity(item)

            # Endpoints that ignore `ReturnValuesOnConditionCheckFailure` still
            # tell us the key was taken; read the winner explicitly.
            return self.get(notification.idempotency_key)

        return None

    def save(self, notification: BankNotification) -> None:
        self._client.put_item(
            TableName=self._table_name,
            Item=self._item(notification),
        )

    def _item(
        self,
        notification: BankNotification,
    ) -> dict[str, AttributeValueTypeDef]:
        return to_item(notification, retention_days=self._retention_days)

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": idempotency_key.value}},
            ConsistentRead=True,
        )
        item = response.get("Item")

        return to_entity(item) if item else None


def to_summary(item: dict[str, AttributeValueTypeDef]) -> NotificationSummary:
    """Build the read model from an item off the `by_user` index.

    Separate from `to_entity` because the index carries no body: the aggregate
    could not be rebuilt from this, and asking for it would mean projecting
    every raw email a second time.
    """
    deferred_reason = _read_optional_string(item, "deferred_reason")

    return NotificationSummary(
        id=NotificationId(value=uuid.UUID(_read_string(item, "notification_id"))),
        message_id=EmailMessageId(_read_string(item, "message_id")),
        sender=EmailAddress(_read_string(item, "sender")),
        subject=_read_string(item, "subject"),
        status=ProcessingStatus(_read_string(item, "status")),
        deferred_reason=(
            NotificationDeferredReason(deferred_reason)
            if deferred_reason is not None
            else None
        ),
        received_at=PosixTime.from_epoch_seconds(_read_int(item, "received_at")),
    )


class DynamoDBNotificationReader:
    """`NotificationReader` over the `by_user_received_at` index.

    Reads only, and only ever within one partition of that index, so a user's
    query cannot reach another user's mail even by accident. The index is
    eventually consistent, which is the right trade here: an email that
    arrived a moment ago may take an instant to show up on a list, and no
    decision depends on this answer.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def page_by_user(self, request: NotificationPageRequest) -> NotificationPage:
        """One window of a user's mail, newest first, read as one window.

        The sort key is what makes this cheap: DynamoDB walks the partition
        backwards and stops, so the rows a screen shows cost those rows rather
        than everything the account ever received.

        A status filter is applied by DynamoDB after the read, so a page of
        matches can take several rounds and each asks for the whole window
        again: `Limit` bounds rows *evaluated*, not rows returned, and asking
        only for what is still missing would shrink to one round trip per row
        scanned exactly where the matches are sparse.
        """
        wanted = request.offset + request.limit
        # One past the window, so "is there another page" is answered without
        # counting the whole partition.
        probe = wanted + 1
        query = self._query(request)
        query["Limit"] = probe
        found: list[NotificationSummary] = []

        while True:
            response = self._client.query(**query)
            found.extend(to_summary(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")

            if len(found) >= probe or not start_key:
                break

            query["ExclusiveStartKey"] = start_key

        return NotificationPage(
            notifications=found[request.offset : wanted],
            has_more=len(found) > wanted,
        )

    def count_by_status(self, user_id: UserId) -> Mapping[ProcessingStatus, int]:
        """How many of each status this user has, over their whole history.

        The one answer here that cannot be windowed — a total is a statement
        about everything — so it reads the partition through, projecting the
        single attribute it counts. It is not on the list path: a caller asks
        for it, and the list itself no longer pays for it.
        """
        query: QueryInputTypeDef = {
            **self._partition(user_id),
            "ProjectionExpression": "#status",
            "ExpressionAttributeNames": {"#status": STATUS_ATTRIBUTE},
        }
        counts: Counter[ProcessingStatus] = Counter()

        while True:
            response = self._client.query(**query)
            counts.update(
                ProcessingStatus(_read_string(item, STATUS_ATTRIBUTE))
                for item in response.get("Items", [])
            )
            start_key = response.get("LastEvaluatedKey")

            if not start_key:
                return dict(counts)

            query["ExclusiveStartKey"] = start_key

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        """Everything one user owns, oldest first.

        The whole walk, for the two questions that are about the history
        rather than about a page of it — when mail first got through, and
        which senders were turned away. Paginated because a query returns at
        most 1 MB, and a silently truncated list would hide somebody's own
        mail from them.
        """
        query: QueryInputTypeDef = self._partition(user_id)
        summaries: list[NotificationSummary] = []

        while True:
            response = self._client.query(**query)
            summaries.extend(to_summary(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")

            if not start_key:
                return summaries

            query["ExclusiveStartKey"] = start_key

    def _partition(
        self,
        user_id: UserId,
        *,
        values: dict[str, AttributeValueTypeDef] | None = None,
    ) -> QueryInputTypeDef:
        return {
            "TableName": self._table_name,
            "IndexName": NOTIFICATIONS_BY_USER_INDEX,
            "KeyConditionExpression": f"{USER_ID_ATTRIBUTE} = :user_id",
            "ExpressionAttributeValues": {
                ":user_id": {"S": str(user_id.value)},
                **(values or {}),
            },
        }

    def _query(self, request: NotificationPageRequest) -> QueryInputTypeDef:
        if request.status is None:
            return {
                **self._partition(request.user_id),
                # Newest first: somebody opening this screen is asking whether
                # the email they just forwarded arrived.
                "ScanIndexForward": False,
            }

        return {
            **self._partition(
                request.user_id,
                values={":status": {"S": request.status.value}},
            ),
            "ScanIndexForward": False,
            "FilterExpression": "#status = :status",
            "ExpressionAttributeNames": {"#status": STATUS_ATTRIBUTE},
        }
