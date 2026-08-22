from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.identity.application.ports import (
    ConnectedMailbox,
    InboxRegistrar,
    InboxRegistration,
    MailboxBackfillSummary,
    MailboxConnector,
    MailboxRefreshSummary,
)
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConnectMailboxCommand:
    address: str
    provider: str
    # Who this mailbox is allowed to be read for. Optional, because a user may
    # connect first and choose senders after — but until there is at least
    # one, connecting reads nothing at all.
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DisconnectMailboxCommand:
    address: str
    provider: str


class ConnectMailboxUseCase:
    """Attaches a mailbox to the authenticated user, with the senders we may
    read it for.

    One action on purpose: a connection without a sender filter reads nothing,
    so splitting them into two calls only creates a state where the user
    believes they are set up and no mail arrives.

    Whose mailbox it becomes comes from the verified token, never from the
    request body: naming someone else's id must not be a way to point their
    mail at your own account.
    """

    def __init__(
        self,
        *,
        connector: MailboxConnector,
        inbox_registrar: InboxRegistrar,
    ) -> None:
        self._connector = connector
        self._inbox_registrar = inbox_registrar

    def execute(self, *, user_id: UserId, command: ConnectMailboxCommand) -> None:
        # Senders first: the moment the connection goes active, the filter
        # that limits what we may read already exists.
        self._inbox_registrar.register(
            user_id=user_id,
            inboxes=(
                InboxRegistration(
                    address=command.address,
                    allowed_domains=command.allowed_domains,
                    allowed_addresses=command.allowed_addresses,
                ),
            ),
        )
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


class RefreshMailboxesUseCase:
    """Catches this user's mailboxes up, on demand.

    The point of a web application: someone opening it is the one moment
    their data most needs to be current, and it costs a cursor-based read
    that finds nothing when nothing changed. It also renews subscriptions on
    the way past, which means a user simply using the application keeps their
    own mailboxes alive even if every scheduled job is dead.
    """

    def __init__(self, *, connector: MailboxConnector) -> None:
        self._connector = connector

    def execute(self, *, user_id: UserId) -> MailboxRefreshSummary:
        return self._connector.refresh_for_user(user_id)


class BackfillMailboxesUseCase:
    """Reads this user's mailboxes from the first of the current month, once.

    Meant to be offered right after someone connects a mailbox: without it,
    signing up on any day but the first of the month means the ordinary sync
    only ever sees mail from that moment forward, and this month's earlier
    history is simply missing. Safe to call more than once — anything already
    ingested comes back as a duplicate, never twice.
    """

    def __init__(self, *, connector: MailboxConnector) -> None:
        self._connector = connector

    def execute(self, *, user_id: UserId) -> MailboxBackfillSummary:
        return self._connector.backfill_current_month_for_user(user_id)
