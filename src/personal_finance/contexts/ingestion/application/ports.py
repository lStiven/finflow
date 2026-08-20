from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxBatch,
    MailboxConnection,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress


class BankNotificationRepository(Protocol):
    """Persistence port for `BankNotification`.

    `add_if_new` must be an atomic conditional write keyed on the
    notification's idempotency key, so at-least-once SQS/webhook redelivery
    never processes the same email twice.
    """

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        """Persist `notification` and return None. If its idempotency key is
        already taken, write nothing and return the stored record instead, so
        a retry can resume an intake that failed midway.
        """
        ...

    def save(self, notification: BankNotification) -> None:
        """Overwrite the stored notification with its current state."""
        ...


class UserInboxRepository(Protocol):
    """Resolves an inbound address to the user who owns it, along with the
    senders that user trusts.
    """

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        """Return the inbox registered for `address`, or None if no user owns
        it.
        """
        ...

    def save(self, inbox: UserInbox) -> None:
        """Create or replace the inbox registered for its address."""
        ...


class MailboxReader(Protocol):
    """Reads new bank email out of one user's mailbox.

    One implementation per email provider. The rest of the context depends on
    this protocol only, which is what keeps Gmail, Outlook and IMAP
    interchangeable.

    Strictly read-only, and deliberately so: the protocol exposes no way to
    mutate a mailbox, and implementations must authenticate with read-only
    scopes (`gmail.readonly`, `Mail.Read`). A message we processed and one we
    skipped must both stay exactly as the user left them — unread if they were
    unread — so nothing they might need can be consumed out from under them.
    """

    provider: MailboxProvider

    def fetch_new(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> MailboxBatch:
        """Return messages received after `connection.cursor`.

        `senders` restricts the query to the user's approved bank senders. It
        is not an optimisation: the mailbox is someone's private
        correspondence, and nothing outside those senders should ever be
        fetched, let alone stored.
        """
        ...


class MailboxConnectionRepository(Protocol):
    """Stores which mailboxes to sync and how far each one got."""

    def list_active(self) -> Sequence[MailboxConnection]: ...

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        """Return the connection a provider event refers to, if we hold one."""
        ...

    def save_cursor(self, connection: MailboxConnection, cursor: str | None) -> None:
        """Record where the last completed sync stopped."""
        ...


class QueuePublisher(Protocol):
    """Port for handing a notification to the asynchronous parsing queue.

    Delivery is at-least-once: the same message may be enqueued more than
    once when a retry replays a partially completed intake.
    """

    def enqueue(self, message: ParseNotificationMessage) -> None: ...
