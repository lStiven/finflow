from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from typing import TYPE_CHECKING, Protocol
import uuid

from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    MovementAlert,
    WeeklySummary,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelId,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.inbox import InboxEntry


class AlertChannelRepository(Protocol):
    """Persistence port for `AlertChannel`.

    Every method takes the owner. Channels are per-user, and an address that
    receives somebody's purchases is the last thing that should be reachable
    by an implementation that does not know whose it is.
    """

    def find(
        self,
        *,
        user_id: UserId,
        channel_id: ChannelId,
    ) -> AlertChannel | None:
        """Load one channel, or None when this user has no such channel.

        A channel belonging to somebody else reads as missing rather than as
        forbidden: telling the two apart is how an id becomes enumerable.
        """
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[AlertChannel]:
        """Every channel this user owns, verified or still pending."""
        ...

    def save(self, channel: AlertChannel) -> None:
        """Create or replace a channel that is not changing its binding."""
        ...

    def save_verified(self, channel: AlertChannel) -> None:
        """Persist a channel that has just bound a chat, atomically.

        Two facts that must never disagree: this channel now points at that
        chat, and that chat now belongs to this account. An implementation
        writes both together or neither.

        It also enforces, in storage, what the aggregate enforced in memory —
        the channel must still be pending, and the chat must be free or
        already this same user's. Both are races a read cannot close, so the
        write is where they are decided:

        * `ChannelAlreadyVerifiedError` when the channel moved on since it
          was loaded, which is a replayed token;
        * `ChatAlreadyLinkedError` when the chat belongs to another account.

        One chat, one account. Two people's movements arriving in one
        Telegram conversation is a leak neither of them agreed to.
        """
        ...

    def delete(self, *, user_id: UserId, channel_id: ChannelId) -> ChatId | None:
        """Remove a channel and free the chat it held, in that order.

        Returns the address it was bound to, so the caller needs no prior
        read that a concurrent delete could invalidate. None when there was
        no such channel, or when it never got as far as being bound.

        The reservation is freed only if it still names this channel: a
        delete arriving late must not release a chat that by now belongs to
        a newer one.
        """
        ...


class ChannelLinkRepository(Protocol):
    """The short-lived invitations that bind a channel to a destination.

    Found by the hash of the token somebody presents, not by who they are:
    the webhook's caller is Telegram, and it carries no identity of ours.
    """

    def issue(self, link: ChannelLink) -> None:
        """Store a pending link. Refuses to overwrite an existing hash."""
        ...

    def spend(self, *, token_hash: SecretHash, now: PosixTime) -> ChannelLink | None:
        """Redeem a link, exactly once, and say whose it was.

        Returns None when the hash is unknown, already spent, or expired —
        one answer for all three, because the caller answers all three the
        same way.

        An implementation must compare the expiry itself rather than trusting
        a storage sweep to have removed it: a time-to-live is eventual and
        routinely hours late.
        """
        ...

    def discard(self, token_hash: SecretHash) -> None:
        """Drop a link before it is used. Unknown hashes are not an error.

        Found by hash like every other operation here, which is why the
        channel carries the hash of its own live link.
        """
        ...


class DeliveryLog(Protocol):
    """Which facts have already reached which channel.

    Delivery from the bus is at-least-once, so the same movement arrives
    twice. This is what makes the second arrival quiet, and it has to survive
    a restart — which is why it is a port and not a set in the worker.

    **The order is the opposite of merchant's `ProcessedEventStore`, and that
    is deliberate.** Merchant claims before doing the work and accepts losing
    one sighting to a crash, because the next sighting rebuilds it. Here the
    two failures are not symmetric: marking first means a crash between the
    mark and the send is a purchase that is never announced — silent, and
    exactly what this context exists to prevent — while marking last means a
    crash between the send and the mark is one repeated message. So the
    caller asks, sends, and only then records.
    """

    def was_delivered(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
    ) -> bool:
        """Whether this exact fact already reached this exact channel.

        Keyed by channel as well as by event: with two channels bound, a
        send that succeeded on one and raised on the other has to finish on
        redelivery instead of being refused as a whole.
        """
        ...

    def record_delivery(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
        now: PosixTime,
    ) -> None:
        """Remember that it did. Writing it twice is not an error."""
        ...


class TokenGenerator(Protocol):
    """Where the unguessable part of a deep link comes from."""

    def link_token(self) -> str:
        """A fresh token, from a cryptographic source, never a PRNG."""
        ...


class TokenHasher(Protocol):
    """How a link token becomes the key it is stored under.

    Deterministic on purpose, unlike a password hash: the value *is* a
    partition key, so it has to hash the same way twice. Safe here and not
    for a password because the input is 256 random bits, which no dictionary
    covers.
    """

    def hash(self, token: str) -> SecretHash: ...


class TransportUnavailableError(Exception):
    """The transport could not be reached, or asked to be tried again later.

    A timeout, a 5xx, a rate limit. The message stays on the queue: the
    purchase is real and the owner still has not been told about it.
    """


class DestinationRefusedError(Exception):
    """The transport refused this destination, and will keep refusing it.

    The bot was blocked, or removed from the chat. Retrying cannot change
    that, so the message must not cycle until a dead-letter queue takes it —
    a blocked chat is a person who said no, not an outage.
    """


class MessageSender(Protocol):
    """Delivers one message to one destination.

    Every method takes facts, never text: the words belong to the adapter,
    because only it knows what its transport can render. That is also what
    keeps caller-supplied text from ever becoming a template with a hole in
    it — the same shape identity's `CredentialNotifier` has, for the same
    reason.

    An implementation raises on a failure worth retrying and returns normally
    otherwise. Deciding which is which belongs to the adapter, because only
    it knows what its transport's refusals mean.
    """

    def send_movement_alert(
        self,
        *,
        chat_id: ChatId,
        alert: MovementAlert,
    ) -> None: ...

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        """Say hello the moment a channel binds.

        Not decoration: it is the only thing that proves this deployment can
        actually *send* to the address it just bound, at the moment somebody
        is looking at Telegram waiting for it — rather than discovering it
        cannot at the first purchase.
        """
        ...

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        """Say why a tap did nothing, where the person is already looking.

        The link is spent either way — it *was* used — so without this the
        tap is a dead end: nothing happens in Telegram, nothing happens on
        the screen, and the only way forward is to guess that pressing
        Connect again is the answer. The same reason identity sends its
        "nothing to do" mail rather than staying silent.
        """
        ...


class SummarySender(Protocol):
    """Delivers Monday's summary. Its own port so the movement's stays small."""

    def send_weekly_summary(
        self, *, chat_id: ChatId, summary: WeeklySummary
    ) -> None: ...


class BudgetStandings(Protocol):
    """Which budgets a movement counts against, as Financial answers it.

    Financial owns budgets, categories and what counts as spending; this port
    is how Alerts asks without knowing any of it. An implementation answers
    empty for a movement it cannot find — and may raise on an outage, which
    the caller treats as «no budget line» rather than as a failed alert.
    """

    def covering(
        self,
        *,
        user_id: UserId,
        movement_id: str,
    ) -> Sequence[BudgetStanding]: ...


class WeeklySpendingSource(Protocol):
    """One week of somebody's spending against their own normal, per currency.

    Busiest currency first. Empty when there is nothing to say — no spending
    this week and none before it.
    """

    def week(self, *, user_id: UserId, week_of: dt.date) -> Sequence[WeeklySummary]: ...


class Inbox(Protocol):
    """The in-app copy of every alert, per user.

    Writing the same entry twice is not an error and changes nothing: the
    entry's id is the fact's, and delivery is at-least-once.
    """

    def record(self, entry: InboxEntry) -> None: ...

    def recent(self, *, user_id: UserId, limit: int) -> Sequence[InboxEntry]:
        """Newest first, this user's only, leaving out what they dismissed."""
        ...

    def dismiss(self, *, user_id: UserId, entry_id: uuid.UUID) -> bool:
        """Hide one entry for good. False when this user has no such entry.

        Hidden rather than deleted: delivery is at-least-once, and a
        redelivery after a delete would write the entry back — the row that
        stays is what makes the second copy a no-op.
        """
        ...

    def dismiss_all(self, *, user_id: UserId) -> int:
        """Hide every entry this user has. Returns how many it hid."""
        ...


class MovementPresence(Protocol):
    """Which movements still exist, as Financial answers it.

    An alert about a movement its owner erased points at nothing; the inbox
    asks this at read time and leaves those out. An implementation may raise
    on an outage, which the caller treats as "assume they all exist" — an
    inbox with one stale entry beats an inbox that does not load.
    """

    def existing(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> frozenset[str]: ...


class Recipients(Protocol):
    """Everybody Alerts has ever had something to tell.

    Alerts' own list, built from the movements it hears about, so a weekly
    job can walk its users without asking another context who exists.
    """

    def remember(self, *, user_id: UserId, now: PosixTime) -> None: ...

    def everyone(self) -> Sequence[UserId]: ...
