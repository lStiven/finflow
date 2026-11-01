"""Whether the model still reads one person's unrecognised mail.

The abuse rule, kept apart from the daily cap on purpose: the cap limits what
anybody spends and punishes nobody, while this answers somebody pointing the
reader at mail that is not from a bank. Ten such emails that *arrived* within
an hour are a strike — counted by arrival, because a queue that backed up and
drained at once would otherwise strike a burst that never happened. The first
strike pauses the model for a day, the second for three, the third ends it
until somebody reinstates it by hand; a strike stops counting 90 days after
it was given. Templates are never suspended: they cost nothing.

Only an email the model itself judged not to come from a bank is an offense.
A decline is not: the prompt refuses one-time codes, statements, marketing
and authorizations, and sender approval admits a bank's whole domain, so
counting declines would strike somebody for an afternoon of ordinary online
banking.
"""

from __future__ import annotations

from collections.abc import Iterable
import dataclasses
import datetime
import enum
from typing import Self

from personal_finance.contexts.ingestion.domain.events import ModelReadingSuspended
from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId


OFFENSES_PER_STRIKE = 10
#: Strictly less than this between the first and the last of a burst.
OFFENSE_WINDOW = datetime.timedelta(hours=1)
STRIKE_LIFETIME = datetime.timedelta(days=90)
#: How long the pause after each strike lasts; the strike after the last of
#: these has no end.
PAUSES = (datetime.timedelta(days=1), datetime.timedelta(days=3))
LAST_STRIKE = len(PAUSES) + 1
#: How long an offense is remembered, by when it arrived. Long enough for a
#: backlog cleared after an outage to still assemble its burst; short enough
#: that the record stays a handful of entries.
OFFENSE_MEMORY = datetime.timedelta(days=2)


class ModelAccess(enum.Enum):
    """Explicit values: the API publishes them."""

    OPEN = "open"
    PAUSED = "paused"
    REVOKED = "revoked"


class OffenseOutcome(enum.Enum):
    #: Nothing changed: already counted, or the model is suspended.
    IGNORED = "ignored"
    COUNTED = "counted"
    #: It completed a burst, and a strike was given.
    STRUCK = "struck"


@dataclasses.dataclass(eq=False, slots=True, kw_only=True)
class ModelStanding(AggregateRoot[UserId]):
    """One person's record with the model. Its id is theirs.

    `version` is the optimistic lock: two parse workers can read the same
    person's mail at once, and only one of them may turn a burst into a
    strike.
    """

    # When each remembered offense arrived, by the email that made it — so a
    # redelivered message is the same offense, not a second one.
    offenses: dict[NotificationId, PosixTime] = dataclasses.field(
        default_factory=lambda: dict[NotificationId, PosixTime](),
    )
    #: When each strike still on the record was given.
    strikes: tuple[PosixTime, ...] = ()
    paused_until: PosixTime | None = None
    revoked: bool = False
    version: int = 0

    @classmethod
    def clean(cls, user_id: UserId) -> Self:
        return cls(id=user_id)

    @property
    def user_id(self) -> UserId:
        return self.id

    def access(self, now: PosixTime) -> ModelAccess:
        if self.revoked:
            return ModelAccess.REVOKED

        if self.paused_until is not None and _before(now, self.paused_until):
            return ModelAccess.PAUSED

        return ModelAccess.OPEN

    def standing_strikes(self, now: PosixTime) -> tuple[PosixTime, ...]:
        """The strikes given within the last 90 days — the ones that count."""
        expired_before = _shift(now, -STRIKE_LIFETIME)

        return tuple(
            given for given in self.strikes if not _before(given, expired_before)
        )

    def record_offense(
        self,
        *,
        notification_id: NotificationId,
        received_at: PosixTime,
        now: PosixTime,
    ) -> OffenseOutcome:
        """Remember one email that was not from a bank. A strike also records
        `ModelReadingSuspended`.

        Nothing is remembered while the model is suspended: an email read just
        before the suspension began was already in flight, and letting it
        count would hand out the next strike for the same afternoon.
        """
        if self.access(now) is not ModelAccess.OPEN:
            return OffenseOutcome.IGNORED

        if notification_id in self.offenses:
            return OffenseOutcome.IGNORED

        forgotten_before = _shift(now, -OFFENSE_MEMORY)
        self.offenses = {
            key: arrived
            for key, arrived in {**self.offenses, notification_id: received_at}.items()
            if not _before(arrived, forgotten_before)
        }
        burst = _largest_burst(self.offenses.values())

        if burst < OFFENSES_PER_STRIKE:
            return OffenseOutcome.COUNTED

        self._strike(now=now, offenses=burst)

        return OffenseOutcome.STRUCK

    def reinstate(self) -> bool:
        """Start over: no suspension, no strikes, nothing remembered.

        What reactivating somebody means. Keeping their strikes would make
        the next burst — even a year from now — end the model again at once.
        Returns whether there was anything to clear.
        """
        changed = (
            self.revoked
            or self.paused_until is not None
            or bool(self.strikes)
            or bool(self.offenses)
        )
        self.revoked = False
        self.paused_until = None
        self.strikes = ()
        self.offenses = {}

        return changed

    def _strike(self, *, now: PosixTime, offenses: int) -> None:
        standing = self.standing_strikes(now)
        strike = min(len(standing) + 1, LAST_STRIKE)
        self.strikes = (*standing, now)
        # A burst is answered once: what made this strike cannot make the next.
        self.offenses = {}

        if strike >= LAST_STRIKE:
            self.revoked = True
            self.paused_until = None
        else:
            self.paused_until = _shift(now, PAUSES[strike - 1])

        self.record_event(
            ModelReadingSuspended(
                user_id=self.user_id,
                strike=strike,
                paused_until=self.paused_until,
                offenses=offenses,
                occurred_at=now,
            ),
        )


def _largest_burst(arrivals: Iterable[PosixTime]) -> int:
    """The most offenses that arrived within one window of each other."""
    moments = sorted(moment.as_epoch_seconds() for moment in arrivals)
    window = int(OFFENSE_WINDOW.total_seconds())
    largest = 0
    first = 0

    for last, moment in enumerate(moments):
        while moment - moments[first] >= window:
            first += 1

        largest = max(largest, last - first + 1)

    return largest


def _before(moment: PosixTime, other: PosixTime) -> bool:
    return moment.to_datetime() < other.to_datetime()


def _shift(moment: PosixTime, delta: datetime.timedelta) -> PosixTime:
    return PosixTime.from_datetime(moment.to_datetime() + delta)
