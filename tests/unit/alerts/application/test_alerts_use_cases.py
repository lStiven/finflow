"""The alerts use cases, against hand-written stand-ins for their ports.

The fakes enforce the same refusals the real adapter does — a chat another
account holds, a token used twice — because a use-case test against a fake
that accepts everything only proves the happy path compiles.
"""

from collections.abc import Sequence
import dataclasses
from decimal import Decimal
import hashlib
import uuid

import pytest

from personal_finance.contexts.alerts.application.commands import (
    CreateChannelCommand,
    DeleteChannelCommand,
    DeliverMovementAlertCommand,
    RedeemChannelLinkCommand,
    UpdateChannelPreferenceCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    CreateChannelUseCase,
    DeleteChannelUseCase,
    DeliverMovementAlertUseCase,
    RedeemChannelLinkUseCase,
    UpdateChannelPreferenceUseCase,
)
from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    MovementDirection,
    MovementOrigin,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
)
from personal_finance.contexts.alerts.application.queries import ListChannelsUseCase
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.events import (
    AlertChannelRevoked,
    AlertChannelVerified,
)
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
    ChannelNotFoundError,
    ChatAlreadyLinkedError,
    InvalidLinkTokenError,
    TooManyChannelsError,
)
from personal_finance.contexts.alerts.domain.linking import ChannelLink
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


class InMemoryChannelRepository:
    def __init__(self) -> None:
        self._channels: dict[tuple[UserId, ChannelId], AlertChannel] = {}
        self._chats: dict[ChatId, tuple[UserId, ChannelId]] = {}

    def find(
        self,
        *,
        user_id: UserId,
        channel_id: ChannelId,
    ) -> AlertChannel | None:
        return self._channels.get((user_id, channel_id))

    def list_by_user(self, user_id: UserId) -> Sequence[AlertChannel]:
        return [
            channel
            for (owner, _), channel in self._channels.items()
            if owner == user_id
        ]

    def save(self, channel: AlertChannel) -> None:
        self._channels[(channel.user_id, channel.id)] = channel

    def save_verified(self, channel: AlertChannel) -> None:
        if channel.chat_id is None:
            raise ValueError("A verified channel must have somewhere to send")

        stored = self._channels.get((channel.user_id, channel.id))

        if (
            stored is not None
            and stored is not channel
            and stored.status is ChannelStatus.VERIFIED
        ):
            raise ChannelAlreadyVerifiedError("Already verified")

        holder = self._chats.get(channel.chat_id)

        if holder is not None and holder[0] != channel.user_id:
            raise ChatAlreadyLinkedError("That chat belongs to another account")

        self._chats[channel.chat_id] = (channel.user_id, channel.id)
        self._channels[(channel.user_id, channel.id)] = channel

    def delete(self, *, user_id: UserId, channel_id: ChannelId) -> ChatId | None:
        channel = self._channels.pop((user_id, channel_id), None)

        if channel is None or channel.chat_id is None:
            return None

        if self._chats.get(channel.chat_id) == (user_id, channel_id):
            del self._chats[channel.chat_id]

        return channel.chat_id


class InMemoryLinkRepository:
    def __init__(self) -> None:
        self._links: dict[str, ChannelLink] = {}

    def issue(self, link: ChannelLink) -> None:
        if link.token_hash.value in self._links:
            raise ValueError("That hash is already issued")

        self._links[link.token_hash.value] = link

    def spend(self, *, token_hash: SecretHash, now: PosixTime) -> ChannelLink | None:
        link = self._links.pop(token_hash.value, None)

        if link is None or link.is_expired(now):
            return None

        return link

    def discard(self, token_hash: SecretHash) -> None:
        self._links.pop(token_hash.value, None)

    @property
    def live_tokens(self) -> int:
        return len(self._links)


class InMemoryDeliveryLog:
    def __init__(self) -> None:
        self._seen: set[tuple[UserId, uuid.UUID, ChannelId]] = set()

    def was_delivered(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
    ) -> bool:
        return (user_id, event_id, channel_id) in self._seen

    def record_delivery(
        self,
        *,
        user_id: UserId,
        event_id: uuid.UUID,
        channel_id: ChannelId,
        now: PosixTime,
    ) -> None:
        self._seen.add((user_id, event_id, channel_id))


class FixedTokenGenerator:
    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)

    def link_token(self) -> str:
        return self._tokens.pop(0) if self._tokens else "token-fallback"


class Sha256TestHasher:
    def hash(self, token: str) -> SecretHash:
        return SecretHash(hashlib.sha256(token.encode()).hexdigest())


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SentAlert:
    chat_id: ChatId
    alert: MovementAlert


class RecordingMessageSender:
    def __init__(self) -> None:
        self.alerts: list[SentAlert] = []
        self.confirmations: list[ChatId] = []
        self.refusals: list[ChatId] = []

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self.alerts.append(SentAlert(chat_id=chat_id, alert=alert))

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        self.confirmations.append(chat_id)

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        self.refusals.append(chat_id)


class ExplodingMessageSender(RecordingMessageSender):
    """A transport having a bad minute."""

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        raise RuntimeError("telegram is down")


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.events.extend(events)


USER = UserId.new()
OTHER_USER = UserId.new()
CHAT = ChatId("123456789")


class _World:
    def __init__(self, *tokens: str, max_channels: int = 5) -> None:
        self.channels = InMemoryChannelRepository()
        self.links = InMemoryLinkRepository()
        self.deliveries = InMemoryDeliveryLog()
        self.sender = RecordingMessageSender()
        self.publisher = RecordingPublisher()
        hasher = Sha256TestHasher()

        self.create = CreateChannelUseCase(
            channels=self.channels,
            links=self.links,
            tokens=FixedTokenGenerator(*tokens),
            hasher=hasher,
            link_ttl_minutes=15,
            max_channels_per_user=max_channels,
        )
        self.redeem = RedeemChannelLinkUseCase(
            channels=self.channels,
            links=self.links,
            hasher=hasher,
            sender=self.sender,
            publisher=self.publisher,
        )
        self.update = UpdateChannelPreferenceUseCase(channels=self.channels)
        self.delete = DeleteChannelUseCase(
            channels=self.channels,
            links=self.links,
            publisher=self.publisher,
        )
        self.list = ListChannelsUseCase(channels=self.channels)

    def open(self, user: UserId = USER) -> str:
        return self.create.execute(
            CreateChannelCommand(user_id=user, kind=ChannelKind.TELEGRAM),
        ).token

    def bind(self, token: str, chat: ChatId = CHAT, label: str | None = "Ana") -> None:
        self.redeem.execute(
            RedeemChannelLinkCommand(token=token, chat_id=chat, label=label),
        )


# ----------------------------------------------------------------------
# Opening a channel
# ----------------------------------------------------------------------


def test_a_new_channel_starts_pending_and_hands_out_one_token() -> None:
    world = _World("tok-1")

    issued = world.create.execute(
        CreateChannelCommand(user_id=USER, kind=ChannelKind.TELEGRAM),
    )

    assert issued.token == "tok-1"
    assert issued.channel.status is ChannelStatus.PENDING
    assert world.links.live_tokens == 1


def test_asking_again_retires_the_previous_attempt_and_its_token() -> None:
    """Two live links for one account is a second thing that can be stolen."""
    world = _World("tok-1", "tok-2")

    first = world.open()
    world.open()

    assert world.links.live_tokens == 1

    with pytest.raises(InvalidLinkTokenError):
        world.bind(first)


def test_an_account_may_not_hold_more_verified_channels_than_allowed() -> None:
    world = _World("tok-1", "tok-2", max_channels=1)

    world.bind(world.open())

    with pytest.raises(TooManyChannelsError):
        world.open()


# ----------------------------------------------------------------------
# Redeeming the link
# ----------------------------------------------------------------------


def test_redeeming_binds_the_chat_and_says_hello() -> None:
    world = _World("tok-1")

    world.bind(world.open())

    [view] = world.list.execute(USER)
    assert view.status is ChannelStatus.VERIFIED
    assert view.chat_hint == "…6789"
    assert view.label == "Ana"
    assert world.sender.confirmations == [CHAT]
    assert any(
        isinstance(event, AlertChannelVerified) for event in world.publisher.events
    )


def test_a_token_is_spent_exactly_once() -> None:
    world = _World("tok-1")
    token = world.open()

    world.bind(token)

    with pytest.raises(InvalidLinkTokenError):
        world.bind(token)


def test_an_unknown_token_is_refused() -> None:
    world = _World("tok-1")
    world.open()

    with pytest.raises(InvalidLinkTokenError):
        world.bind("not-a-token")


def test_a_chat_bound_to_one_account_cannot_be_taken_by_another() -> None:
    """Two people's movements in one conversation is a leak neither agreed to."""
    world = _World("tok-1", "tok-2")

    world.bind(world.open(USER))

    with pytest.raises(ChatAlreadyLinkedError):
        world.bind(world.open(OTHER_USER))


def test_a_refused_chat_is_told_why_where_it_is_looking() -> None:
    """The token is spent either way, so without this the tap is a dead end."""
    world = _World("tok-1", "tok-2")
    world.bind(world.open(USER))

    with pytest.raises(ChatAlreadyLinkedError):
        world.bind(world.open(OTHER_USER))

    assert world.sender.refusals == [CHAT]


def test_the_owner_never_comes_from_the_caller() -> None:
    """The token decides whose channel this is, and nothing else does."""
    world = _World("tok-1")
    token = world.open(USER)

    world.bind(token)

    assert len(world.list.execute(USER)) == 1
    assert world.list.execute(OTHER_USER) == []


# ----------------------------------------------------------------------
# Preferences and removal
# ----------------------------------------------------------------------


def test_a_preference_is_stored_and_read_back() -> None:
    world = _World("tok-1")
    world.bind(world.open())
    [view] = world.list.execute(USER)

    world.update.execute(
        UpdateChannelPreferenceCommand(
            user_id=USER,
            channel_id=view.channel_id,
            alert_type=AlertType.MOVEMENT,
            enabled=False,
            minimum_amount=None,
        ),
    )

    [updated] = world.list.execute(USER)
    assert updated.preferences[0].enabled is False


def test_the_view_carries_every_type_even_the_unspoken_ones() -> None:
    world = _World("tok-1")
    world.bind(world.open())

    [view] = world.list.execute(USER)

    assert {preference.alert_type for preference in view.preferences} == set(AlertType)


def test_another_account_cannot_touch_a_channel() -> None:
    world = _World("tok-1")
    world.bind(world.open(USER))
    [view] = world.list.execute(USER)

    with pytest.raises(ChannelNotFoundError):
        world.update.execute(
            UpdateChannelPreferenceCommand(
                user_id=OTHER_USER,
                channel_id=view.channel_id,
                alert_type=AlertType.MOVEMENT,
                enabled=False,
                minimum_amount=None,
            ),
        )


def test_deleting_frees_the_chat_for_somebody_else() -> None:
    world = _World("tok-1", "tok-2")
    world.bind(world.open(USER))
    [view] = world.list.execute(USER)

    world.delete.execute(
        DeleteChannelCommand(user_id=USER, channel_id=view.channel_id),
    )

    assert world.list.execute(USER) == []
    assert any(
        isinstance(event, AlertChannelRevoked) for event in world.publisher.events
    )

    world.bind(world.open(OTHER_USER))
    assert len(world.list.execute(OTHER_USER)) == 1


def test_deleting_something_that_is_not_yours_is_not_found() -> None:
    world = _World("tok-1")
    world.bind(world.open(USER))
    [view] = world.list.execute(USER)

    with pytest.raises(ChannelNotFoundError):
        world.delete.execute(
            DeleteChannelCommand(user_id=OTHER_USER, channel_id=view.channel_id),
        )


# ----------------------------------------------------------------------
# Delivering a movement
# ----------------------------------------------------------------------


def _alert(
    amount: str = "84300",
    *,
    origin: MovementOrigin = MovementOrigin.BANK_ALERT,
) -> MovementAlert:
    return MovementAlert(
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        direction=MovementDirection.OUTGOING,
        counterparty="COMPRA EN *PAYU*COL",
        bank="Bancolombia",
        occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
        origin=origin,
        unassigned=False,
    )


def _deliverer(
    world: _World, sender: object | None = None
) -> DeliverMovementAlertUseCase:
    return DeliverMovementAlertUseCase(
        channels=world.channels,
        deliveries=world.deliveries,
        sender=sender or world.sender,  # type: ignore[arg-type]
    )


def test_a_verified_channel_is_told() -> None:
    world = _World("tok-1")
    world.bind(world.open())

    sent = _deliverer(world).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(),
        ),
    )

    assert sent == 1
    assert world.sender.alerts[0].chat_id == CHAT
    assert world.sender.alerts[0].alert.counterparty == "COMPRA EN *PAYU*COL"


def test_a_pending_channel_is_not_told() -> None:
    world = _World("tok-1")
    world.open()

    sent = _deliverer(world).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(),
        ),
    )

    assert sent == 0
    assert world.sender.alerts == []


def test_the_same_event_delivered_twice_sends_once() -> None:
    """Delivery from the bus is at-least-once."""
    world = _World("tok-1")
    world.bind(world.open())
    deliverer = _deliverer(world)
    command = DeliverMovementAlertCommand(
        user_id=USER,
        event_id=uuid.uuid4(),
        alert=_alert(),
    )

    assert deliverer.execute(command) == 1
    assert deliverer.execute(command) == 0
    assert len(world.sender.alerts) == 1


def test_a_send_that_fails_is_not_recorded_as_delivered() -> None:
    """Marking after the send is what makes the retry actually retry."""
    world = _World("tok-1")
    world.bind(world.open())
    command = DeliverMovementAlertCommand(
        user_id=USER,
        event_id=uuid.uuid4(),
        alert=_alert(),
    )

    with pytest.raises(RuntimeError):
        _deliverer(world, ExplodingMessageSender()).execute(command)

    assert _deliverer(world).execute(command) == 1


def test_an_accrual_is_not_announced() -> None:
    """Interest the app itself computed, in bulk, while its owner watches."""
    world = _World("tok-1")
    world.bind(world.open())

    sent = _deliverer(world).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(origin=MovementOrigin.ACCRUAL),
        ),
    )

    assert sent == 0


def test_an_amount_under_the_floor_is_not_announced() -> None:
    world = _World("tok-1")
    world.bind(world.open())
    [view] = world.list.execute(USER)
    world.update.execute(
        UpdateChannelPreferenceCommand(
            user_id=USER,
            channel_id=view.channel_id,
            alert_type=AlertType.MOVEMENT,
            enabled=True,
            minimum_amount=Money(amount=Decimal("100000"), currency=Currency.COP),
        ),
    )

    sent = _deliverer(world).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert("84300"),
        ),
    )

    assert sent == 0


def test_only_the_owner_of_the_movement_is_told() -> None:
    world = _World("tok-1", "tok-2")
    world.bind(world.open(USER))
    world.bind(world.open(OTHER_USER), chat=ChatId("55554444"))

    _deliverer(world).execute(
        DeliverMovementAlertCommand(
            user_id=OTHER_USER,
            event_id=uuid.uuid4(),
            alert=_alert(),
        ),
    )

    assert [sent.chat_id for sent in world.sender.alerts] == [ChatId("55554444")]


def test_a_destination_that_refuses_does_not_stop_the_others() -> None:
    """A blocked chat is a person who said no, not a failed message."""

    class RefusingOnce(RecordingMessageSender):
        def __init__(self, refuse: ChatId) -> None:
            super().__init__()
            self._refuse = refuse

        def send_movement_alert(
            self,
            *,
            chat_id: ChatId,
            alert: MovementAlert,
        ) -> None:
            if chat_id == self._refuse:
                raise DestinationRefusedError("blocked")

            super().send_movement_alert(chat_id=chat_id, alert=alert)

    world = _World("tok-1", "tok-2")
    blocked = ChatId("111122223333")
    world.bind(world.open(USER), chat=blocked)
    world.bind(world.open(USER), chat=ChatId("444455556666"))

    sender = RefusingOnce(blocked)
    delivered = _deliverer(world, sender).execute(
        DeliverMovementAlertCommand(
            user_id=USER,
            event_id=uuid.uuid4(),
            alert=_alert(),
        ),
    )

    assert delivered == 1
    assert [sent.chat_id for sent in sender.alerts] == [ChatId("444455556666")]


def test_a_refused_destination_is_not_recorded_as_delivered() -> None:
    """If the owner unblocks the bot, the next redelivery reaches them."""

    class AlwaysRefusing(RecordingMessageSender):
        def send_movement_alert(
            self,
            *,
            chat_id: ChatId,
            alert: MovementAlert,
        ) -> None:
            raise DestinationRefusedError("blocked")

    world = _World("tok-1")
    world.bind(world.open())
    command = DeliverMovementAlertCommand(
        user_id=USER,
        event_id=uuid.uuid4(),
        alert=_alert(),
    )

    assert _deliverer(world, AlwaysRefusing()).execute(command) == 0
    assert _deliverer(world).execute(command) == 1
