from __future__ import annotations

import dataclasses

from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelId,
    ChannelKind,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AlertChannelEvent(Event):
    """Every channel fact belongs to exactly one user.

    None of these reach EventBridge. Alerts is a leaf: it subscribes to
    `finflow.financial` and publishes nothing, so there is no translator in
    `application/` and these go to the logging publisher alone. That is not
    an omission — not writing a translator is precisely the mechanism by
    which a context keeps its lifecycle to itself.

    They carry the channel and its transport, never the chat id and never a
    token. A record of who linked what belongs in a log; an address that
    receives somebody's spending does not.
    """

    channel_id: ChannelId
    user_id: UserId
    kind: ChannelKind


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AlertChannelVerified(AlertChannelEvent):
    """A destination proved it is the owner's and started receiving."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AlertChannelRevoked(AlertChannelEvent):
    """The owner unlinked a destination. Nothing more is sent to it."""
