"""What alerts does, one use case per thing that can happen to a channel.

The delivery path is the one worth reading closely. It asks before sending
and records after — the opposite order to merchant's processed-event store,
and deliberately so: the two failures are not symmetric. See `DeliveryLog`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from datetime import timedelta
import logging
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.alerts.application.commands import (
    CreateChannelCommand,
    DeleteChannelCommand,
    DeliverMovementAlertCommand,
    RedeemChannelLinkCommand,
    UpdateChannelPreferenceCommand,
)
from personal_finance.contexts.alerts.application.inbox import InboxEntry, InboxKind
from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    MovementDirection,
    WeeklySummary,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelNotFoundError,
    ChatAlreadyLinkedError,
    InvalidLinkTokenError,
    TooManyChannelsError,
)
from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.ports import (
        AlertChannelRepository,
        BudgetStandings,
        ChannelLinkRepository,
        DeliveryLog,
        Inbox,
        MessageSender,
        Recipients,
        SummarySender,
        TokenGenerator,
        TokenHasher,
        WeeklySpendingSource,
    )
    from personal_finance.shared.application.ports import EventPublisher


def _expires_at(now: PosixTime, minutes: int) -> PosixTime:
    return PosixTime.from_datetime(now.to_datetime() + timedelta(minutes=minutes))


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class IssuedLink:
    """A channel waiting to be bound, and the one copy of its token.

    The token is handed out here and never again: it is not stored, only its
    hash is, and `GET /alerts/channels` cannot return it a second time. Lose
    it and ask for another — that is the whole difference between a one-time
    credential and a permanent one.
    """

    channel: AlertChannel
    token: str


class CreateChannelUseCase:
    """Open a pending channel and issue the link that will bind it."""

    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        links: ChannelLinkRepository,
        tokens: TokenGenerator,
        hasher: TokenHasher,
        link_ttl_minutes: int,
        max_channels_per_user: int,
    ) -> None:
        self._channels = channels
        self._links = links
        self._tokens = tokens
        self._hasher = hasher
        self._link_ttl_minutes = link_ttl_minutes
        self._max_channels_per_user = max_channels_per_user

    def execute(self, command: CreateChannelCommand) -> IssuedLink:
        existing = self._channels.list_by_user(command.user_id)

        # A pending channel is an unfinished attempt, not a channel. Retiring
        # it — and its link — keeps "ask again" from leaving a trail of live
        # tokens, the way a new reset link retires the previous one.
        for channel in existing:
            if channel.status is ChannelStatus.PENDING:
                if channel.pending_link_hash is not None:
                    self._links.discard(channel.pending_link_hash)

                self._channels.delete(
                    user_id=command.user_id,
                    channel_id=channel.id,
                )

        verified = [
            channel for channel in existing if channel.status is ChannelStatus.VERIFIED
        ]

        if len(verified) >= self._max_channels_per_user:
            raise TooManyChannelsError(
                "This account already has as many channels as it may have",
            )

        now = PosixTime.now()
        token = self._tokens.link_token()
        token_hash = self._hasher.hash(token)
        channel = AlertChannel.pending(
            user_id=command.user_id,
            kind=command.kind,
            link_hash=token_hash,
            now=now,
        )

        # The channel first: a link pointing at a channel that does not exist
        # yet would be redeemable in the instant between the two writes.
        self._channels.save(channel)
        self._links.issue(
            ChannelLink(
                token_hash=token_hash,
                channel_id=channel.id,
                user_id=command.user_id,
                expires_at=_expires_at(now, self._link_ttl_minutes),
            ),
        )

        return IssuedLink(channel=channel, token=token)


class RedeemChannelLinkUseCase:
    """Bind the destination that presented a live link token.

    Everything about who this is comes from the token. The caller is Telegram
    and carries no identity of ours, so the chat id it reports is an address
    to write to and never a claim about whose account this is.
    """

    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        links: ChannelLinkRepository,
        hasher: TokenHasher,
        sender: MessageSender,
        publisher: EventPublisher,
    ) -> None:
        self._channels = channels
        self._links = links
        self._hasher = hasher
        self._sender = sender
        self._publisher = publisher

    def execute(self, command: RedeemChannelLinkCommand) -> AlertChannel:
        now = PosixTime.now()
        link = self._links.spend(
            token_hash=self._hasher.hash(command.token),
            now=now,
        )

        if link is None:
            raise InvalidLinkTokenError("That link is not valid any more")

        channel = self._channels.find(
            user_id=link.user_id,
            channel_id=link.channel_id,
        )

        if channel is None:
            # The channel was deleted between asking for the link and
            # following it. Nothing to bind, and the token is already spent.
            raise InvalidLinkTokenError("That link is not valid any more")

        channel.verify(chat_id=command.chat_id, label=command.label, now=now)

        try:
            self._channels.save_verified(channel)
        except ChatAlreadyLinkedError:
            # The token is spent either way, so the tap would otherwise be a
            # dead end. Telling them here is safe: only the owner of a private
            # chat reads what is written to it.
            self._sender.send_chat_already_linked(chat_id=command.chat_id)

            raise

        self._publisher.publish(channel.pull_events())

        # Last, and outside everything that had to be consistent: a transport
        # that is having a bad minute must not undo a binding that already
        # happened.
        self._sender.send_link_confirmation(chat_id=command.chat_id)

        return channel


class UpdateChannelPreferenceUseCase:
    def __init__(self, *, channels: AlertChannelRepository) -> None:
        self._channels = channels

    def execute(self, command: UpdateChannelPreferenceCommand) -> AlertChannel:
        channel = self._channels.find(
            user_id=command.user_id,
            channel_id=command.channel_id,
        )

        if channel is None:
            raise ChannelNotFoundError("No such channel")

        channel.update_preference(
            AlertPreference(
                alert_type=command.alert_type,
                enabled=command.enabled,
                minimum_amount=command.minimum_amount,
            ),
        )
        self._channels.save(channel)

        return channel


class DeleteChannelUseCase:
    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        links: ChannelLinkRepository,
        publisher: EventPublisher,
    ) -> None:
        self._channels = channels
        self._links = links
        self._publisher = publisher

    def execute(self, command: DeleteChannelCommand) -> None:
        channel = self._channels.find(
            user_id=command.user_id,
            channel_id=command.channel_id,
        )

        if channel is None:
            raise ChannelNotFoundError("No such channel")

        if channel.pending_link_hash is not None:
            self._links.discard(channel.pending_link_hash)

        self._channels.delete(
            user_id=command.user_id,
            channel_id=command.channel_id,
        )
        channel.revoke()
        self._publisher.publish(channel.pull_events())


class DeliverMovementAlertUseCase:
    """Tell every channel of one account that money moved — and keep it for
    the app's own inbox, channel or not.

    Per channel, not per account: a send that succeeded on one destination
    and failed on another has to finish when the message is redelivered, so
    what is remembered is the pair.

    **The budget line is an enrichment and never a precondition.** Which
    budgets a purchase ate into is Financial's answer, read through a port,
    and an answer that fails — a table having a bad second — sends the alert
    without it. A purchase nobody heard about because a budget could not be
    read is exactly the failure this context exists to prevent.
    """

    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        deliveries: DeliveryLog,
        sender: MessageSender,
        budgets: BudgetStandings | None = None,
        inbox: Inbox | None = None,
        recipients: Recipients | None = None,
    ) -> None:
        self._channels = channels
        self._deliveries = deliveries
        self._sender = sender
        self._budgets = budgets
        self._inbox = inbox
        self._recipients = recipients

    def execute(self, command: DeliverMovementAlertCommand) -> int:
        """Deliver, and return how many destinations were told."""
        if not command.alert.is_worth_announcing:
            return 0

        alert = self._with_budgets(command)
        now = PosixTime.now()

        # Before any channel, and whether or not there is one: the inbox is
        # for everybody. Idempotent by the event's id, so a redelivery lands
        # on the same entry.
        if self._inbox is not None:
            self._inbox.record(
                InboxEntry(
                    user_id=command.user_id,
                    entry_id=command.event_id,
                    created_at=command.recorded_at or now,
                    kind=InboxKind.MOVEMENT,
                    movement=alert,
                ),
            )

        if self._recipients is not None:
            self._recipients.remember(user_id=command.user_id, now=now)

        delivered = 0

        for channel in self._channels.list_by_user(command.user_id):
            if not channel.should_deliver(
                alert_type=AlertType.MOVEMENT,
                amount=alert.amount,
            ):
                continue

            if channel.chat_id is None:  # pragma: no cover - the invariant holds
                continue

            if self._deliveries.was_delivered(
                user_id=command.user_id,
                event_id=command.event_id,
                channel_id=channel.id,
            ):
                continue

            try:
                self._sender.send_movement_alert(
                    chat_id=channel.chat_id,
                    alert=alert,
                )
            except DestinationRefusedError:
                # This destination said no and will keep saying no. It is not
                # a failure of the message, and the other channels of this
                # account are still waiting for it.
                _logger.warning(
                    "a destination refused an alert",
                    extra={"channel_id": str(channel.id.value)},
                )

                continue

            # Recorded only now. A crash here repeats one message; recording
            # first would have risked a purchase nobody was ever told about.
            self._deliveries.record_delivery(
                user_id=command.user_id,
                event_id=command.event_id,
                channel_id=channel.id,
                now=PosixTime.now(),
            )
            delivered += 1

        return delivered

    def _with_budgets(self, command: DeliverMovementAlertCommand) -> MovementAlert:
        alert = command.alert

        if (
            self._budgets is None
            or alert.movement_id is None
            or alert.direction is not MovementDirection.OUTGOING
        ):
            return alert

        try:
            standings = tuple(
                self._budgets.covering(
                    user_id=command.user_id,
                    movement_id=alert.movement_id,
                ),
            )
        except Exception:
            # Broad on purpose — see the class docstring. Logged without the
            # error's text, which could carry somebody's figures.
            _logger.warning("budgets could not be read for an alert", exc_info=False)

            return alert

        return dataclasses.replace(alert, budgets=standings)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WeeklyRun:
    """What one Monday did, for the log line and the tests."""

    users: int = 0
    summaries: int = 0
    delivered: int = 0
    failed: int = 0


# A fixed namespace, so a week's id is the same on every run: re-running
# Monday's job finds the entries and deliveries it already wrote.
WEEKLY_NAMESPACE = uuid.UUID("5f0c3e0e-6b1a-4f0e-9a51-2c7d8e7f0b61")


def weekly_entry_id(user_id: UserId, summary: WeeklySummary) -> uuid.UUID:
    return uuid.uuid5(
        WEEKLY_NAMESPACE,
        f"{user_id.value}:{summary.week_start.isoformat()}:{summary.currency.value}",
    )


class WeekNotOverError(ValueError):
    """A summary was asked for a week that has not ended."""


def weekly_created_at(summary: WeeklySummary) -> PosixTime:
    """The Monday after the week, 13:00 UTC — eight in the morning in Bogotá.

    Fixed rather than "now", so a second run of the same Monday writes the
    same inbox key and the conditional write makes it a no-op.
    """
    return PosixTime.from_datetime(
        dt.datetime.combine(
            summary.week_end + dt.timedelta(days=1),
            dt.time(13),
            tzinfo=dt.UTC,
        ),
    )


class SendWeeklySummariesUseCase:
    """Monday's look back at the week before, for everybody Alerts knows.

    One user failing never stops the others — a summary is a nicety, and a
    job that dies on the first person with odd data tells nobody anything.

    Idempotent per user, week and currency: the entry id is derived from
    them, the inbox write is conditional on it, and each channel's delivery
    is remembered under it. Running Monday twice sends nothing twice.
    """

    def __init__(
        self,
        *,
        recipients: Recipients,
        spending: WeeklySpendingSource,
        channels: AlertChannelRepository,
        deliveries: DeliveryLog,
        sender: SummarySender,
        inbox: Inbox,
    ) -> None:
        self._recipients = recipients
        self._spending = spending
        self._channels = channels
        self._deliveries = deliveries
        self._sender = sender
        self._inbox = inbox

    def execute(self, *, week_of: dt.date, today: dt.date) -> WeeklyRun:
        """Raises `TransportUnavailableError` at the end — never halfway —
        when a transport was down for somebody, so the scheduler runs it
        again; everybody already told is skipped by the delivery log.

        Raises `WeekNotOverError` for a week that has not ended by `today`.
        A week's id is fixed, so a summary of half a week would take the
        place of the whole one: Monday's run would find it already written
        and already sent, and the real figures would never go out.
        """
        week_ends = (
            week_of - dt.timedelta(days=week_of.weekday()) + dt.timedelta(days=6)
        )

        if week_ends >= today:
            raise WeekNotOverError(
                f"The week ending {week_ends.isoformat()} is not over yet",
            )

        users = summaries = delivered = failed = 0
        transport_down = False

        for user_id in self._recipients.everyone():
            users += 1

            try:
                told, sent = self._one(user_id, week_of)
            except TransportUnavailableError:
                transport_down = True
                failed += 1

                continue
            except Exception:
                failed += 1
                _logger.warning(
                    "weekly summary failed for one user",
                    extra={"user_id": str(user_id.value)},
                    exc_info=False,
                )

                continue

            summaries += told
            delivered += sent

        run = WeeklyRun(
            users=users,
            summaries=summaries,
            delivered=delivered,
            failed=failed,
        )
        _logger.info("weekly summaries done", extra=dataclasses.asdict(run))

        if transport_down:
            raise TransportUnavailableError(
                f"{failed} weekly summaries could not be sent; run again",
            )

        return run

    def _one(self, user_id: UserId, week_of: dt.date) -> tuple[int, int]:
        weeks = self._spending.week(user_id=user_id, week_of=week_of)

        if not weeks:
            return 0, 0

        # The busiest currency is the summary. A second one is rare enough —
        # a trip — that one message about it would be noise more often than
        # help; the inbox keeps it all the same.
        for summary in weeks:
            self._inbox.record(
                InboxEntry(
                    user_id=user_id,
                    entry_id=weekly_entry_id(user_id, summary),
                    created_at=weekly_created_at(summary),
                    kind=InboxKind.WEEKLY_SUMMARY,
                    summary=summary,
                ),
            )

        lead = weeks[0]
        entry_id = weekly_entry_id(user_id, lead)
        sent = 0

        for channel in self._channels.list_by_user(user_id):
            if channel.chat_id is None or not channel.is_verified:
                continue

            if not channel.preferences.for_type(AlertType.WEEKLY_SUMMARY).enabled:
                continue

            if self._deliveries.was_delivered(
                user_id=user_id,
                event_id=entry_id,
                channel_id=channel.id,
            ):
                continue

            try:
                self._sender.send_weekly_summary(chat_id=channel.chat_id, summary=lead)
            except DestinationRefusedError:
                continue

            self._deliveries.record_delivery(
                user_id=user_id,
                event_id=entry_id,
                channel_id=channel.id,
                now=PosixTime.now(),
            )
            sent += 1

        return len(weeks), sent
