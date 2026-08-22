from __future__ import annotations

import dataclasses
import enum

from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


class MailboxAccessRevokedError(Exception):
    """The provider will not accept our credentials any more.

    Permanent until the user authorizes again: retrying is useless, and
    hammering a revoked grant is how an application gets rate-limited or
    flagged. Adapters must raise this — and only this — for that case, so the
    application layer can tell it apart from a provider having a bad minute.
    """


class MailboxTemporarilyUnavailableError(Exception):
    """The provider failed in a way that is worth retrying: a timeout, a 5xx,
    a rate limit.
    """


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
    # The provider stopped accepting our credentials — the user revoked
    # access, or the grant simply aged out. Distinct from REVOKED because the
    # user did not ask for this and needs to be told: one click puts it back.
    NEEDS_REAUTH = "needs_reauth"
    # The user disconnected the mailbox themselves. We must not read it, and
    # must not nag them about it either.
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
    # When the provider stops notifying us unless we renew. Gmail caps a watch
    # at 7 days, Graph at about 3. None means nothing is subscribed yet.
    subscription_expires_at: PosixTime | None = None
    # When a sync last completed. A mailbox that has been quiet far longer
    # than usual is the only visible symptom of a subscription that died
    # without erroring.
    last_synced_at: PosixTime | None = None

    @property
    def is_active(self) -> bool:
        return self.status is MailboxConnectionStatus.ACTIVE

    def subscription_expires_within(
        self,
        *,
        now: PosixTime,
        seconds: int,
    ) -> bool:
        """Whether the subscription needs renewing already.

        An unsubscribed connection always answers yes: never having had a
        subscription is more urgent than having one about to lapse.
        """
        if self.subscription_expires_at is None:
            return True

        remaining = (
            self.subscription_expires_at.as_epoch_seconds() - now.as_epoch_seconds()
        )

        return remaining <= seconds


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MailboxBatch:
    """What one sync pass found, plus where to resume next time."""

    emails: tuple[InboundEmail, ...] = ()
    cursor: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Subscription:
    """What a provider gives back when we ask it to notify us.

    The cursor matters as much as the expiry: Gmail's `watch` answers with the
    history id current at that moment, which is where a first sync must start
    from. Without it a fresh connection has no idea how far back "new" goes.
    """

    expires_at: PosixTime
    cursor: str | None = None


class MailboxEventDelivery(enum.Enum):
    """How a provider tells us a mailbox changed."""

    # The provider calls us. What we want everywhere it is offered.
    PUSH = "push"
    # Nobody calls; the only way to notice is to look. The simulated provider
    # and plain IMAP work this way.
    POLL = "poll"


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
