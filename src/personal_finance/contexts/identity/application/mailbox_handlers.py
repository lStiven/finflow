from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.identity.application.ports import (
    ConnectedMailbox,
    MailboxConnector,
)
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConnectMailboxCommand:
    address: str
    provider: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DisconnectMailboxCommand:
    address: str
    provider: str


class ConnectMailboxUseCase:
    """Attaches a mailbox to the authenticated user.

    Whose mailbox it becomes comes from the verified token, never from the
    request body: naming someone else's id must not be a way to point their
    mail at your own account.
    """

    def __init__(self, *, connector: MailboxConnector) -> None:
        self._connector = connector

    def execute(self, *, user_id: UserId, command: ConnectMailboxCommand) -> None:
        self._connector.connect(
            user_id=user_id,
            address=command.address,
            provider=command.provider,
        )


class DisconnectMailboxUseCase:
    """Stops reading a mailbox the user no longer wants us in."""

    def __init__(self, *, connector: MailboxConnector) -> None:
        self._connector = connector

    def execute(self, *, user_id: UserId, command: DisconnectMailboxCommand) -> None:
        self._connector.disconnect(
            user_id=user_id,
            address=command.address,
            provider=command.provider,
        )


class ListMailboxesUseCase:
    """Reports the mailboxes the authenticated user connected."""

    def __init__(self, *, connector: MailboxConnector) -> None:
        self._connector = connector

    def execute(self, *, user_id: UserId) -> Sequence[ConnectedMailbox]:
        return self._connector.list_for_user(user_id)
