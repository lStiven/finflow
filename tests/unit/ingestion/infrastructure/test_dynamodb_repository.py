from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef
import pytest

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    CorruptNotificationItemError,
    to_entity,
    to_item,
)
from personal_finance.shared.domain.value_objects import PosixTime


RECEIVED_AT_EPOCH = 1_700_000_000
RETENTION_DAYS = 90


def _item(notification: BankNotification) -> dict[str, AttributeValueTypeDef]:
    return to_item(notification, retention_days=RETENTION_DAYS)


def _notification() -> BankNotification:
    return BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("alerts@bank.com"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.from_epoch_seconds(RECEIVED_AT_EPOCH),
    )


def test_item_round_trip_preserves_the_aggregate() -> None:
    notification = _notification()

    restored = to_entity(_item(notification))

    assert restored.id == notification.id
    assert restored.message_id == notification.message_id
    assert restored.idempotency_key == notification.idempotency_key
    assert restored.sender == notification.sender
    assert restored.subject == notification.subject
    assert restored.raw_content == notification.raw_content
    assert restored.received_at == notification.received_at
    assert restored.status is ProcessingStatus.RECEIVED


def test_restored_aggregate_has_no_pending_events() -> None:
    notification = _notification()

    restored = to_entity(_item(notification))

    # `receive` recorded an event on the original; a record read back from
    # storage must not replay it.
    assert notification.pull_events() != []
    assert restored.pull_events() == []


def test_item_is_partitioned_by_idempotency_key_and_carries_a_ttl() -> None:
    notification = _notification()

    item = _item(notification)

    assert item[PARTITION_KEY] == {"S": notification.idempotency_key.value}
    assert item["expires_at"] == {
        "N": str(RECEIVED_AT_EPOCH + RETENTION_DAYS * 86_400),
    }


def test_status_is_stored_as_a_stable_string() -> None:
    notification = _notification()
    notification.mark_as_queued()

    assert _item(notification)["status"] == {"S": "queued"}


def test_corrupt_item_is_rejected_with_a_named_error() -> None:
    item = _item(_notification())
    del item["sender"]

    with pytest.raises(CorruptNotificationItemError, match="sender"):
        to_entity(item)
