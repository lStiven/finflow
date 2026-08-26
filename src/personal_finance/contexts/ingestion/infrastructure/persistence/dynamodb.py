from __future__ import annotations

from collections.abc import Sequence
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef, QueryInputTypeDef

from personal_finance.contexts.ingestion.application.ports import NotificationSummary
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


_SECONDS_PER_DAY = 86_400

PARTITION_KEY = "pk"
USER_ID_ATTRIBUTE = "user_id"

# The table is keyed by idempotency key because that is what deduplicates an
# arriving email. Listing one person's mail is the reverse question, and a scan
# would answer it at a cost that grows with everybody else's data — on a path a
# screen refreshes.
NOTIFICATIONS_BY_USER_INDEX = "by_user"

# What the index carries beyond the keys, and therefore everything a summary
# can be built from. `raw_content` is deliberately absent: a list never shows a
# body, and projecting one would copy every untrusted email into a second place
# to answer a question that never involves it.
SUMMARY_ATTRIBUTES = (
    "notification_id",
    "message_id",
    "sender",
    "subject",
    "received_at",
    "status",
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
    """`NotificationReader` over the `by_user` index.

    Reads only, and only ever within one partition of that index, so a user's
    query cannot reach another user's mail even by accident. The index is
    eventually consistent, which is the right trade here: an email that
    arrived a moment ago may take an instant to show up on a list, and no
    decision depends on this answer.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        """Paginated: a query returns at most 1 MB, and a silently truncated
        list would hide somebody's own mail from them.
        """
        request: QueryInputTypeDef = {
            "TableName": self._table_name,
            "IndexName": NOTIFICATIONS_BY_USER_INDEX,
            "KeyConditionExpression": f"{USER_ID_ATTRIBUTE} = :user_id",
            "ExpressionAttributeValues": {":user_id": {"S": str(user_id.value)}},
        }
        summaries: list[NotificationSummary] = []

        while True:
            response = self._client.query(**request)
            summaries.extend(to_summary(item) for item in response.get("Items", []))
            start_key = response.get("LastEvaluatedKey")

            if not start_key:
                return summaries

            request["ExclusiveStartKey"] = start_key
