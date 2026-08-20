from collections.abc import Sequence

import pytest

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
    ReceiveOutcome,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    ProcessingStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


INBOX_ADDRESS = "u-7f3a9c@inbound.finflow.test"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
OTHER_INBOX_ADDRESS = "u-000002@inbound.finflow.test"


def _inbox(
    *,
    user_id: UserId = USER_ID,
    address: str = INBOX_ADDRESS,
    domains: frozenset[str] = frozenset({"bank.com"}),
) -> UserInbox:
    return UserInbox(
        user_id=user_id,
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


def _detached(notification: BankNotification) -> BankNotification:
    """Copy of `notification` as a real repository would hand it back: same
    state, no pending events.
    """
    return BankNotification(
        id=notification.id,
        user_id=notification.user_id,
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


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox


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
    inbox_repository: InMemoryUserInboxRepository | None = None,
    queue_publisher: RecordingQueuePublisher | None = None,
    event_publisher: RecordingEventPublisher | None = None,
) -> tuple[
    ReceiveBankNotificationUseCase,
    InMemoryBankNotificationRepository,
    RecordingQueuePublisher,
    RecordingEventPublisher,
]:
    repository = repository or InMemoryBankNotificationRepository()
    inbox_repository = inbox_repository or InMemoryUserInboxRepository(_inbox())
    queue_publisher = queue_publisher or RecordingQueuePublisher()
    event_publisher = event_publisher or RecordingEventPublisher()

    use_case = ReceiveBankNotificationUseCase(
        repository=repository,
        inbox_repository=inbox_repository,
        queue_publisher=queue_publisher,
        event_publisher=event_publisher,
    )

    return use_case, repository, queue_publisher, event_publisher


def _command(
    *,
    recipient: str = INBOX_ADDRESS,
    message_id: str = "message-1",
    sender: str = "alerts@bank.com",
) -> ReceiveBankNotificationCommand:
    return ReceiveBankNotificationCommand(
        recipient=EmailAddress(recipient),
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

    assert result.outcome is ReceiveOutcome.ACCEPTED
    assert result.status is ProcessingStatus.QUEUED
    assert len(repository.saved) == 1
    assert len(queue_publisher.enqueued) == 1
    assert queue_publisher.enqueued[0].user_id == USER_ID
    assert _published_types(event_publisher) == [
        "BankNotificationReceived",
        "BankNotificationQueued",
    ]


def test_unknown_recipient_is_not_stored_at_all() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    result = use_case.execute(_command(recipient="nobody@inbound.finflow.test"))

    assert result.outcome is ReceiveOutcome.UNKNOWN_RECIPIENT
    assert result.notification_id is None
    assert result.status is None
    # The webhook is reachable by anyone: unattributable email must not create
    # an unbounded write.
    assert repository.saved == {}
    assert queue_publisher.enqueued == []
    assert event_publisher.published == []


def test_sender_outside_the_users_list_is_ignored_but_still_stored() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    result = use_case.execute(_command(sender="phisher@evil.com"))

    assert result.outcome is ReceiveOutcome.ACCEPTED
    assert result.status is ProcessingStatus.IGNORED
    assert len(repository.saved) == 1
    assert queue_publisher.enqueued == []
    assert _published_types(event_publisher) == [
        "BankNotificationReceived",
        "BankNotificationIgnored",
    ]


def test_each_user_has_their_own_allow_list() -> None:
    # Same sender, two users: approved for one, not for the other.
    inboxes = InMemoryUserInboxRepository(
        _inbox(),
        _inbox(
            user_id=OTHER_USER_ID,
            address=OTHER_INBOX_ADDRESS,
            domains=frozenset({"other-bank.com"}),
        ),
    )
    use_case, _, queue_publisher, _ = _make_use_case(inbox_repository=inboxes)

    approved = use_case.execute(_command())
    rejected = use_case.execute(_command(recipient=OTHER_INBOX_ADDRESS))

    assert approved.status is ProcessingStatus.QUEUED
    assert rejected.status is ProcessingStatus.IGNORED
    assert len(queue_publisher.enqueued) == 1


def test_a_user_with_no_approved_senders_authorizes_nothing() -> None:
    inboxes = InMemoryUserInboxRepository(_inbox(domains=frozenset()))
    use_case, repository, queue_publisher, _ = _make_use_case(
        inbox_repository=inboxes,
    )

    result = use_case.execute(_command())

    # An empty list is a normal state for a new user, and it fails closed.
    assert result.status is ProcessingStatus.IGNORED
    assert len(repository.saved) == 1
    assert queue_publisher.enqueued == []


def test_the_same_email_forwarded_by_two_users_is_kept_separate() -> None:
    inboxes = InMemoryUserInboxRepository(
        _inbox(),
        _inbox(user_id=OTHER_USER_ID, address=OTHER_INBOX_ADDRESS),
    )
    use_case, repository, queue_publisher, _ = _make_use_case(
        inbox_repository=inboxes,
    )

    first = use_case.execute(_command())
    second = use_case.execute(_command(recipient=OTHER_INBOX_ADDRESS))

    # A forwarded bank email keeps its original Message-ID. Without scoping the
    # idempotency key by user, the second user would lose their notification.
    assert second.outcome is ReceiveOutcome.ACCEPTED
    assert second.notification_id != first.notification_id
    assert len(repository.saved) == 2
    assert len(queue_publisher.enqueued) == 2


def test_duplicate_message_is_not_saved_queued_or_republished_twice() -> None:
    use_case, repository, queue_publisher, event_publisher = _make_use_case()

    first = use_case.execute(_command())
    second = use_case.execute(_command())

    assert first.outcome is ReceiveOutcome.ACCEPTED
    assert second.outcome is ReceiveOutcome.DUPLICATE
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

    assert result.outcome is ReceiveOutcome.DUPLICATE
    assert result.status is ProcessingStatus.QUEUED
    assert len(queue_publisher.enqueued) == 1
    # The first attempt died before publishing, so only the resumed attempt's
    # queued event reaches the publisher.
    assert _published_types(event_publisher) == ["BankNotificationQueued"]


def test_registering_an_inbox_creates_a_user() -> None:
    repository = InMemoryUserInboxRepository()
    use_case = RegisterUserInboxUseCase(inbox_repository=repository)

    inbox = use_case.execute(
        RegisterUserInboxCommand(
            address=EmailAddress(INBOX_ADDRESS),
            allowed_domains=frozenset({"bank.com"}),
        ),
    )

    assert inbox.address == EmailAddress(INBOX_ADDRESS)
    assert repository.inboxes[inbox.address].sender_policy.is_authorized(
        EmailAddress("alerts@bank.com"),
    )


def test_re_registering_an_address_keeps_its_owner() -> None:
    repository = InMemoryUserInboxRepository(_inbox())
    use_case = RegisterUserInboxUseCase(inbox_repository=repository)

    updated = use_case.execute(
        RegisterUserInboxCommand(
            address=EmailAddress(INBOX_ADDRESS),
            allowed_domains=frozenset({"other-bank.com"}),
        ),
    )

    # Reassigning the address to a new user would hand one person's incoming
    # email to another.
    assert updated.user_id == USER_ID
