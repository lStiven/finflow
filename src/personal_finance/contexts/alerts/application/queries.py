"""The read side: what a screen is allowed to know about a channel.

A view rather than the aggregate, for one reason worth stating: the token is
absent by construction. There is no field here it could travel in, so no
later change to a response model can accidentally hand a live credential back
a second time.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from typing import TYPE_CHECKING, Self

from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.ports import (
        AlertChannelRepository,
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ChannelView:
    """One channel, as its owner may see it.

    `chat_hint` is the destination's last few characters and never the whole
    of it. Nobody typed this in — the deep link bound it — so the tail tells
    its owner everything the full value would, while an address that receives
    somebody's purchases is worth handing out to no one.
    """

    channel_id: ChannelId
    kind: ChannelKind
    status: ChannelStatus
    chat_hint: str | None
    label: str | None
    created_at: PosixTime
    verified_at: PosixTime | None
    preferences: tuple[AlertPreference, ...]

    @classmethod
    def of(cls, channel: AlertChannel) -> Self:
        return cls(
            channel_id=channel.id,
            kind=channel.kind,
            status=channel.status,
            chat_hint=channel.chat_id.masked if channel.chat_id else None,
            label=channel.label,
            created_at=channel.created_at,
            verified_at=channel.verified_at,
            # Every type, not only the ones stored: a screen has to be able
            # to draw a switch for an alert nobody has yet expressed an
            # opinion about, and the default is the aggregate's answer to
            # give rather than the screen's to guess.
            preferences=tuple(
                channel.preferences.for_type(alert_type) for alert_type in AlertType
            ),
        )


class ListChannelsUseCase:
    def __init__(self, *, channels: AlertChannelRepository) -> None:
        self._channels = channels

    def execute(self, user_id: UserId) -> Sequence[ChannelView]:
        return [
            ChannelView.of(channel) for channel in self._channels.list_by_user(user_id)
        ]
