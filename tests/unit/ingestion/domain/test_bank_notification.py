import pytest

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationIgnored,
    BankNotificationReceived,
)
from personal_finance.contexts.ingestion.domain.exceptions import (
    InvalidNotificationStateError,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime


def test_receive_bank_notification() -> None:
    message_id = EmailMessageId("bank-message-123")

    notification = BankNotification.receive(
        message_id=message_id,
        sender=EmailAddress("BANK@EXAMPLE.COM"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )

    assert notification.message_id == message_id
    assert notification.sender == EmailAddress("bank@example.com")
    assert notification.status is ProcessingStatus.RECEIVED


def test_idempotency_key_is_deterministic() -> None:
    message_id = EmailMessageId("bank-message-123")

    first = IdempotencyKey.from_message_id(message_id)
    second = IdempotencyKey.from_message_id(message_id)

    assert first == second


def test_different_messages_have_different_idempotency_keys() -> None:
    first = IdempotencyKey.from_message_id(
        EmailMessageId("message-1"),
    )
    second = IdempotencyKey.from_message_id(
        EmailMessageId("message-2"),
    )

    assert first != second


def test_email_address_is_normalized() -> None:
    email = EmailAddress("  BANK@EXAMPLE.COM  ")

    assert email.value == "bank@example.com"


def test_notification_can_be_queued() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )

    notification.mark_as_queued()

    assert notification.status is ProcessingStatus.QUEUED


def test_queued_notification_cannot_be_queued_again() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )

    notification.mark_as_queued()

    with pytest.raises(InvalidNotificationStateError):
        notification.mark_as_queued()


def test_receive_records_bank_notification_received_event() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )

    events = notification.pull_events()

    assert events == [
        BankNotificationReceived(
            event_id=events[0].event_id,
            occurred_at=events[0].occurred_at,
            notification_id=notification.id,
            message_id=notification.message_id,
            sender=notification.sender,
            received_at=notification.received_at,
        ),
    ]


def test_pull_events_drains_pending_events() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )

    notification.pull_events()

    assert notification.pull_events() == []


def test_ignore_transitions_status_and_records_event() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("untrusted@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )
    notification.pull_events()

    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    assert notification.status is ProcessingStatus.IGNORED
    events = notification.pull_events()
    assert events == [
        BankNotificationIgnored(
            event_id=events[0].event_id,
            occurred_at=events[0].occurred_at,
            notification_id=notification.id,
            message_id=notification.message_id,
            sender=notification.sender,
            reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER,
        ),
    ]


def test_ignored_notification_cannot_be_ignored_again() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("untrusted@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )

    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    with pytest.raises(InvalidNotificationStateError):
        notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)


def test_queued_notification_cannot_be_ignored() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )
    notification.mark_as_queued()

    with pytest.raises(InvalidNotificationStateError):
        notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)


def test_identity_is_derived_from_the_message_id() -> None:
    def _receive(message_id: str) -> BankNotification:
        return BankNotification.receive(
            message_id=EmailMessageId(message_id),
            sender=EmailAddress("bank@example.com"),
            subject="Purchase notification",
            raw_content="Purchase for COP 50,000",
            received_at=PosixTime.now(),
        )

    # A redelivery of the same email must resolve to the same aggregate, so the
    # identity cannot be random per attempt.
    assert _receive("bank-message-123").id == _receive("bank-message-123").id
    assert _receive("bank-message-123").id != _receive("bank-message-456").id


def test_mark_as_queued_records_an_event() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("bank-message-123"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )
    notification.pull_events()

    notification.mark_as_queued()

    assert notification.status is ProcessingStatus.QUEUED
    events = notification.pull_events()
    assert [type(event).__name__ for event in events] == ["BankNotificationQueued"]


def test_an_ignored_notification_cannot_be_queued() -> None:
    notification = BankNotification.receive(
        message_id=EmailMessageId("bank-message-123"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    with pytest.raises(InvalidNotificationStateError):
        notification.mark_as_queued()
