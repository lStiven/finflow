from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from typing import Protocol

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
    EmailMessageId,
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboundEmail:
    """One message read off the ingest mailbox, normalized.

    `recipient` is the exact `+alias` address it was delivered to — that is
    what `ReceiveBankNotificationUseCase` uses to attribute it to a user.
    """

    recipient: EmailAddress
    sender: EmailAddress
    message_id: EmailMessageId
    subject: str
    raw_content: str
    received_at: PosixTime


class IngestMailboxReader(Protocol):
    """Reads new mail out of the one mailbox this deployment owns.

    Unlike a user's personal mailbox, this one exists for exactly this
    purpose, so there is no per-user permission to model here: one
    implementation, one account. Strictly read-only in the same spirit as
    everything else in this context — it never deletes a message, only marks
    each one handled once `ack` is called for it.
    """

    def fetch_new(self) -> Sequence[InboundEmail]:
        """Every message not yet acknowledged."""
        ...

    def ack(self, emails: Sequence[InboundEmail]) -> None:
        """Mark these messages handled, so they are not fetched again.

        Takes a batch rather than one message at a time so an implementation
        can settle the whole poll in one round trip instead of one per
        message. Called only for messages already durably recorded —
        acknowledging first and recording second would let a crash in
        between lose one for good.
        """
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
