import pytest

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationIgnored,
    BankNotificationReceived,
    TransactionExtractionDeferred,
)
from personal_finance.contexts.ingestion.domain.exceptions import (
    InvalidNotificationStateError,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


def test_receive_bank_notification() -> None:
    message_id = EmailMessageId("bank-message-123")

    notification = BankNotification.receive(
        user_id=USER_ID,
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

    first = IdempotencyKey.from_message(user_id=USER_ID, message_id=message_id)
    second = IdempotencyKey.from_message(user_id=USER_ID, message_id=message_id)

    assert first == second


def test_different_messages_have_different_idempotency_keys() -> None:
    first = IdempotencyKey.from_message(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
    )
    second = IdempotencyKey.from_message(
        user_id=USER_ID,
        message_id=EmailMessageId("message-2"),
    )

    assert first != second


def test_the_same_message_belongs_to_a_different_key_per_user() -> None:
    message_id = EmailMessageId("bank-message-123")
    other_user = UserId.from_string("22222222-2222-2222-2222-222222222222")

    mine = IdempotencyKey.from_message(user_id=USER_ID, message_id=message_id)
    theirs = IdempotencyKey.from_message(user_id=other_user, message_id=message_id)

    # A forwarded bank email keeps its original Message-ID, so without the user
    # in the key one of the two people would lose their notification.
    assert mine != theirs


def test_email_address_is_normalized() -> None:
    email = EmailAddress("  BANK@EXAMPLE.COM  ")

    assert email.value == "bank@example.com"


def test_notification_can_be_queued() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
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
        user_id=USER_ID,
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
        user_id=USER_ID,
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
            user_id=notification.user_id,
            message_id=notification.message_id,
            sender=notification.sender,
            received_at=notification.received_at,
        ),
    ]


def test_pull_events_drains_pending_events() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
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
        user_id=USER_ID,
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
            user_id=notification.user_id,
            message_id=notification.message_id,
            sender=notification.sender,
            reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER,
        ),
    ]


def test_ignored_notification_cannot_be_ignored_again() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
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
        user_id=USER_ID,
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
            user_id=USER_ID,
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
        user_id=USER_ID,
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
        user_id=USER_ID,
        message_id=EmailMessageId("bank-message-123"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    with pytest.raises(InvalidNotificationStateError):
        notification.mark_as_queued()


def test_ignoring_a_notification_discards_the_email_body() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("stranger@example.com"),
        subject="Purchase",
        raw_content="Private correspondence we were never allowed to read",
        received_at=PosixTime.now(),
    )

    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    # An email from a sender the user did not approve stays in their mailbox.
    # Keeping a copy of its body would be reading it anyway.
    assert notification.raw_content == ""
    # The metadata survives so the user can approve that sender later.
    assert notification.sender == EmailAddress("stranger@example.com")
    assert notification.message_id == EmailMessageId("message-1")


def test_defer_to_fallback_transitions_status_and_records_event() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )
    notification.mark_as_queued()
    notification.start_processing()
    notification.pull_events()

    notification.defer_to_fallback(
        reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
    )

    assert notification.status is ProcessingStatus.PENDING_FALLBACK
    assert (
        notification.deferred_reason
        is NotificationDeferredReason.FALLBACK_FOUND_NOTHING
    )
    events = notification.pull_events()
    assert events == [
        TransactionExtractionDeferred(
            event_id=events[0].event_id,
            occurred_at=events[0].occurred_at,
            notification_id=notification.id,
            user_id=notification.user_id,
            message_id=notification.message_id,
            sender=notification.sender,
            reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
        ),
    ]


def test_a_queued_notification_cannot_be_deferred() -> None:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress("bank@example.com"),
        subject="Purchase",
        raw_content="Purchase information",
        received_at=PosixTime.now(),
    )
    notification.mark_as_queued()

    with pytest.raises(InvalidNotificationStateError):
        notification.defer_to_fallback(
            reason=NotificationDeferredReason.NO_FALLBACK_CONFIGURED,
        )


def test_an_ignored_notification_rebuilds_without_a_body() -> None:
    stored = BankNotification(
        id=NotificationId.for_message(
            user_id=USER_ID,
            message_id=EmailMessageId("message-1"),
        ),
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        idempotency_key=IdempotencyKey.from_message(
            user_id=USER_ID,
            message_id=EmailMessageId("message-1"),
        ),
        sender=EmailAddress("stranger@example.com"),
        subject="Purchase",
        raw_content="",
        received_at=PosixTime.now(),
        status=ProcessingStatus.IGNORED,
    )

    assert stored.raw_content == ""
