"""A ceiling the owner puts on one category, and how close the month is to it.

Deliberately **not** the method of assigning every peso somewhere. That is
YNAB's, and it works — but it asks somebody to sit down and allocate their
whole income before the app is worth anything, which is the entry curve this
app is trying not to have. A cap is the opposite trade: one category, one
number, and it starts being useful the moment it is written.

**Nothing here is money that moved.** A budget writes nothing to the ledger,
moves no balance and publishes no event. It is a number, a currency and the
months it governs. Like an account and like the monthly plan: *declared, never
discovered.*

**What is spent against a cap is never stored here.** It is read off the ledger
every time, through the same grouping that draws the breakdown on the summary
screen. The reason is the one the bills module already gives about "paid": a
second copy of the answer would be the copy that survives somebody editing or
erasing a movement, and nothing in this app schedules a repair. It also makes
the cap retroactively honest for free — renaming a merchant or filing it under
another category re-attributes every past movement, and the budget follows.

**And crossing a cap is derived too, not announced.** The plan this came from
expected a `BudgetThresholdCrossed` event on the way to Telegram. It cannot
work yet, and the reason is worth writing down rather than rediscovering: a
movement has no category *at the moment it is recorded*. Financial stores the
counterparty text the bank wrote and joins it to a merchant when the answer is
read — that join is what makes a correction retroactive, and it is also why
nothing at write time knows which cap a purchase belongs to. So the crossing
is computed on the way out, in `progress`, and the warning it produces is one a
screen shows rather than one a phone rings for. Announcing it needs a trigger
that walks users, which this deployment does not have.

Two shapes of cap, because the owner picks:

* **Every month** (`month=None`) — the ordinary one. A number that keeps
  meaning what it meant without anybody re-declaring it every 1st.
* **One month** (`month="2026-09"`) — December, when the rules are different.
  It **shadows** the recurring cap for exactly that month, which is what makes
  the exception an exception rather than an edit that outlives its reason.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
import enum
import re
from typing import Self

from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
    ValueObject,
)


# Where the bar turns amber by default. Eighty is the number the market
# research quotes, and it is a default rather than a constant because the
# useful warning point depends on the category: eighty per cent of the rent is
# news about nothing, and eighty per cent of what somebody allows themselves
# for restaurants is news on the 12th.
DEFAULT_WARN_PERCENT = 80

# Neither end is a warning. At a hundred the amber band is empty — the cap and
# the warning are the same event — and at zero everything is amber from the
# first peso, which is a bar that has never once been green.
MIN_WARN_PERCENT = 1
MAX_WARN_PERCENT = 99

# A month key as the rest of this context spells it: `2026-09`. The same shape
# `SummarizeSpendingUseCase` buckets by, so a cap and the spending it is
# compared against name the month identically.
#
# Read with `fullmatch` everywhere, never `match`: Python's `$` also matches
# *before* a trailing newline, so `match` would let `2026-09\n` through and
# store it as a month key nothing else spells that way. The router's own
# Pydantic `pattern` refuses it — two validators of one rule disagreeing is a
# rule with a gap in it, waiting for a second caller.
MONTH_KEY = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")

# Stands where the month key goes for a cap that governs all of them. Cannot
# collide with a real month: `MONTH_KEY` only ever matches digits and a dash.
EVERY_MONTH = "EVERY"

# Long past any category value this can hold — the shipped ones are single
# words and a user's own is `custom:` plus thirty-two hex characters — and
# short enough that a cap's identity stays a key somebody can read in a log.
MAX_CATEGORY_LENGTH = 64


class BudgetState(enum.Enum):
    """How a month is doing against one cap.

    The three the screen draws, decided here and not there, so the card on the
    dashboard and the row on the budgets screen can never disagree about the
    same category.

    Explicit string values: they cross the HTTP boundary as the enum itself,
    so reordering the members must not change what a client reads.
    """

    OK = "ok"
    WARNING = "warning"
    OVER = "over"


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetId(ValueObject):
    """What identifies one cap inside its owner's budgets.

    The category it governs, and the month when it governs only one. **No
    generated id**, for the reason `MonthlyPlan` has none: two caps on the same
    category for the same month are not two caps, they are one cap declared
    twice. An id would make a contradiction storable and leave the screen to
    pick a winner.
    """

    category: str
    #: None means every month. A key like `2026-09` means that one only, and
    #: it shadows the recurring cap while it lasts.
    month: str | None = None

    @property
    def month_key(self) -> str:
        """The month as a storage key, with `EVERY` standing for all of them.

        One string rather than a nullable one, because this is what goes into
        a sort key and a sort key cannot be absent.
        """
        return self.month or EVERY_MONTH


@dataclasses.dataclass(eq=False, slots=True)
class CategoryBudget(AggregateRoot[BudgetId]):
    """A cap on one category, in one currency, for one month or all of them.

    One currency, like the monthly plan and for the same reason: comparing a
    cap against spending in another unit needs a rate this app has no business
    inventing. Somebody who spends in two currencies declares two caps.
    """

    user_id: UserId
    limit: Money
    #: The share of the cap at which the bar turns amber, in per cent.
    warn_at: int = DEFAULT_WARN_PERCENT
    updated_at: PosixTime = dataclasses.field(default_factory=PosixTime.now)

    @classmethod
    def declare(
        cls,
        *,
        user_id: UserId,
        category: str,
        limit: Money,
        month: str | None = None,
        warn_at: int = DEFAULT_WARN_PERCENT,
    ) -> Self:
        """Put a cap on a category, or restate the one that is there.

        There is no separate «amend», like the monthly plan has none: a cap is
        a number somebody guesses and then corrects, so replacing it whole is
        the honest operation and it makes it impossible to leave a warning
        point standing against a ceiling it was never set against.

        No event. A ceiling somebody sets on their own spending is a statement
        about their life, not about money that moved, and the module docstring
        says why the *crossing* is not an event either.
        """
        return cls(
            id=BudgetId(
                category=keyable_category(category),
                month=valid_month(month),
            ),
            user_id=user_id,
            limit=_cappable(limit),
            warn_at=_warnable(warn_at),
        )

    @property
    def category(self) -> str:
        """The category, spelled out.

        `id` already carries it, but every other aggregate here reads a named
        field and code that has to remember which one this class uses is code
        that will get it wrong.
        """
        return self.id.category

    @property
    def month(self) -> str | None:
        return self.id.month

    @property
    def recurring(self) -> bool:
        """Whether this cap governs every month rather than just one."""
        return self.id.month is None

    @property
    def currency(self) -> Currency:
        return self.limit.currency

    @property
    def warning_at(self) -> Decimal:
        """The amount at which the bar turns amber.

        Derived rather than stored: it is the cap and the share, and a stored
        copy would be the one that survives somebody moving either.
        """
        return self.limit.amount * Decimal(self.warn_at) / Decimal(100)

    def progress(self, spent: Decimal) -> BudgetProgress:
        """How this month is doing, given what the ledger says went out.

        `spent` comes from outside because it is a question for a repository,
        and it is the *outgoing* figure and never the net one: a refund inside
        the category would make the net read as under budget while the money
        did leave and came back, which is two facts and not none.

        Nothing is floored. Somebody who went over needs to see by how much,
        so `remaining` is reported negative exactly the way the allowance is.
        """
        return BudgetProgress(
            category=self.category,
            currency=self.currency,
            limit=self.limit.amount,
            spent=spent,
            remaining=self.limit.amount - spent,
            warn_at=self.warn_at,
            state=self._state(spent),
            month=self.month,
            recurring=self.recurring,
        )

    def _state(self, spent: Decimal) -> BudgetState:
        """Green, amber or red — and red only where there is something to do.

        The order matters: over is checked first, so a cap whose warning point
        somebody set to 99 still goes red rather than staying amber past it.
        """
        if spent >= self.limit.amount:
            return BudgetState.OVER

        if spent >= self.warning_at:
            return BudgetState.WARNING

        return BudgetState.OK


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetProgress:
    """One cap and what the month has done to it.

    Every figure the screen needs to show the comparison rather than assert
    its result: a bar somebody cannot check against their own head is a bar
    they stop believing the first time it disagrees.

    Deliberately flat — the cap's fields are copied in rather than nested —
    because this is what the endpoint answers, and a client reading a ceiling
    out of one object and a state out of another is a client that can render
    the two out of step.
    """

    category: str
    currency: Currency
    limit: Decimal
    #: What went out of this category this month, transfers excluded.
    spent: Decimal
    #: The cap less what is spent. Negative when it was passed, and reported
    #: that way rather than floored at zero.
    remaining: Decimal
    warn_at: int
    state: BudgetState
    #: The month this cap is declared for, or None when it governs all of them.
    month: str | None
    recurring: bool


def keyable_category(category: str) -> str:
    """A category value that can safely be part of an identity.

    Public because dropping a cap asks the same question as declaring one, and
    two spellings of "what may be half of an identity" is how the refusal ends
    up living in only one of the two callers.

    Whether it names anything is not settled here — the vocabulary is
    Merchant's and half of it is whatever this person wrote, so that answer
    comes through a port at the boundary. What is settled here is that the
    value can be a key: `#` separates the segments of a sort key, so a
    category carrying one would be two categories that store as one row.
    """
    stripped = category.strip()

    if not stripped:
        raise ValueError("A budget needs a category to cap")

    if len(stripped) > MAX_CATEGORY_LENGTH:
        raise ValueError(
            f"A category cannot exceed {MAX_CATEGORY_LENGTH} characters",
        )

    if "#" in stripped:
        raise ValueError(f"A category cannot contain '#': {stripped!r}")

    return stripped


def valid_month(month: str | None) -> str | None:
    """`None` for every month, or one spelled the way this context spells it.

    Refused rather than coerced. `2026-9` and `sep-2026` are both somebody
    meaning September, and both would store as a month that no spending is
    ever bucketed under — a cap that silently governs nothing, which looks
    exactly like a cap nothing has been spent against.

    Public because reading a month of budgets asks the same question as
    declaring one, and two spellings of "how a month is written" is how the
    cap and the spending it is compared against end up in different months.
    """
    if month is None:
        return None

    if not MONTH_KEY.fullmatch(month):
        raise ValueError(f"A budget month is written like 2026-09: {month!r}")

    return month


def month_containing(day: dt.date) -> str:
    """Which month a calendar day belongs to, spelled the one way.

    The day has to be read in the owner's own zone before it gets here: in UTC
    the last evening of a Bogotá month is already the next one, and a cap that
    governs the wrong month is wrong on the 1st — the day it is most likely to
    be looked at.
    """
    return f"{day.year:04d}-{day.month:02d}"


def month_bounds(month: str) -> tuple[dt.date, dt.date]:
    """The first and last calendar day of a month, both inclusive.

    Built by stepping to the first of the following month and taking a day off
    it, so February needs no table and a leap year needs no special case.
    """
    if not MONTH_KEY.fullmatch(month):
        raise ValueError(f"A budget month is written like 2026-09: {month!r}")

    year, index = (int(part) for part in month.split("-"))
    first = dt.date(year, index, 1)
    following = dt.date(year + 1, 1, 1) if index == 12 else dt.date(year, index + 1, 1)

    return first, following - dt.timedelta(days=1)


def _cappable(limit: Money) -> Money:
    """`Money` already refuses a negative. Zero is refused here.

    A cap of nothing is red before a peso is spent, which is not a warning
    anybody can act on. "I do not want to spend here at all" is a real
    intention and a cap is the wrong way to say it: every month would open
    already over, and a bar that is never once green is a bar nobody reads.
    """
    if limit.amount == 0:
        raise ValueError("A budget cannot be for nothing")

    return limit


def _warnable(warn_at: int) -> int:
    """The amber point, strictly inside the cap.

    Both ends are refused rather than clamped. At a hundred there is no amber
    band at all — the warning and the ceiling are one event — and at zero the
    bar is amber from the first peso, which is the same as having no warning.
    """
    if not MIN_WARN_PERCENT <= warn_at <= MAX_WARN_PERCENT:
        raise ValueError(
            f"A budget warns between {MIN_WARN_PERCENT}% and "
            f"{MAX_WARN_PERCENT}% of its limit: {warn_at}",
        )

    return warn_at
