"""What an alert channel is made of, and the rule that decides delivery.

The one thing worth reading twice is `AlertPreferences.for_type`. It never
raises and never reports a missing entry: a type nobody has an opinion about
falls back to the opinion the type itself carries. That is what lets a new
`AlertType` member ship without rewriting a single stored row — the channel
written today has no entry for it, and the member decides its own default in
code rather than in the table.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Self
import uuid

from personal_finance.shared.domain.value_objects import (
    JsonValue,
    Money,
    ValueObject,
)


class ChannelKind(enum.Enum):
    """Which transport a channel speaks.

    One member today. It exists as an enum rather than as an assumption so
    that the day a second transport arrives, every stored row already says
    which one it is instead of meaning Telegram by omission.
    """

    TELEGRAM = "telegram"


class ChannelStatus(enum.Enum):
    """Whether the destination has proved it is really the owner's.

    Deliberately no `REVOKED`: unlinking deletes the channel. A tombstone
    would be a row that can never alert again and that nothing ever reads,
    and keeping one would only invite somebody to resurrect it.
    """

    PENDING = "pending"
    VERIFIED = "verified"


class AlertType(enum.Enum):
    """What a channel can be told about.

    Explicit string values because they are persisted. `MOVEMENT` is every
    purchase and income as it happens; `WEEKLY_SUMMARY` is Monday's look back
    at the week before, against its owner's own normal.
    """

    MOVEMENT = "movement"
    WEEKLY_SUMMARY = "weekly_summary"

    @property
    def enabled_by_default(self) -> bool:
        """Whether a channel that has never said anything about this wants it.

        Read whenever a stored channel has no preference for a type — which
        is every channel, for every type added after it was written. Defaulting
        to on is what makes a new alert reach the people who already linked a
        channel instead of only the ones who link one afterwards.
        """
        return True


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelId(ValueObject):
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid channel id: {value!r}") from error

    def to_dict(self) -> JsonValue:
        return str(self.value)


@dataclasses.dataclass(frozen=True, slots=True)
class ChatId(ValueObject):
    """Where a transport delivers — a Telegram chat, for now.

    Kept as text rather than as a number so the key never depends on how an
    integer is spelled, and so another transport's address fits without a
    second type.

    It redacts itself down to the last four characters, and that is what the
    API returns too. Nobody types this in — the deep link binds it — so the
    full value tells its owner nothing the tail does not, while an address
    that receives somebody's purchases is worth handing out to no one.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Chat id cannot be empty")

    @property
    def masked(self) -> str:
        return f"…{self.value[-4:]}"

    def to_dict(self) -> JsonValue:
        return self.masked


@dataclasses.dataclass(frozen=True, slots=True)
class SecretHash(ValueObject):
    """The hash of a link token.

    Alerts keeps its own rather than importing Identity's: a dozen lines is
    the price of the two contexts staying able to change independently, and
    it is the same trade the inbound payload models already make.

    Deterministic on purpose — this value *is* a partition key, so it has to
    hash the same way twice. And never printed, so it redacts itself.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Secret hash cannot be empty")

    def to_dict(self) -> JsonValue:
        return "<redacted>"


@dataclasses.dataclass(frozen=True, slots=True)
class AlertPreference(ValueObject):
    """One type of alert, and how much of it this channel wants."""

    alert_type: AlertType
    enabled: bool
    minimum_amount: Money | None = None

    @classmethod
    def default_for(cls, alert_type: AlertType) -> Self:
        return cls(alert_type=alert_type, enabled=alert_type.enabled_by_default)

    def admits(self, amount: Money) -> bool:
        """Whether an alert of this size is worth sending.

        The amount is unsigned — `Money` refuses a negative — so a floor
        tuned to stop small purchases also stops small income. That is the
        intended reading of "no avisar por debajo de X".

        A floor in one currency says nothing about a movement in another, and
        the codebase refuses cross-currency arithmetic everywhere else for
        good reason. When the filter cannot be evaluated the safe direction
        for an *alert* is to deliver: a message too many is noise, a message
        too few is a purchase nobody heard about.
        """
        if not self.enabled:
            return False

        if self.minimum_amount is None:
            return True

        if self.minimum_amount.currency is not amount.currency:
            return True

        return amount.amount >= self.minimum_amount.amount


@dataclasses.dataclass(frozen=True, slots=True)
class AlertPreferences(ValueObject):
    """Everything a channel has said about what it wants.

    Only opinions actually expressed are stored. Silence is not "off" — it is
    answered by the type's own default, which is what keeps a new alert type
    from needing a migration.
    """

    entries: tuple[AlertPreference, ...] = ()

    @classmethod
    def none_expressed(cls) -> Self:
        return cls()

    def for_type(self, alert_type: AlertType) -> AlertPreference:
        for entry in self.entries:
            if entry.alert_type is alert_type:
                return entry

        return AlertPreference.default_for(alert_type)

    def with_updated(self, preference: AlertPreference) -> Self:
        kept = tuple(
            entry
            for entry in self.entries
            if entry.alert_type is not preference.alert_type
        )

        return type(self)(entries=(*kept, preference))

    def to_dict(self) -> JsonValue:
        return [entry.to_dict() for entry in self.entries]
