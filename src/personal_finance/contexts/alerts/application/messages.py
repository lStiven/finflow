"""The facts an alert is about, in this context's own vocabulary.

Alerts learns about money moving from the bus and nowhere else, so these
mirror the wire payload rather than Financial's domain types — the same
reason the inbound envelope models are declared here instead of imported.
Neither context gets to change the other's meaning by editing its own.

Nothing here holds words. What a message *says* is the transport adapter's
job, because a message to Telegram and a message to anything else would say
it differently; what a message is *about* is this.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
import enum

from personal_finance.shared.domain.value_objects import Currency, Money, PosixTime


class MovementDirection(enum.Enum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class MovementOrigin(enum.Enum):
    """Who recorded the movement, which decides whether it is news.

    A bank alert and a hand-written entry are both things that happened to
    the owner. An accrual is not: it is what this app computed from terms the
    owner declared, and it lands in bulk the moment somebody opens a credit
    screen and presses refresh. Announcing four interest charges to a phone
    while their owner is watching them appear on screen is the kind of noise
    that gets alerts switched off altogether — taking the useful ones with
    them.

    A scheduled charge — a declared bill, confirmed — is news, and that is
    why it is not folded into `ACCRUAL` however much the two resemble each
    other. Both are money this app wrote without a bank announcing it; the
    difference is that nobody is watching a bill get charged, and the moment
    it does is exactly when its owner wants to hear about it.

    **This member has to exist here before Financial starts publishing it.**
    An origin this cannot read is a message refused as malformed, retried
    five times and sent to the dead-letter queue within minutes — the same
    family as the bug of 14 September, where two contexts quietly stopped
    agreeing about a payload.
    """

    BANK_ALERT = "bank_alert"
    MANUAL = "manual"
    ACCRUAL = "accrual"
    SCHEDULED = "scheduled"

    @property
    def is_news_to_the_owner(self) -> bool:
        return self is not MovementOrigin.ACCRUAL


class BudgetState(enum.Enum):
    """Green, amber or red, as Financial decided it. Never recomputed here."""

    OK = "ok"
    WARNING = "warning"
    OVER = "over"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetStanding:
    """One budget the movement counts against, and how the month stands.

    Plain figures rather than `Money`: `remaining` goes negative once the cap
    is passed, and `Money` refuses a negative — rightly, for an amount that
    moved, and wrongly for a distance to a ceiling.
    """

    name: str
    currency: Currency
    limit: Decimal
    spent: Decimal
    #: The cap less what is spent; negative once it was passed.
    remaining: Decimal
    state: BudgetState


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MovementAlert:
    """Money moved, and everything an alert may say about it.

    `occurred_at` is when the money moved, not when the fact was recorded.
    The two differ by days when a bank alert arrives late, and a message
    saying "acabas de gastar" about a three-day-old purchase is wrong.
    """

    amount: Money
    direction: MovementDirection
    counterparty: str
    bank: str
    occurred_at: PosixTime
    origin: MovementOrigin
    unassigned: bool
    #: Financial's id for the movement, so a screen can open it. None only
    #: for a payload published before Financial carried it.
    movement_id: str | None = None
    #: The budgets this movement counts against — empty for most movements,
    #: and filled by the use case, never by the payload.
    budgets: tuple[BudgetStanding, ...] = ()

    @property
    def is_worth_announcing(self) -> bool:
        return self.origin.is_news_to_the_owner


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CategoryRise:
    """The category that went up the most against its owner's own normal."""

    category: str
    #: The owner's name for a category they wrote; the API's English label for
    #: a shipped one, which the message restates from `category`.
    label: str
    spent: Decimal
    typical: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WeeklySummary:
    """One week of spending against the weeks before it, in one currency.

    `typical` is None in somebody's first week: there is no normal yet, and
    the message says so rather than comparing against zero.
    """

    week_start: dt.date
    #: Sunday, inclusive.
    week_end: dt.date
    currency: Currency
    spent: Decimal
    movements: int
    typical: Decimal | None
    rise: CategoryRise | None
