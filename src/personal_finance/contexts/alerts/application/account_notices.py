"""Notices about somebody's account rather than their money.

Today one: the model stopped reading their unrecognised mail. It goes
everywhere they can be told — the in-app inbox, every bound Telegram chat,
and their email — whatever they chose for alerts, because it is not an alert
they opted into: it is something that happened to their account, and a
person who never linked Telegram must still hear about it.

Delivery follows the movement's order: ask, send, and only then record, so a
crash repeats one message rather than swallowing the notice.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.alerts.application.inbox import InboxEntry, InboxKind
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelId,
    ChannelStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.commands import (
        NotifyModelPauseCommand,
    )
    from personal_finance.contexts.alerts.application.ports import (
        AccountContacts,
        AccountMailer,
        AccountNoticeSender,
        AlertChannelRepository,
        DeliveryLog,
        Inbox,
    )


_logger = logging.getLogger(__name__)

#: The delivery log is keyed by channel; the account's email is not a channel,
#: so it is remembered under an id no channel can have — `ChannelId.new()` is
#: random, version 4, and never all zeros.
EMAIL_DELIVERY = ChannelId(value=uuid.UUID(int=0))


class NotifyModelPauseUseCase:
    def __init__(
        self,
        *,
        channels: AlertChannelRepository,
        deliveries: DeliveryLog,
        sender: AccountNoticeSender,
        inbox: Inbox,
        contacts: AccountContacts,
        mailer: AccountMailer,
    ) -> None:
        self._channels = channels
        self._deliveries = deliveries
        self._sender = sender
        self._inbox = inbox
        self._contacts = contacts
        self._mailer = mailer

    def execute(self, command: NotifyModelPauseCommand) -> int:
        """Tell the person, and return how many destinations were told.

        Raises `TransportUnavailableError` when the mail server or Telegram
        was down — after trying every other destination, so a mailbox that
        is misconfigured never keeps a working Telegram from being told.
        What was delivered is remembered, so the retry only finishes the rest.
        """
        self._inbox.record(
            InboxEntry(
                user_id=command.user_id,
                entry_id=command.event_id,
                created_at=command.recorded_at,
                kind=InboxKind.MODEL_PAUSED,
                pause=command.pause,
            ),
        )

        unavailable: list[TransportUnavailableError] = []

        try:
            delivered = self._email(command)
        except TransportUnavailableError as error:
            unavailable.append(error)
            delivered = 0

        for channel in self._channels.list_by_user(command.user_id):
            if channel.status is not ChannelStatus.VERIFIED or channel.chat_id is None:
                continue

            if self._deliveries.was_delivered(
                user_id=command.user_id,
                event_id=command.event_id,
                channel_id=channel.id,
            ):
                continue

            try:
                self._sender.send_model_pause(
                    chat_id=channel.chat_id,
                    pause=command.pause,
                )
            except TransportUnavailableError as error:
                unavailable.append(error)

                continue
            except DestinationRefusedError:
                _logger.warning(
                    "a destination refused an account notice",
                    extra={"channel_id": str(channel.id.value)},
                )

                continue

            self._deliveries.record_delivery(
                user_id=command.user_id,
                event_id=command.event_id,
                channel_id=channel.id,
                now=PosixTime.now(),
            )
            delivered += 1

        if unavailable:
            raise unavailable[0]

        return delivered

    def _email(self, command: NotifyModelPauseCommand) -> int:
        if self._deliveries.was_delivered(
            user_id=command.user_id,
            event_id=command.event_id,
            channel_id=EMAIL_DELIVERY,
        ):
            return 0

        recipient = self._contacts.find(command.user_id)

        if recipient is None:
            # The account is gone; nobody is left to tell.
            _logger.warning(
                "no account to email an account notice to",
                extra={"event_id": str(command.event_id)},
            )

            return 0

        try:
            self._mailer.send_model_pause(to=recipient, pause=command.pause)
        except DestinationRefusedError:
            _logger.warning(
                "the mail server refused an account's address",
                extra={"event_id": str(command.event_id)},
            )

            return 0

        self._deliveries.record_delivery(
            user_id=command.user_id,
            event_id=command.event_id,
            channel_id=EMAIL_DELIVERY,
            now=PosixTime.now(),
        )

        return 1
