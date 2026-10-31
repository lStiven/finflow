"""Telling somebody the model stopped reading their unrecognised mail —
everywhere they can be told, once, whatever they chose for alerts."""

from collections.abc import Sequence
import uuid

import pytest

from personal_finance.contexts.alerts.application.account_notices import (
    EMAIL_DELIVERY,
    NotifyModelPauseUseCase,
)
from personal_finance.contexts.alerts.application.commands import (
    NotifyModelPauseCommand,
)
from personal_finance.contexts.alerts.application.inbox import InboxEntry, InboxKind
from personal_finance.contexts.alerts.application.messages import (
    MailRecipient,
    ModelPause,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelId,
    ChannelKind,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_epoch_seconds(1_757_800_000)
ANA = MailRecipient(address="ana@example.com", name="Ana")
PAUSE = ModelPause(
    strike=1,
    paused_until=PosixTime.from_epoch_seconds(1_757_886_400),
    offenses=10,
)


class Channels:
    """Read-only: the notice never changes a channel."""

    def __init__(self, *channels: AlertChannel) -> None:
        self._channels = channels

    def list_by_user(self, user_id: UserId) -> Sequence[AlertChannel]:
        return [channel for channel in self._channels if channel.user_id == user_id]

    def find(self, *, user_id: UserId, channel_id: ChannelId) -> AlertChannel | None:
        raise NotImplementedError

    def save(self, channel: AlertChannel) -> None:
        raise NotImplementedError

    def save_verified(self, channel: AlertChannel) -> None:
        raise NotImplementedError

    def delete(self, *, user_id: UserId, channel_id: ChannelId) -> ChatId | None:
        raise NotImplementedError


class Deliveries:
    def __init__(self) -> None:
        self.seen: set[tuple[uuid.UUID, ChannelId]] = set()

    def was_delivered(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
    ) -> bool:
        del user_id

        return (event_id, channel_id) in self.seen

    def record_delivery(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
        now: PosixTime,
    ) -> None:
        del user_id, now
        self.seen.add((event_id, channel_id))


class Sender:
    def __init__(self, *, refuse: set[ChatId] | None = None) -> None:
        self.sent: list[ChatId] = []
        self.refuse = refuse or set()
        self.down = False

    def send_model_pause(self, *, chat_id: ChatId, pause: ModelPause) -> None:
        del pause

        if self.down:
            raise TransportUnavailableError("telegram down")

        if chat_id in self.refuse:
            raise DestinationRefusedError("blocked")

        self.sent.append(chat_id)


class Inbox:
    def __init__(self) -> None:
        self.entries: list[InboxEntry] = []

    def record(self, entry: InboxEntry) -> None:
        self.entries.append(entry)

    def recent(self, *, user_id: UserId, limit: int) -> Sequence[InboxEntry]:
        del user_id, limit

        return self.entries

    def dismiss(self, *, user_id: UserId, entry_id: uuid.UUID) -> bool:
        del user_id, entry_id

        return False

    def dismiss_all(self, *, user_id: UserId) -> int:
        del user_id

        return 0


class Contacts:
    def __init__(self, recipient: MailRecipient | None) -> None:
        self.recipient = recipient

    def find(self, user_id: UserId) -> MailRecipient | None:
        del user_id

        return self.recipient


class Mailer:
    def __init__(self) -> None:
        self.sent: list[MailRecipient] = []
        self.down = False
        self.refuse = False

    def send_model_pause(self, *, to: MailRecipient, pause: ModelPause) -> None:
        del pause

        if self.down:
            raise TransportUnavailableError("smtp down")

        if self.refuse:
            raise DestinationRefusedError("no such mailbox")

        self.sent.append(to)


def _channel(chat: str | None) -> AlertChannel:
    channel = AlertChannel.pending(
        user_id=USER_ID,
        kind=ChannelKind.TELEGRAM,
        link_hash=SecretHash(f"sha256:{uuid.uuid4().hex}"),
        now=NOW,
    )

    if chat is not None:
        channel.verify(chat_id=ChatId(chat), label="Ana", now=NOW)

    return channel


def _command() -> NotifyModelPauseCommand:
    return NotifyModelPauseCommand(
        user_id=USER_ID,
        event_id=uuid.uuid4(),
        pause=PAUSE,
        recorded_at=NOW,
    )


def _use_case(
    *channels: AlertChannel,
    sender: Sender | None = None,
    mailer: Mailer | None = None,
    contact: MailRecipient | None = ANA,
    deliveries: Deliveries | None = None,
    inbox: Inbox | None = None,
) -> NotifyModelPauseUseCase:
    return NotifyModelPauseUseCase(
        channels=Channels(*channels),
        deliveries=deliveries or Deliveries(),
        sender=sender or Sender(),
        inbox=inbox or Inbox(),
        contacts=Contacts(contact),
        mailer=mailer or Mailer(),
    )


def test_it_reaches_the_inbox_the_email_and_every_bound_chat() -> None:
    sender, mailer, inbox = Sender(), Mailer(), Inbox()

    delivered = _use_case(
        _channel("111"),
        _channel("222"),
        sender=sender,
        mailer=mailer,
        inbox=inbox,
    ).execute(_command())

    assert delivered == 3
    assert sorted(chat.value for chat in sender.sent) == ["111", "222"]
    assert [to.address for to in mailer.sent] == ["ana@example.com"]
    [entry] = inbox.entries
    assert entry.kind is InboxKind.MODEL_PAUSED
    assert entry.pause == PAUSE


def test_a_channel_still_waiting_to_be_bound_is_skipped() -> None:
    sender = Sender()

    _use_case(_channel(None), sender=sender).execute(_command())

    assert sender.sent == []


def test_it_ignores_what_the_person_chose_for_alerts() -> None:
    """A notice about the account is not an alert anybody opted into."""
    channel = _channel("111")
    for alert_type in AlertType:
        channel.update_preference(AlertPreference(alert_type=alert_type, enabled=False))
    sender = Sender()

    _use_case(channel, sender=sender).execute(_command())

    assert [chat.value for chat in sender.sent] == ["111"]


def test_somebody_without_telegram_is_still_emailed() -> None:
    mailer = Mailer()

    assert _use_case(mailer=mailer).execute(_command()) == 1
    assert len(mailer.sent) == 1


def test_a_redelivery_tells_nobody_twice() -> None:
    sender, mailer, deliveries = Sender(), Mailer(), Deliveries()
    use_case = _use_case(
        _channel("111"),
        sender=sender,
        mailer=mailer,
        deliveries=deliveries,
    )
    command = _command()

    use_case.execute(command)
    assert use_case.execute(command) == 0

    assert len(sender.sent) == 1
    assert len(mailer.sent) == 1


def test_a_mail_server_that_is_down_retries_without_repeating_the_rest() -> None:
    sender, mailer, deliveries = Sender(), Mailer(), Deliveries()
    use_case = _use_case(
        _channel("111"),
        sender=sender,
        mailer=mailer,
        deliveries=deliveries,
    )
    command = _command()
    sender.down = True

    with pytest.raises(TransportUnavailableError):
        use_case.execute(command)

    # The email went out before Telegram failed; the retry only finishes it.
    sender.down = False
    use_case.execute(command)

    assert len(mailer.sent) == 1
    assert [chat.value for chat in sender.sent] == ["111"]
    assert (command.event_id, EMAIL_DELIVERY) in deliveries.seen


def test_a_mail_server_that_is_down_still_lets_telegram_tell_them() -> None:
    sender, mailer, deliveries = Sender(), Mailer(), Deliveries()
    mailer.down = True
    use_case = _use_case(
        _channel("111"),
        sender=sender,
        mailer=mailer,
        deliveries=deliveries,
    )
    command = _command()

    with pytest.raises(TransportUnavailableError):
        use_case.execute(command)

    # Telegram was told anyway; the retry only owes the email.
    assert [chat.value for chat in sender.sent] == ["111"]
    mailer.down = False
    use_case.execute(command)
    assert len(sender.sent) == 1
    assert len(mailer.sent) == 1


def test_a_refused_address_or_chat_does_not_stop_the_others() -> None:
    sender, mailer = Sender(refuse={ChatId("111")}), Mailer()
    mailer.refuse = True

    delivered = _use_case(
        _channel("111"),
        _channel("222"),
        sender=sender,
        mailer=mailer,
    ).execute(_command())

    assert delivered == 1
    assert [chat.value for chat in sender.sent] == ["222"]


def test_an_account_that_is_gone_gets_no_email_but_the_rest_still_goes() -> None:
    sender = Sender()

    delivered = _use_case(_channel("111"), sender=sender, contact=None).execute(
        _command(),
    )

    assert delivered == 1
