"""The channel a person bound, and the one rule that decides delivery.

`should_deliver` is the whole decision. It reads status and preference and
nothing else — no clock, no transport, no repository — which is what makes
the question answerable in a unit test and answerable the same way from the
worker and from anywhere else that ever needs to ask it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self
import unicodedata

from personal_finance.contexts.alerts.domain.events import (
    AlertChannelRevoked,
    AlertChannelVerified,
)
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertPreferences,
    AlertType,
    ChannelId,
    ChannelKind,
    ChannelStatus,
    ChatId,
    SecretHash,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import Money, PosixTime, UserId


MAX_LABEL_LENGTH = 64

# The right-to-left override. It reverses how everything after it is drawn,
# which is the classic way to make one string display as another — and this
# one arrives from a stranger's Telegram profile and is shown back to the
# owner. Stripped rather than escaped: there is no legitimate name that needs
# to re-order its own rendering.
_RTL_OVERRIDE = "\u202e"


def _valid_label(label: str | None) -> str | None:
    """What a destination may be called, after a stranger chose it.

    The label comes from a Telegram profile, so it is untrusted text that a
    person later reads next to their own finances. Control characters and the
    bidirectional overrides go, the rest is trimmed to something that fits on
    one line. An empty result is no label at all rather than a blank one.
    """
    if label is None:
        return None

    cleaned = "".join(
        character
        for character in label.replace(_RTL_OVERRIDE, "")
        if unicodedata.category(character) not in {"Cc", "Cf"}
    ).strip()

    if not cleaned:
        return None

    return cleaned[:MAX_LABEL_LENGTH]


@dataclass(slots=True)
class AlertChannel(AggregateRoot[ChannelId]):
    """One destination a person asked to be told things at.

    Starts `PENDING` with no address: creating a channel is asking for a link,
    and until somebody presses Start in Telegram there is nowhere to send. The
    invariant that follows — `VERIFIED` implies a chat id — is what lets the
    delivery path treat a verified channel as sendable without re-checking.
    """

    user_id: UserId
    kind: ChannelKind
    status: ChannelStatus
    created_at: PosixTime
    preferences: AlertPreferences = field(
        default_factory=AlertPreferences.none_expressed,
    )
    chat_id: ChatId | None = None
    label: str | None = None
    verified_at: PosixTime | None = None
    pending_link_hash: SecretHash | None = None

    def __post_init__(self) -> None:
        if self.status is ChannelStatus.VERIFIED and self.chat_id is None:
            raise ValueError("A verified channel must have somewhere to send")

        self.label = _valid_label(self.label)

    @classmethod
    def pending(
        cls,
        *,
        user_id: UserId,
        kind: ChannelKind,
        link_hash: SecretHash,
        now: PosixTime,
    ) -> Self:
        """A channel that exists only to be linked.

        It holds the hash of the one link that can bind it — the same reason
        identity's `PasswordResetWindow` holds the hash of the live reset
        link. Without it there is no way to retire a link except to wait for
        it to expire, and "ask me again" would leave a trail of live tokens
        behind it.
        """
        return cls(
            id=ChannelId.new(),
            user_id=user_id,
            kind=kind,
            status=ChannelStatus.PENDING,
            created_at=now,
            pending_link_hash=link_hash,
        )

    @property
    def is_verified(self) -> bool:
        return self.status is ChannelStatus.VERIFIED

    def verify(
        self,
        *,
        chat_id: ChatId,
        label: str | None,
        now: PosixTime,
    ) -> None:
        """Bind the destination that proved it holds the link token.

        A repeat with the *same* chat is a no-op rather than an error: the
        webhook is redelivered by Telegram as a matter of course, and the
        second arrival has changed nothing. A repeat with a *different* chat
        is refused — that would move somebody's purchases to another
        destination, which is the one thing linking must never do quietly.
        """
        if self.is_verified:
            if self.chat_id == chat_id:
                return

            raise ChannelAlreadyVerifiedError(
                "This channel is already bound to another chat",
            )

        self.status = ChannelStatus.VERIFIED
        # The link that got here is spent; nothing may bind this channel again.
        self.pending_link_hash = None
        self.chat_id = chat_id
        self.label = _valid_label(label)
        self.verified_at = now
        self.record_event(
            AlertChannelVerified(
                channel_id=self.id,
                user_id=self.user_id,
                kind=self.kind,
            ),
        )

    def update_preference(self, preference: AlertPreference) -> None:
        self.preferences = self.preferences.with_updated(preference)

    def revoke(self) -> None:
        self.record_event(
            AlertChannelRevoked(
                channel_id=self.id,
                user_id=self.user_id,
                kind=self.kind,
            ),
        )

    def should_deliver(self, *, alert_type: AlertType, amount: Money) -> bool:
        """Whether this fact is worth waking this channel for."""
        return self.is_verified and self.preferences.for_type(alert_type).admits(
            amount,
        )
