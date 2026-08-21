from __future__ import annotations

import dataclasses
import enum

from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


class MailboxProvider(enum.Enum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    IMAP = "imap"
    # A mailbox the project itself hosts, so the whole notify-fetch-ingest
    # path can be exercised without a real account or real credentials. It is
    # a provider like any other: it goes through the same port, and nothing
    # downstream can tell it apart from Gmail.
    SIMULATED = "simulated"


class MailboxConnectionStatus(enum.Enum):
    """Explicit string values: this is persisted."""

    ACTIVE = "active"
    # The user disconnected the mailbox, or the provider stopped accepting our
    # credentials. Either way we must not read it again.
    REVOKED = "revoked"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboundEmail:
    """One email, in the shape the context understands.

    Every provider adapter produces this and nothing else, so the rest of the
    system never learns whether the message arrived from Gmail's API, a Graph
    delta query, an IMAP fetch, or a webhook push.
    """

    message_id: EmailMessageId
    recipient: EmailAddress
    sender: EmailAddress
    subject: str
    raw_content: str
    received_at: PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MailboxConnection:
    """A mailbox the user authorized us to read.

    Deliberately carries no credentials: tokens are resolved by the adapter
    from a secure store keyed by `user_id`, so they never travel through the
    application layer, get logged, or land in an exception message.
    """

    user_id: UserId
    address: EmailAddress
    provider: MailboxProvider
    # Opaque provider position: Gmail's historyId, Graph's deltaLink, an IMAP
    # UIDVALIDITY/UID pair. Only the adapter that wrote it can read it.
    cursor: str | None = None
    status: MailboxConnectionStatus = MailboxConnectionStatus.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.status is MailboxConnectionStatus.ACTIVE


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MailboxBatch:
    """What one sync pass found, plus where to resume next time."""

    emails: tuple[InboundEmail, ...] = ()
    cursor: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MailboxEvent:
    """A provider's "something arrived" signal.

    Providers push a pointer, never the message: Gmail sends a Pub/Sub
    notification carrying a history id, Graph sends a change notification
    carrying a resource id. The content is fetched afterwards, filtered by the
    user's approved senders — which is why the signal alone is not enough to
    decide anything.
    """

    provider: MailboxProvider
    address: EmailAddress
