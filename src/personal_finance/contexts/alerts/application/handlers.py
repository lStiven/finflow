"""What alerts does, one use case per thing that can happen to a channel.

The delivery path is the one worth reading closely. It asks before sending
and records after — the opposite order to merchant's processed-event store,
and deliberately so: the two failures are not symmetric. See `DeliveryLog`.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
import logging
from typing import TYPE_CHECKING

from personal_finance.contexts.alerts.application.commands import (
    CreateChannelCommand,
    DeleteChannelCommand,
    DeliverMovementAlertCommand,
    RedeemChannelLinkCommand,
    UpdateChannelPreferenceCommand,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
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
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.ports import (
        AlertChannelRepository,
        ChannelLinkRepository,
        DeliveryLog,
        MessageSender,
        TokenGenerator,
        TokenHasher,
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
    """Tell every channel of one account that money moved.

    Per channel, not per account: a send that succeeded on one destination
    and failed on another has to finish when the message is redelivered, so
    what is remembered is the pair.
    """

    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        deliveries: DeliveryLog,
        sender: MessageSender,
    ) -> None:
        self._channels = channels
        self._deliveries = deliveries
        self._sender = sender

    def execute(self, command: DeliverMovementAlertCommand) -> int:
        """Deliver, and return how many destinations were told."""
        if not command.alert.is_worth_announcing:
            return 0

        delivered = 0

        for channel in self._channels.list_by_user(command.user_id):
            if not channel.should_deliver(
                alert_type=AlertType.MOVEMENT,
                amount=command.alert.amount,
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
                    alert=command.alert,
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
