from collections.abc import Sequence

import pytest

from personal_finance.contexts.ingestion.application.connection_handlers import (
    ConnectMailboxCommand,
    ConnectMailboxUseCase,
    DisconnectMailboxCommand,
    DisconnectMailboxUseCase,
    ListMailboxConnectionsUseCase,
    MailboxAlreadyConnectedError,
    MailboxNotConnectedError,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
ADDRESS = "ana@gmail.test"
PROVIDER = MailboxProvider.SIMULATED


class InMemoryConnectionRepository:
    def __init__(self, *connections: MailboxConnection) -> None:
        self.connections = {
            (connection.provider, connection.address): connection
            for connection in connections
        }

    def save(self, connection: MailboxConnection) -> None:
        self.connections[(connection.provider, connection.address)] = connection

    def list_active(self) -> Sequence[MailboxConnection]:
        return tuple(c for c in self.connections.values() if c.is_active)

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        return self.connections.get((provider, address))

    def find_by_user(self, user_id: UserId) -> Sequence[MailboxConnection]:
        return tuple(c for c in self.connections.values() if c.user_id == user_id)

    def save_cursor(self, connection: MailboxConnection, cursor: str | None) -> None:
        raise NotImplementedError


def _connection(
    *,
    user_id: UserId = USER_ID,
    address: str = ADDRESS,
    cursor: str | None = None,
    status: MailboxConnectionStatus = MailboxConnectionStatus.ACTIVE,
) -> MailboxConnection:
    return MailboxConnection(
        user_id=user_id,
        address=EmailAddress(address),
        provider=PROVIDER,
        cursor=cursor,
        status=status,
    )


def _connect(
    repository: InMemoryConnectionRepository,
    *,
    user_id: UserId = USER_ID,
    address: str = ADDRESS,
) -> MailboxConnection:
    return ConnectMailboxUseCase(connection_repository=repository).execute(
        ConnectMailboxCommand(
            user_id=user_id,
            address=EmailAddress(address),
            provider=PROVIDER,
        ),
    )


def test_connecting_a_mailbox_records_it_as_active() -> None:
    repository = InMemoryConnectionRepository()

    connection = _connect(repository)

    assert connection.is_active
    assert connection.user_id == USER_ID
    assert repository.find(provider=PROVIDER, address=EmailAddress(ADDRESS)) is not None


def test_a_new_connection_starts_with_no_position() -> None:
    repository = InMemoryConnectionRepository()

    assert _connect(repository).cursor is None


def test_reconnecting_keeps_the_position_so_old_mail_is_not_reread() -> None:
    repository = InMemoryConnectionRepository(_connection(cursor="42"))

    # Resetting the cursor would reach back into a private mailbox for mail we
    # already handled.
    assert _connect(repository).cursor == "42"


def test_reconnecting_a_revoked_mailbox_makes_it_active_again() -> None:
    repository = InMemoryConnectionRepository(
        _connection(cursor="42", status=MailboxConnectionStatus.REVOKED),
    )

    connection = _connect(repository)

    assert connection.is_active
    assert connection.cursor == "42"


def test_a_mailbox_owned_by_someone_else_cannot_be_taken_over() -> None:
    repository = InMemoryConnectionRepository(_connection(user_id=OTHER_USER_ID))

    # Otherwise one person's mail would start feeding another's finances.
    with pytest.raises(MailboxAlreadyConnectedError):
        _connect(repository)


def test_disconnecting_revokes_without_forgetting_the_position() -> None:
    repository = InMemoryConnectionRepository(_connection(cursor="42"))
    use_case = DisconnectMailboxUseCase(connection_repository=repository)

    revoked = use_case.execute(
        DisconnectMailboxCommand(
            user_id=USER_ID,
            address=EmailAddress(ADDRESS),
            provider=PROVIDER,
        ),
    )

    assert revoked.status is MailboxConnectionStatus.REVOKED
    assert revoked.cursor == "42"


def test_disconnecting_a_mailbox_you_do_not_own_is_refused() -> None:
    repository = InMemoryConnectionRepository(_connection(user_id=OTHER_USER_ID))
    use_case = DisconnectMailboxUseCase(connection_repository=repository)

    # Same error as "never connected", so nobody can discover which mailboxes
    # somebody else attached.
    with pytest.raises(MailboxNotConnectedError):
        use_case.execute(
            DisconnectMailboxCommand(
                user_id=USER_ID,
                address=EmailAddress(ADDRESS),
                provider=PROVIDER,
            ),
        )


def test_disconnecting_an_unknown_mailbox_is_refused() -> None:
    use_case = DisconnectMailboxUseCase(
        connection_repository=InMemoryConnectionRepository(),
    )

    with pytest.raises(MailboxNotConnectedError):
        use_case.execute(
            DisconnectMailboxCommand(
                user_id=USER_ID,
                address=EmailAddress(ADDRESS),
                provider=PROVIDER,
            ),
        )


def test_listing_returns_only_this_users_mailboxes_sorted() -> None:
    repository = InMemoryConnectionRepository(
        _connection(address="b@gmail.test"),
        _connection(address="a@gmail.test"),
        _connection(address="theirs@gmail.test", user_id=OTHER_USER_ID),
    )
    use_case = ListMailboxConnectionsUseCase(connection_repository=repository)

    connections = use_case.execute(USER_ID)

    assert [c.address.value for c in connections] == ["a@gmail.test", "b@gmail.test"]


def test_listing_includes_revoked_mailboxes() -> None:
    repository = InMemoryConnectionRepository(
        _connection(status=MailboxConnectionStatus.REVOKED),
    )
    use_case = ListMailboxConnectionsUseCase(connection_repository=repository)

    # The user needs to see a mailbox they turned off, or reconnecting it
    # would mean guessing it is still there.
    assert len(use_case.execute(USER_ID)) == 1
