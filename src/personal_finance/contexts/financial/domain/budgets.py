"""A ceiling the owner puts on part of their spending, and how a month is
doing against it.

Deliberately **not** the method of assigning every peso somewhere. That is
YNAB's, and it works — but it asks somebody to sit down and allocate their
whole income before the app is worth anything, which is the entry curve this
app is trying not to have. A cap is the opposite trade: a number, what it
watches, and it starts being useful the moment it is written.

**Nothing here is money that moved.** A budget writes nothing to the ledger,
moves no balance and publishes no event. Like an account and like the monthly
plan: *declared, never discovered.*

**What is spent against a cap is never stored here.** It is read off the ledger
every time, through the same grouping that draws the breakdown on the summary
screen. The reason is the one the bills module already gives about "paid": a
second copy of the answer would be the copy that survives somebody editing or
erasing a movement, and nothing in this app schedules a repair. It also makes
the cap retroactively honest for free — renaming a merchant or filing it under
another category re-attributes every past movement, and the budget follows.

**And crossing a cap is derived too, not announced.** A movement has no
category *at the moment it is recorded*: Financial stores the counterparty text
the bank wrote and joins it to a merchant when the answer is read — that join
is what makes a correction retroactive, and it is also why nothing at write
time knows which category a purchase belongs to. So the crossing is computed on
the way out, in `progress`.

That last paragraph has one exception, and it is the reason `BudgetScope`
exists in the shape it does: **a budget that watches every category needs no
category**, so it *can* be judged at write time. The alert this module could
never ring becomes possible for exactly that kind of cap.

What a budget watches
---------------------

A `BudgetScope` — categories and accounts, each of which is *every* one of them
when left empty. That covers the four cases anybody actually asks for with one
field instead of four flags: the whole month, one category, a handful of them
together («salidas» is restaurants and bars and delivery), and any of those
narrowed to a card.

Identity
--------

**A generated id**, and this reverses what this module used to say. The cap
used to *be* its category and its month, on the argument that two caps on the
same category are one cap declared twice. That argument only holds while a cap
watches exactly one category: once «Salidas» and «Restaurantes» can both exist,
overlapping on purpose, two caps on the same category are two caps. The
uniqueness that identity used to enforce is gone deliberately, not lost.

The rule it took with it is **shadowing**. A cap for one month used to hide the
recurring one for its category; with overlapping scopes there is no honest
answer to which of two caps hides which, so every cap that governs a month now
applies. December's exception is declared as its own budget and read alongside
the usual one.

Having an id is also what makes `amend` possible, and therefore necessary: when
the identity was the content, editing a cap meant replacing it, and there was
nothing to rename.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
import enum
import re
from typing import Self
import uuid

from personal_finance.contexts.financial.domain.value_objects import AccountId
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
# useful warning point depends on what is watched: eighty per cent of the rent
# is news about nothing, and eighty per cent of what somebody allows themselves
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
# store it as a month key nothing else spells that way.
MONTH_KEY = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")

# Long past any category value this can hold — the shipped ones are single
# words and a user's own is `custom:` plus thirty-two hex characters.
MAX_CATEGORY_LENGTH = 64

# A budget's name is read in a list, under a bar, on a phone. Long enough for
# «Salidas de fin de semana», short enough that it never decides the layout.
MAX_NAME_LENGTH = 40

# What an icon value may be: a short slug the presentation layer maps to a
# drawing. **Which slugs exist is not settled here.** The domain has no opinion
# about what a gym looks like, and a table of allowed icons in this file would
# be a deployment every time somebody wants a new one.
MAX_ICON_LENGTH = 32
ICON_SLUG = re.compile(r"^[a-z][a-z0-9-]*$")

# How many categories one budget may gather. Not a storage limit — it is that a
# cap over twenty categories is a cap over everything, which is what an empty
# scope already says, more cheaply and more honestly.
MAX_SCOPE_CATEGORIES = 20


class BudgetState(enum.Enum):
    """How a period is doing against one cap.

    The three the screen draws, decided here and not there, so the card on the
    dashboard and the row on the budgets screen can never disagree.

    Explicit string values: they cross the HTTP boundary as the enum itself,
    so reordering the members must not change what a client reads.
    """

    OK = "ok"
    WARNING = "warning"
    OVER = "over"


@dataclasses.dataclass(frozen=True, slots=True)
class BudgetId(ValueObject):
    """What identifies one budget. A uuid, like a bill's.

    Generated rather than derived from what the budget watches — see this
    module's docstring for what that reversed and why it had to.
    """

    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid budget id: {value!r}") from error

    def __str__(self) -> str:
        return str(self.value)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetScope(ValueObject):
    """What counts toward a budget: which categories, in which accounts.

    **Empty means every one**, on both axes, and that is the whole design. The
    alternative was a flag per axis — `all_categories: bool` next to
    `categories: frozenset` — which stores two ways to say the same thing and
    lets them contradict each other.

    The two axes are independent and are read as an *and*: «restaurantes, pero
    solo lo que pase por la tarjeta» is a real sentence somebody says.
    """

    #: Empty watches every category, including spending no merchant owns yet.
    categories: frozenset[str] = frozenset()
    #: Empty watches every account, including movements with no account.
    accounts: frozenset[AccountId] = frozenset()

    @classmethod
    def everything(cls) -> Self:
        """The whole month, unqualified. What a first budget usually is."""
        return cls()

    @classmethod
    def of(
        cls,
        *,
        categories: frozenset[str] | None = None,
        accounts: frozenset[AccountId] | None = None,
    ) -> Self:
        """Build a scope, refusing what cannot be one.

        Categories go through the same rule that guards any category value
        crossing into an identity, so a scope cannot hold a spelling that no
        spending will ever be bucketed under.
        """
        clean = frozenset(keyable_category(value) for value in categories or ())

        if len(clean) > MAX_SCOPE_CATEGORIES:
            raise ValueError(
                f"A budget watches at most {MAX_SCOPE_CATEGORIES} categories; "
                "leave it empty to watch every one",
            )

        return cls(categories=clean, accounts=frozenset(accounts or ()))

    @property
    def total(self) -> bool:
        """Whether this watches every category.

        The one property worth a name: it is what decides that a budget can be
        judged without knowing a movement's category, which is what the module
        docstring calls the exception that makes an alert possible.
        """
        return not self.categories

    @property
    def every_account(self) -> bool:
        return not self.accounts

    def watches_category(self, category: str | None) -> bool:
        """Whether spending filed here counts against this budget.

        `None` is the bucket for movements no merchant owns yet — *unknown*,
        which is not the `uncategorized` category. A total budget counts it,
        because it is money that left; a budget naming categories cannot, since
        nobody has said which of them it belongs to.
        """
        if self.total:
            return True

        return category is not None and category in self.categories


@dataclasses.dataclass(eq=False, slots=True)
class Budget(AggregateRoot[BudgetId]):
    """A ceiling on what a scope spends, in one currency, for a month.

    One currency, like the monthly plan and for the same reason: comparing a
    cap against spending in another unit needs a rate this app has no business
    inventing. Somebody who spends in two currencies declares two budgets.

    `month` is still the only period this understands — every month when it is
    `None`, one month when it is a key. Weekly, yearly and a free date range
    are the next iteration's, and they land here rather than beside here: a
    second vocabulary of periods inside one context is how a screen and an
    endpoint end up disagreeing about when a period started.
    """

    user_id: UserId
    name: str
    limit: Money
    scope: BudgetScope = dataclasses.field(default_factory=BudgetScope.everything)
    #: A slug the presentation layer draws. Empty means «pick one for me», and
    #: what it picks is a question about drawings, answered there.
    icon: str = ""
    #: The share of the cap at which the bar turns amber, in per cent.
    warn_at: int = DEFAULT_WARN_PERCENT
    #: None governs every month. A key like `2026-09` governs that one only,
    #: **alongside** the recurring ones rather than instead of them.
    month: str | None = None
    updated_at: PosixTime = dataclasses.field(default_factory=PosixTime.now)

    @classmethod
    def declare(
        cls,
        *,
        user_id: UserId,
        name: str,
        limit: Money,
        scope: BudgetScope | None = None,
        icon: str = "",
        month: str | None = None,
        warn_at: int = DEFAULT_WARN_PERCENT,
    ) -> Self:
        """Put a new ceiling on part of somebody's spending.

        No event. A ceiling somebody sets on their own spending is a statement
        about their life, not about money that moved.
        """
        return cls(
            id=BudgetId.new(),
            user_id=user_id,
            name=nameable(name),
            limit=_cappable(limit),
            scope=scope if scope is not None else BudgetScope.everything(),
            icon=iconable(icon),
            warn_at=_warnable(warn_at),
            month=valid_month(month),
        )

    def amend(
        self,
        *,
        name: str,
        limit: Money,
        scope: BudgetScope,
        icon: str,
        month: str | None,
        warn_at: int,
    ) -> None:
        """Restate this budget whole.

        Every field, never a subset, which is the shape the monthly plan uses
        for the same reason: a ceiling and the point it warns at are one
        statement, and half an update leaves a warning standing against a
        ceiling it was never set against.

        **The id does not move.** That is the entire difference this iteration
        bought: editing a budget used to mean writing a second row under a new
        identity and hoping somebody deleted the first.
        """
        self.name = nameable(name)
        self.limit = _cappable(limit)
        self.scope = scope
        self.icon = iconable(icon)
        self.month = valid_month(month)
        self.warn_at = _warnable(warn_at)
        self.updated_at = PosixTime.now()

    @property
    def recurring(self) -> bool:
        """Whether this governs every month rather than just one."""
        return self.month is None

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

    def governs(self, month: str) -> bool:
        """Whether this budget has anything to say about a month."""
        return self.recurring or self.month == month

    def progress(self, spent: Decimal) -> BudgetProgress:
        """How a period is doing, given what the ledger says went out.

        `spent` comes from outside because it is a question for a repository,
        and it is the *outgoing* figure and never the net one: a refund inside
        the scope would make the net read as under budget while the money did
        leave and came back, which is two facts and not none.

        Nothing is floored. Somebody who went over needs to see by how much, so
        `remaining` is reported negative exactly the way the allowance is.
        """
        return BudgetProgress(
            budget_id=self.id,
            name=self.name,
            icon=self.icon,
            scope=self.scope,
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
    """One budget and what the period has done to it.

    Every figure the screen needs to show the comparison rather than assert its
    result: a bar somebody cannot check against their own head is a bar they
    stop believing the first time it disagrees.

    Deliberately flat — the budget's fields are copied in rather than nested —
    because this is what the endpoint answers, and a client reading a ceiling
    out of one object and a state out of another is a client that can render
    the two out of step.
    """

    budget_id: BudgetId
    name: str
    icon: str
    scope: BudgetScope
    currency: Currency
    limit: Decimal
    #: What went out of this scope this period, transfers excluded.
    spent: Decimal
    #: The cap less what is spent. Negative when it was passed, and reported
    #: that way rather than floored at zero.
    remaining: Decimal
    warn_at: int
    state: BudgetState
    #: The month this budget is declared for, or None when it governs all.
    month: str | None
    recurring: bool


def keyable_category(category: str) -> str:
    """A category value that can safely be part of a budget's scope.

    Public because reading and declaring ask the same question, and two
    spellings of "what may be watched" is how the refusal ends up living in
    only one of the two callers.

    Whether it names anything is not settled here — the vocabulary is
    Merchant's and half of it is whatever this person wrote, so that answer
    comes through a port at the boundary. What is settled here is that the
    value can be stored and compared: `#` separates the segments of a sort key,
    so a category carrying one would be two categories that store as one.
    """
    stripped = category.strip()

    if not stripped:
        raise ValueError("A budget cannot watch a category with no name")

    if len(stripped) > MAX_CATEGORY_LENGTH:
        raise ValueError(
            f"A category cannot exceed {MAX_CATEGORY_LENGTH} characters",
        )

    if "#" in stripped:
        raise ValueError(f"A category cannot contain '#': {stripped!r}")

    return stripped


def nameable(name: str) -> str:
    """What a budget is called.

    Required, and that is new. While a cap *was* its category there was nothing
    to name it: the category was the name. A budget over four categories, or
    over everything, has no name unless somebody writes one — «Salidas» is not
    derivable from restaurants, bars and delivery.
    """
    stripped = name.strip()

    if not stripped:
        raise ValueError("A budget needs a name")

    if len(stripped) > MAX_NAME_LENGTH:
        raise ValueError(
            f"A budget name cannot exceed {MAX_NAME_LENGTH} characters",
        )

    return stripped


def iconable(icon: str) -> str:
    """A slug for a drawing, or nothing at all.

    Refused rather than coerced, and checked for shape rather than membership:
    this file does not know which icons the app draws, and should not — that
    list changes with a frontend release, and a domain that owned it would make
    a new icon a backend deployment.
    """
    stripped = icon.strip()

    if not stripped:
        return ""

    if len(stripped) > MAX_ICON_LENGTH:
        raise ValueError(f"An icon cannot exceed {MAX_ICON_LENGTH} characters")

    if not ICON_SLUG.fullmatch(stripped):
        raise ValueError(f"An icon is written like 'shopping-bag': {stripped!r}")

    return stripped


def valid_month(month: str | None) -> str | None:
    """`None` for every month, or one spelled the way this context spells it.

    Refused rather than coerced. `2026-9` and `sep-2026` are both somebody
    meaning September, and both would store as a month that no spending is ever
    bucketed under — a cap that silently governs nothing, which looks exactly
    like a cap nothing has been spent against.
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
