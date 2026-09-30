from __future__ import annotations

import dataclasses
import uuid

from personal_finance.contexts.alerts.application.messages import MovementAlert
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertType,
    ChannelId,
    ChannelKind,
    ChatId,
)
from personal_finance.shared.domain.value_objects import Money, PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CreateChannelCommand:
    user_id: UserId
    kind: ChannelKind


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RedeemChannelLinkCommand:
    """What the webhook carries, once it has been believed.

    No user id: the token is the identity. Reading the owner off anything
    the caller supplied would let whoever is holding the link decide whose
    account it binds.
    """

    token: str
    chat_id: ChatId
    label: str | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UpdateChannelPreferenceCommand:
    user_id: UserId
    channel_id: ChannelId
    alert_type: AlertType
    enabled: bool
    minimum_amount: Money | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeleteChannelCommand:
    user_id: UserId
    channel_id: ChannelId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeliverMovementAlertCommand:
    """One movement, to every channel of one account that wants it.

    `event_id` travels from the integration event and is what makes a
    redelivery quiet. It is per event, not per movement: the same movement
    edited later is a different fact and deserves to be announced again.
    """

    user_id: UserId
    event_id: uuid.UUID
    alert: MovementAlert
    #: When Financial recorded the fact — the envelope's own time, the same on
    #: every redelivery, so the in-app entry lands in one place. None falls
    #: back to when this worker first sees it.
    recorded_at: PosixTime | None = None
