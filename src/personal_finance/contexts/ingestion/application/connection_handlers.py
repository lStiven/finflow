from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.ports import (
    MailboxConnectionRepository,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


class MailboxNotConnectedError(Exception):
    """Raised when an operation names a mailbox this user has not connected."""


class MailboxAlreadyConnectedError(Exception):
    """Raised when a mailbox is already connected by a different user."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConnectMailboxCommand:
    user_id: UserId
    address: EmailAddress
    provider: MailboxProvider


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DisconnectMailboxCommand:
    user_id: UserId
    address: EmailAddress
    provider: MailboxProvider


class ConnectMailboxUseCase:
    """Records that a user authorized us to read one of their mailboxes.

    This is the seam the real integration plugs into: an OAuth callback ends
    by calling exactly this, once the provider has handed back a token. The
    token itself never arrives here — it belongs in a secret store the
    provider adapter reads, so it cannot travel through the application layer
    or end up in a log.

    Reconnecting a mailbox keeps its cursor. Resetting it would re-read
    everything the user already has, which is only safe because ingestion is
    idempotent — but it would still mean reaching into a private mailbox for
    mail we already handled.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
    ) -> None:
        self._connection_repository = connection_repository

    def execute(self, command: ConnectMailboxCommand) -> MailboxConnection:
        existing = self._connection_repository.find(
            provider=command.provider,
            address=command.address,
        )

        if existing is not None and existing.user_id != command.user_id:
            # The address is already someone else's. Reassigning it would
            # start feeding one person's mail into another's finances.
            raise MailboxAlreadyConnectedError(
                f"{command.address.value} is connected by another user",
            )

        connection = MailboxConnection(
            user_id=command.user_id,
            address=command.address,
            provider=command.provider,
            cursor=existing.cursor if existing else None,
            status=MailboxConnectionStatus.ACTIVE,
        )
        self._connection_repository.save(connection)

        return connection


class DisconnectMailboxUseCase:
    """Stops reading a mailbox, without forgetting it.

    The record is kept as `revoked` rather than deleted: the cursor stays put,
    so reconnecting later resumes instead of re-reading a whole mailbox, and a
    provider that keeps pushing notifications is answered with a deliberate
    "no" instead of an "I have never heard of this".
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
    ) -> None:
        self._connection_repository = connection_repository

    def execute(self, command: DisconnectMailboxCommand) -> MailboxConnection:
        existing = self._connection_repository.find(
            provider=command.provider,
            address=command.address,
        )

        if existing is None or existing.user_id != command.user_id:
            # Same answer either way: a caller must not be able to discover
            # which mailboxes somebody else connected.
            raise MailboxNotConnectedError(
                f"{command.address.value} is not connected",
            )

        revoked = dataclasses.replace(
            existing,
            status=MailboxConnectionStatus.REVOKED,
        )
        self._connection_repository.save(revoked)

        return revoked


class ListMailboxConnectionsUseCase:
    """Lists the mailboxes a user connected.

    Published for other contexts to call, the same way `ListUserInboxesUseCase`
    is: identity exposes it to an authenticated user through its own adapter.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
    ) -> None:
        self._connection_repository = connection_repository

    def execute(self, user_id: UserId) -> Sequence[MailboxConnection]:
        connections = self._connection_repository.find_by_user(user_id)

        return sorted(connections, key=lambda item: item.address.value)
