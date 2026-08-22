from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from personal_finance.contexts.ingestion.application.backfill_handlers import (
    BackfillMailboxUseCase,
    BackfillUserMailboxesUseCase,
    start_of_current_month,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxBatch,
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
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


def _connection(
    *,
    address: str = MAILBOX,
    status: MailboxConnectionStatus = MailboxConnectionStatus.ACTIVE,
) -> MailboxConnection:
    return MailboxConnection(
        user_id=USER_ID,
        address=EmailAddress(address),
        provider=MailboxProvider.GMAIL,
        status=status,
    )


class FakeRangeReader:
    """Stands in for a provider adapter's `fetch_range`."""

    provider = MailboxProvider.GMAIL

    def __init__(self, *batches: MailboxBatch) -> None:
        self._batches = list(batches)
        self.range_queries: list[Sequence[str]] = []
        self.ranges: list[tuple[PosixTime, PosixTime]] = []

    def fetch_new(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> MailboxBatch:
        del connection, senders

        return MailboxBatch()

    def fetch_range(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
        since: PosixTime,
        until: PosixTime,
    ) -> MailboxBatch:
        del connection
        self.range_queries.append(senders)
        self.ranges.append((since, until))

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


def _receive_use_case(
    *,
    inboxes: InMemoryUserInboxRepository,
    queue: NullQueuePublisher,
) -> ReceiveBankNotificationUseCase:
    return ReceiveBankNotificationUseCase(
        repository=InMemoryBankNotificationRepository(),
        inbox_repository=inboxes,
        queue_publisher=queue,
        event_publisher=NullEventPublisher(),
    )


def test_start_of_current_month_is_midnight_utc_on_the_first() -> None:
    now = PosixTime.from_datetime(datetime(2026, 8, 22, 15, 30, tzinfo=UTC))

    start = start_of_current_month(now=now)

    assert start.to_datetime() == datetime(2026, 8, 1, tzinfo=UTC)


def test_backfilled_email_is_ingested() -> None:
    reader = FakeRangeReader(
        MailboxBatch(emails=(_email(message_id="m1"), _email(message_id="m2"))),
    )
    inboxes = InMemoryUserInboxRepository(_inbox())
    queue = NullQueuePublisher()
    use_case = BackfillMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
    )

    result = use_case.execute(
        _connection(),
        since=PosixTime.now(),
        until=PosixTime.now(),
    )

    assert (result.fetched, result.accepted) == (2, 2)
    assert len(queue.enqueued) == 2


def test_only_approved_senders_are_ever_queried() -> None:
    reader = FakeRangeReader()
    inboxes = InMemoryUserInboxRepository(_inbox())
    queue = NullQueuePublisher()
    use_case = BackfillMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
    )

    use_case.execute(_connection(), since=PosixTime.now(), until=PosixTime.now())

    assert reader.range_queries == [(BANK_DOMAIN,)]


def test_a_mailbox_with_no_approved_senders_is_not_read_at_all() -> None:
    reader = FakeRangeReader(MailboxBatch(emails=(_email(message_id="m1"),)))
    inboxes = InMemoryUserInboxRepository(_inbox(domains=frozenset()))
    queue = NullQueuePublisher()
    use_case = BackfillMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
    )

    result = use_case.execute(
        _connection(),
        since=PosixTime.now(),
        until=PosixTime.now(),
    )

    assert result.fetched == 0
    assert reader.range_queries == []
    assert queue.enqueued == []


def test_redelivered_email_is_counted_as_duplicate_and_queued_once() -> None:
    same = _email(message_id="m1")
    reader = FakeRangeReader(MailboxBatch(emails=(same, same)))
    inboxes = InMemoryUserInboxRepository(_inbox())
    queue = NullQueuePublisher()
    use_case = BackfillMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
    )

    result = use_case.execute(
        _connection(),
        since=PosixTime.now(),
        until=PosixTime.now(),
    )

    assert (result.accepted, result.duplicates) == (1, 1)
    assert len(queue.enqueued) == 1


def test_an_unsupported_provider_is_rejected_loudly() -> None:
    reader = FakeRangeReader()
    inboxes = InMemoryUserInboxRepository(_inbox())
    queue = NullQueuePublisher()
    use_case = BackfillMailboxUseCase(
        readers={MailboxProvider.GMAIL: reader},
        inbox_repository=inboxes,
        receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
    )
    outlook = MailboxConnection(
        user_id=USER_ID,
        address=EmailAddress(MAILBOX),
        provider=MailboxProvider.OUTLOOK,
    )

    with pytest.raises(UnsupportedMailboxProviderError):
        use_case.execute(outlook, since=PosixTime.now(), until=PosixTime.now())


def _user_backfill(
    *connections: MailboxConnection,
    inbox: UserInbox | None = None,
    batches: tuple[MailboxBatch, ...] = (),
) -> tuple[BackfillUserMailboxesUseCase, FakeConnectionRepository, FakeRangeReader]:
    connection_repository = FakeConnectionRepository(*connections)
    reader = FakeRangeReader(*batches)
    inboxes = InMemoryUserInboxRepository(inbox if inbox is not None else _inbox())
    queue = NullQueuePublisher()

    use_case = BackfillUserMailboxesUseCase(
        connection_repository=connection_repository,
        backfill_use_case=BackfillMailboxUseCase(
            readers={MailboxProvider.GMAIL: reader},
            inbox_repository=inboxes,
            receive_use_case=_receive_use_case(inboxes=inboxes, queue=queue),
        ),
    )

    return use_case, connection_repository, reader


def test_backfills_every_active_mailbox_for_the_user() -> None:
    use_case, _, reader = _user_backfill(
        _connection(),
        batches=(MailboxBatch(emails=(_email(message_id="m1"),)),),
    )

    result = use_case.execute(USER_ID)

    assert (result.mailboxes, result.fetched, result.accepted) == (1, 1, 1)
    assert len(reader.range_queries) == 1


def test_a_connection_needing_reauth_is_skipped_and_counted() -> None:
    use_case, _, reader = _user_backfill(
        _connection(status=MailboxConnectionStatus.NEEDS_REAUTH),
    )

    result = use_case.execute(USER_ID)

    assert result.mailboxes == 0
    assert result.needs_reauth == 1
    assert reader.range_queries == []


def test_a_revoked_connection_is_skipped() -> None:
    use_case, _, reader = _user_backfill(
        _connection(status=MailboxConnectionStatus.REVOKED),
    )

    result = use_case.execute(USER_ID)

    assert result.mailboxes == 0
    assert reader.range_queries == []


def test_a_backfill_never_touches_the_stored_cursor() -> None:
    use_case, connections, _ = _user_backfill(
        _connection(),
        batches=(MailboxBatch(emails=(_email(message_id="m1"),)),),
    )

    use_case.execute(USER_ID)

    assert connections.saved_cursors == []


def test_since_is_the_first_of_the_current_month() -> None:
    use_case, _, reader = _user_backfill(_connection())

    result = use_case.execute(USER_ID)

    now = PosixTime.now().to_datetime()
    assert result.since.to_datetime() == now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    assert reader.ranges[0][0] == result.since
