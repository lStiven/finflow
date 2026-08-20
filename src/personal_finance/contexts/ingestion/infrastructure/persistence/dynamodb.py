from __future__ import annotations

import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime


_SECONDS_PER_DAY = 86_400

PARTITION_KEY = "pk"


class CorruptNotificationItemError(Exception):
    """Raised when a stored item does not match the expected attribute shape."""


def _read_string(item: dict[str, AttributeValueTypeDef], key: str) -> str:
    value = item.get(key, {}).get("S")

    if value is None:
        raise CorruptNotificationItemError(
            f"Missing string attribute {key!r} on stored notification",
        )

    return value


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

    return {
        PARTITION_KEY: {"S": notification.idempotency_key.value},
        "notification_id": {"S": str(notification.id.value)},
        "message_id": {"S": notification.message_id.value},
        "sender": {"S": notification.sender.value},
        "subject": {"S": notification.subject},
        "raw_content": {"S": notification.raw_content},
        "received_at": {"N": str(received_at)},
        "status": {"S": notification.status.value},
        "expires_at": {"N": str(received_at + retention_days * _SECONDS_PER_DAY)},
    }


def to_entity(item: dict[str, AttributeValueTypeDef]) -> BankNotification:
    """Rebuild the aggregate from a stored item, with no pending events: what
    a previous attempt already published must never be replayed.
    """
    return BankNotification(
        id=NotificationId(value=uuid.UUID(_read_string(item, "notification_id"))),
        message_id=EmailMessageId(_read_string(item, "message_id")),
        idempotency_key=IdempotencyKey(_read_string(item, PARTITION_KEY)),
        sender=EmailAddress(_read_string(item, "sender")),
        subject=_read_string(item, "subject"),
        raw_content=_read_string(item, "raw_content"),
        received_at=PosixTime.from_epoch_seconds(_read_int(item, "received_at")),
        status=ProcessingStatus(_read_string(item, "status")),
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
            return self._get(notification.idempotency_key)

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

    def _get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key={PARTITION_KEY: {"S": idempotency_key.value}},
            ConsistentRead=True,
        )
        item = response.get("Item")

        return to_entity(item) if item else None
