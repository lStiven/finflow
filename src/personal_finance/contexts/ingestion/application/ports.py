from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxBatch,
    MailboxConnection,
    MailboxEventDelivery,
    MailboxProvider,
    Subscription,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    IdempotencyKey,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


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

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        """Load the stored notification, or None if it is gone."""
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

    def find_by_user(self, user_id: UserId) -> Sequence[UserInbox]:
        """Return every inbox `user_id` owns, empty when they have none.

        This is a secondary access path: the hot path resolves one address at
        a time, so an implementation must not scan the whole table to answer
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

    def fetch_range(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
        since: PosixTime,
        until: PosixTime,
    ) -> MailboxBatch:
        """Return messages received between `since` and `until`, ignoring the
        cursor entirely.

        A one-time bounded catch-up — someone connecting mid-month asking to
        also see what this month already holds — not part of the ongoing sync.
        `senders` means exactly what it means in `fetch_new`: nothing outside
        the approved list is ever fetched.
        """
        ...


class MailboxSubscriber(Protocol):
    """Asks a provider to keep telling us when a mailbox changes.

    Every provider expires its subscription — Gmail caps a watch at 7 days,
    Graph at about 3 — so this is not a one-time setup call. `subscribe` is
    written to be safe to call again at any moment: providers treat a repeat
    as a renewal, and renewing early is the whole strategy.
    """

    provider: MailboxProvider
    # How this provider delivers. A polling provider is never renewed, because
    # there is nothing on the other side holding a subscription open.
    delivery: MailboxEventDelivery
    # How long this provider's subscriptions last when freshly created. Comes
    # from the provider rather than being inferred from an existing expiry:
    # time *remaining* is always more than half of itself, so a policy built
    # on it would postpone renewal forever and never fire.
    lifetime_seconds: int

    def subscribe(self, connection: MailboxConnection) -> Subscription:
        """Start or renew notifications. Returns the new expiry, and the
        position to read from when the connection has none yet.

        Raises `MailboxAccessRevokedError` when the grant is gone, and
        `MailboxTemporarilyUnavailableError` when it is worth trying again.
        """
        ...

    def unsubscribe(self, connection: MailboxConnection) -> None:
        """Ask the provider to stop. Best-effort: a subscription nobody
        renews expires on its own anyway.
        """
        ...


class MailboxConnectionRepository(Protocol):
    """Stores which mailboxes to sync and how far each one got."""

    def save(self, connection: MailboxConnection) -> None:
        """Create or replace a connection, keyed by provider and address."""
        ...

    def list_active(self) -> Sequence[MailboxConnection]: ...

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        """Return the connection a provider event refers to, if we hold one.

        Returns revoked connections too: deciding what to do with one is the
        caller's business, and silently hiding it here would make a revoked
        mailbox indistinguishable from one we never knew about.
        """
        ...

    def find_by_user(self, user_id: UserId) -> Sequence[MailboxConnection]:
        """Return every mailbox this user connected, revoked ones included."""
        ...


class TransactionExtractor(Protocol):
    """Plan B: reads an alert no deterministic template understood.

    Only ever consulted after every template has missed, which is what keeps
    the cheap, repeatable, auditable path in charge of the common case. An
    implementation returns None when it cannot read the message — that is a
    normal answer, and far better than a plausible invention, because nothing
    downstream can tell a guessed amount from a real one.

    Whatever produces the result is untrusted: the returned value object is
    the contract, not the text some model wrote.
    """

    def extract(
        self,
        *,
        sender: EmailAddress,
        subject: str,
        body: str,
        received_at: PosixTime,
    ) -> ExtractedTransaction | None:
        """Return what the alert says, or None if it cannot be read.

        `received_at` is context, not data: an alert that writes a date with
        no year is resolved against when it arrived.

        Raises `LLMTemporarilyUnavailableError` when the model is rate limited
        or down, so the caller can leave the email for another attempt instead
        of recording a failure that was never the email's fault.
        """
        ...


class QueuePublisher(Protocol):
    """Port for handing a notification to the asynchronous parsing queue.

    Delivery is at-least-once: the same message may be enqueued more than
    once when a retry replays a partially completed intake.
    """

    def enqueue(self, message: ParseNotificationMessage) -> None: ...
