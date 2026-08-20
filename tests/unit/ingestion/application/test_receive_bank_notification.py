from collections.abc import Sequence

import pytest

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    ProcessingStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime


def _detached(notification: BankNotification) -> BankNotification:
    """Copy of `notification` as a real repository would hand it back: same
    state, no pending events.
    """
    return BankNotification(
        id=notification.id,
        message_id=notification.message_id,
        idempotency_key=notification.idempotency_key,
        sender=notification.sender,
        subject=notification.subject,
        raw_content=notification.raw_content,
        received_at=notification.received_at,
        status=notification.status,
    )


class InMemoryBankNotificationRepository:
    def __init__(self) -> None:
        self.saved: dict[IdempotencyKey, BankNotification] = {}

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        existing = self.saved.get(notification.idempotency_key)

        if existing is not None:
            return existing

        self.saved[notification.idempotency_key] = _detached(notification)

        return None

    def save(self, notification: BankNotification) -> None:
        self.saved[notification.idempotency_key] = _detached(notification)


class RecordingQueuePublisher:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.enqueued: list[ParseNotificationMessage] = []
        self._fail_once = fail_once

    def enqueue(self, message: ParseNotificationMessage) -> None:
        if self._fail_once:
            self._fail_once = False

            raise RuntimeError("queue unavailable")

        self.enqueued.append(message)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _make_use_case(
    *,
    repository: InMemoryBankNotificationRepository | None = None,
    queue_publisher: RecordingQueuePublisher | None = None,
    event_publisher: RecordingEventPublisher | None = None,
    sender_policy: AuthorizedSenderPolicy | None = None,
) -> tuple[
    ReceiveBankNotificationUseCase,
    InMemoryBankNotificationRepository,
    RecordingQueuePublisher,
    RecordingEventPublisher,
]:
    repository = repository or InMemoryBankNotificationRepository()
    queue_publisher = queue_publisher or RecordingQueuePublisher()
    event_publisher = event_publisher or RecordingEventPublisher()
    sender_policy = sender_policy or AuthorizedSenderPolicy(
        allowed_domains=frozenset({"bank.com"}),
    )

    use_case = ReceiveBankNotificationUseCase(
        repository=repository,
        sender_policy=sender_policy,
        queue_publisher=queue_publisher,
        event_publisher=event_publisher,
    )

    return use_case, repository, queue_publisher, event_publisher


def _command(
    *,
    message_id: str = "message-1",
    sender: str = "alerts@bank.com",
) -> ReceiveBankNotificationCommand:
    return ReceiveBankNotificationCommand(
        message_id=EmailMessageId(message_id),
        sender=EmailAddress(sender),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )


def _published_types(event_publisher: RecordingEventPublisher) -> list[str]:
    return [type(event).__name__ for event in event_publisher.published]


def test_authorized_sender_is_persisted_queued_and_published() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    result = use_case.execute(_command())

    assert result.status is ProcessingStatus.QUEUED
    assert result.is_duplicate is False
    assert len(repository.saved) == 1
    assert len(queue_publisher.enqueued) == 1
    assert queue_publisher.enqueued[0].notification_id == result.notification_id
    assert _published_types(event_publisher) == [
        "BankNotificationReceived",
        "BankNotificationQueued",
    ]


def test_unauthorized_sender_is_ignored_persisted_and_never_queued() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    result = use_case.execute(_command(sender="someone@gmail.com"))

    assert result.status is ProcessingStatus.IGNORED
    assert len(repository.saved) == 1
    assert queue_publisher.enqueued == []
    # `receive` records BankNotificationReceived, and `ignore` additionally
    # records BankNotificationIgnored — both are kept for an accurate audit
    # trail of what happened to the notification.
    assert _published_types(event_publisher) == [
        "BankNotificationReceived",
        "BankNotificationIgnored",
    ]


def test_duplicate_message_is_not_saved_queued_or_republished_twice() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    first = use_case.execute(_command())
    second = use_case.execute(_command())

    assert first.is_duplicate is False
    assert second.is_duplicate is True
    assert second.notification_id == first.notification_id
    assert len(repository.saved) == 1
    assert len(queue_publisher.enqueued) == 1
    assert len(event_publisher.published) == 2


def test_retry_queues_a_notification_left_unqueued_by_a_failed_attempt() -> None:
    repository = InMemoryBankNotificationRepository()
    queue_publisher = RecordingQueuePublisher(fail_once=True)
    use_case, _, _, event_publisher = _make_use_case(
        repository=repository,
        queue_publisher=queue_publisher,
    )

    with pytest.raises(RuntimeError):
        use_case.execute(_command())

    stored = next(iter(repository.saved.values()))
    assert stored.status is ProcessingStatus.RECEIVED
    assert queue_publisher.enqueued == []

    result = use_case.execute(_command())

    assert result.is_duplicate is True
    assert result.status is ProcessingStatus.QUEUED
    assert len(queue_publisher.enqueued) == 1
    # The first attempt died before publishing, so only the resumed attempt's
    # queued event reaches the publisher.
    assert _published_types(event_publisher) == ["BankNotificationQueued"]
