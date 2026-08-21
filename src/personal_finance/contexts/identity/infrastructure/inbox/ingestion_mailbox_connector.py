from __future__ import annotations

from collections.abc import Sequence

from personal_finance.contexts.identity.application.ports import ConnectedMailbox
from personal_finance.contexts.ingestion.application.connection_handlers import (
    ConnectMailboxCommand,
    ConnectMailboxUseCase,
    DisconnectMailboxCommand,
    DisconnectMailboxUseCase,
    ListMailboxConnectionsUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import MailboxProvider
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


class UnknownMailboxProviderError(ValueError):
    """Raised when a caller names a provider that does not exist."""


def _provider(value: str) -> MailboxProvider:
    try:
        return MailboxProvider(value)
    except ValueError as error:
        supported = ", ".join(sorted(item.value for item in MailboxProvider))

        raise UnknownMailboxProviderError(
            f"Unknown mailbox provider {value!r}. Supported: {supported}",
        ) from error


class IngestionMailboxConnector:
    """Adapts identity's `MailboxConnector` port to ingestion's use cases.

    Like `IngestionInboxRegistrar`, this is the only file in identity allowed
    to know ingestion exists. Ingestion's connection use cases are its
    published integration surface; its `MailboxConnection` stops here and
    identity's `ConnectedMailbox` continues outwards.
    """

    def __init__(
        self,
        *,
        connect_use_case: ConnectMailboxUseCase,
        disconnect_use_case: DisconnectMailboxUseCase,
        list_use_case: ListMailboxConnectionsUseCase,
    ) -> None:
        self._connect_use_case = connect_use_case
        self._disconnect_use_case = disconnect_use_case
        self._list_use_case = list_use_case

    def connect(self, *, user_id: UserId, address: str, provider: str) -> None:
        self._connect_use_case.execute(
            ConnectMailboxCommand(
                user_id=user_id,
                address=EmailAddress(address),
                provider=_provider(provider),
            ),
        )

    def disconnect(self, *, user_id: UserId, address: str, provider: str) -> None:
        self._disconnect_use_case.execute(
            DisconnectMailboxCommand(
                user_id=user_id,
                address=EmailAddress(address),
                provider=_provider(provider),
            ),
        )

    def list_for_user(self, user_id: UserId) -> Sequence[ConnectedMailbox]:
        return [
            ConnectedMailbox(
                address=connection.address.value,
                provider=connection.provider.value,
                status=connection.status.value,
            )
            for connection in self._list_use_case.execute(user_id)
        ]
