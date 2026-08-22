from collections.abc import Sequence

import pytest

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxBatch,
    MailboxConnection,
    MailboxEvent,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
    HandleMailboxEventUseCase,
    SyncMailboxUseCase,
    UnsupportedMailboxProviderError,
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
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


MAILBOX = "someone@gmail.com"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"


def _inbox(*, domains: frozenset[str] = frozenset({BANK_DOMAIN})) -> UserInbox:
    return UserInbox(
        user_id=USER_ID,
        address=EmailAddress(MAILBOX),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


def _email(*, message_id: str, sender: str = BANK_SENDER) -> InboundEmail:
    return InboundEmail(
        message_id=EmailMessageId(message_id),
        recipient=EmailAddress(MAILBOX),
        sender=EmailAddress(sender),
        subject="Alertas y Notificaciones",
        raw_content="Bancolombia: Compraste COP29.259,00 en TIENDAS ARA",
        received_at=PosixTime.now(),
    )


class FakeReader:
    """Stands in for any provider adapter: Gmail, Graph, IMAP."""

    provider = MailboxProvider.GMAIL

    def __init__(self, *batches: MailboxBatch) -> None:
        self._batches = list(batches)
        self.sender_queries: list[Sequence[str]] = []
        self.cursors: list[str | None] = []

    def fetch_new(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> MailboxBatch:
        self.sender_queries.append(senders)
        self.cursors.append(connection.cursor)

        return self._batches.pop(0) if self._batches else MailboxBatch()


class FakeConnectionRepository:
    def __init__(self, *connections: MailboxConnection) -> None:
        self.saved_cursors: list[str | None] = []
        self.connections = {
            (connection.provider, connection.address): connection
            for connection in connections
        }

    def save(self, connection: MailboxConnection) -> None:
        self.saved_cursors.append(connection.cursor)
        self.connections[(connection.provider, connection.address)] = connection

    def list_active(self) -> Sequence[MailboxConnection]:
        return tuple(
            connection
            for connection in self.connections.values()
            if connection.is_active
        )

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        return self.connections.get((provider, address))

    def find_by_user(self, user_id: UserId) -> Sequence[MailboxConnection]:
        return tuple(
            connection
            for connection in self.connections.values()
            if connection.user_id == user_id
        )


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox


class InMemoryBankNotificationRepository:
    def __init__(self) -> None:
        self.saved: dict[IdempotencyKey, BankNotification] = {}

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        existing = self.saved.get(notification.idempotency_key)

        if existing is not None:
            return existing

        self.saved[notification.idempotency_key] = notification

        return None

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        return self.saved.get(idempotency_key)

    def save(self, notification: BankNotification) -> None:
        self.saved[notification.idempotency_key] = notification


class NullQueuePublisher:
    def __init__(self) -> None:
        self.enqueued: list[ParseNotificationMessage] = []

    def enqueue(self, message: ParseNotificationMessage) -> None:
        self.enqueued.append(message)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


def _connection(cursor: str | None = None) -> MailboxConnection:
    return MailboxConnection(
        user_id=USER_ID,
        address=EmailAddress(MAILBOX),
        provider=MailboxProvider.GMAIL,
        cursor=cursor,
    )


def _make(
    reader: FakeReader,
    *,
    inbox: UserInbox | None = None,
) -> tuple[SyncMailboxUseCase, FakeConnectionRepository, NullQueuePublisher]:
    connections = FakeConnectionRepository(_connection())
    queue = NullQueuePublisher()
    inboxes = InMemoryUserInboxRepository(inbox if inbox is not None else _inbox())

    use_case = SyncMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        connection_repository=connections,
        receive_use_case=ReceiveBankNotificationUseCase(
            repository=InMemoryBankNotificationRepository(),
            inbox_repository=inboxes,
            queue_publisher=queue,
            event_publisher=NullEventPublisher(),
        ),
    )

    return use_case, connections, queue


def test_fetched_email_is_ingested() -> None:
    reader = FakeReader(
        MailboxBatch(
            emails=(_email(message_id="m1"), _email(message_id="m2")),
            cursor="history-2",
        ),
    )
    use_case, connections, queue = _make(reader)

    result = use_case.execute(_connection())

    assert (result.fetched, result.accepted) == (2, 2)
    assert len(queue.enqueued) == 2
    assert connections.saved_cursors == ["history-2"]


def test_only_approved_senders_are_ever_queried() -> None:
    reader = FakeReader()
    use_case, _, _ = _make(reader)

    use_case.execute(_connection())

    # The mailbox is private correspondence. Nothing outside the user's own
    # bank senders should be fetched, let alone stored.
    assert reader.sender_queries == [(BANK_DOMAIN,)]


def test_sync_resumes_from_the_stored_cursor() -> None:
    reader = FakeReader()
    use_case, _, _ = _make(reader)

    use_case.execute(_connection(cursor="history-7"))

    assert reader.cursors == ["history-7"]


def test_a_mailbox_with_no_approved_senders_is_not_read_at_all() -> None:
    reader = FakeReader(MailboxBatch(emails=(_email(message_id="m1"),)))
    use_case, connections, queue = _make(reader, inbox=_inbox(domains=frozenset()))

    result = use_case.execute(_connection())

    assert result.fetched == 0
    assert reader.sender_queries == []
    assert queue.enqueued == []
    assert connections.saved_cursors == []


def test_redelivered_email_is_counted_as_duplicate_and_queued_once() -> None:
    same = _email(message_id="m1")
    reader = FakeReader(
        MailboxBatch(emails=(same, same), cursor="history-1"),
    )
    use_case, _, queue = _make(reader)

    result = use_case.execute(_connection())

    assert (result.accepted, result.duplicates) == (1, 1)
    assert len(queue.enqueued) == 1


def test_an_unsupported_provider_is_rejected_loudly() -> None:
    reader = FakeReader()
    use_case, _, _ = _make(reader)
    outlook = MailboxConnection(
        user_id=USER_ID,
        address=EmailAddress(MAILBOX),
        provider=MailboxProvider.OUTLOOK,
    )

    with pytest.raises(UnsupportedMailboxProviderError):
        use_case.execute(outlook)


def test_a_provider_event_syncs_the_matching_mailbox() -> None:
    reader = FakeReader(
        MailboxBatch(emails=(_email(message_id="m1"),), cursor="history-1"),
    )
    sync, connections, queue = _make(reader)
    use_case = HandleMailboxEventUseCase(
        connection_repository=connections,
        sync_use_case=sync,
    )

    result = use_case.execute(
        MailboxEvent(provider=MailboxProvider.GMAIL, address=EmailAddress(MAILBOX)),
    )

    assert result.accepted == 1
    assert len(queue.enqueued) == 1


def test_an_event_for_an_unknown_mailbox_fetches_nothing() -> None:
    reader = FakeReader(MailboxBatch(emails=(_email(message_id="m1"),)))
    sync, connections, queue = _make(reader)
    use_case = HandleMailboxEventUseCase(
        connection_repository=connections,
        sync_use_case=sync,
    )

    result = use_case.execute(
        MailboxEvent(
            provider=MailboxProvider.GMAIL,
            address=EmailAddress("stranger@gmail.com"),
        ),
    )

    # We hold no connection for that mailbox, so it is not ours to read.
    assert result.fetched == 0
    assert reader.sender_queries == []
    assert queue.enqueued == []
