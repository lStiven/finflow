"""Accounts whose balance moves without anybody spending anything.

A loan is the case that makes this necessary. Somebody who owes 60 000 000 and
pays 2 000 000 in a month does not then owe 58 000 000: the month charged
interest on what was owed, the bank added the insurance the loan is required
to carry, and the payment cleared those before it touched the debt. What is
left of the payment is the only part that reduced anything. Recording just the
payment therefore reports a debt falling faster than it falls — and it is the
error a balance never recovers from, because nothing arriving later
contradicts it.

So this module is the arithmetic of the two things a plain ledger cannot see:

* **What a period charged.** Interest on the outstanding balance, plus the
  recurring charges the product carries — *seguro de vida deudores* on what is
  owed, *seguro de incendio y terremoto* on the value of the property, a flat
  administration fee, *retención en la fuente* on what an investment earned.
* **What a payment is made of.** A statement's instalment splits into
  interest, charges and the part that actually amortizes, and only the third
  moves the debt down.

Nothing here reads or writes anything. It takes a balance, a rate and a
calendar and answers with numbers; `Account` holds the terms, and the
application layer turns a closed period into ledger rows, so an accrual ends
up being an ordinary movement that the balance is the running total of. That
is deliberate: an interest charge nobody can see as a row is a balance nobody
can retrace.

**The conventions this picks, once, so every caller shares them:**

* A rate is a fraction — `0.0175` is 1.75 %, never `1.75` — and it is quoted
  in one of the three ways a Colombian bank quotes one: effective annual
  (E.A.), nominal annual capitalizing monthly (M.V.), or the monthly rate
  itself. Everything converts to an effective monthly rate before it is used.
* A statement period is a month, cut on the day of the month the owner
  declared. Only two periods are ever shorter — the stub between disbursement
  and the first cut, and the part-month between the last cut and today — and
  those prorate 30/360.
* Interest for a period is charged on the balance the period closes with,
  before that period's own interest and charges land on it. A payment made on
  the cut day itself belongs to the next period: every window in this codebase
  is half-open, and two consecutive periods must not both claim one movement.
* Money is quantized to the cent, half-up, at every step rather than at the
  end. A schedule whose rows do not each stand on their own is a schedule
  nobody can check against a statement.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Iterator, Sequence
import dataclasses
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
import enum

from personal_finance.contexts.financial.domain.exceptions import (
    CurrencyMismatchError,
    FinancingTermsError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    JsonValue,
    Money,
    ValueObject,
)


# Half a century of monthly periods. A cap rather than a rule about products:
# the walk that builds a schedule is driven by a date the owner typed, and an
# accidental 1970 is otherwise an unbounded loop inside a request.
MAX_TERM_MONTHS = 600
MAX_PROJECTED_PERIODS = 600

# A typo guard, not a usury cap. Somebody who means 19.56 % E.A. and types
# `19.56` instead of `0.1956` gets a debt that grows by a factor of twenty a
# month, and every number after it is nonsense. Ten is far above any real
# rate and far below what that mistake produces.
MAX_RATE = Decimal(10)

# The window a date on these terms may sit in. Not a rule about products: a
# term of fifty years is added to whatever is stated, and a `disbursed_on` in
# the year 9999 makes `matures_on()` raise out of `datetime` — from inside the
# account's own response, which is rendered outside the endpoint's error
# translation, so one impossible date would answer 500 for the whole accounts
# list until somebody cleared it.
MIN_DATE = dt.date(1970, 1, 1)
MAX_DATE = dt.date(2999, 12, 31)

_CENTS = Decimal("0.01")
_MONTHS_IN_YEAR = 12
# The denominator of the 30/360 proration. Banks here count a month as thirty
# days regardless of what the calendar says, and a schedule that disagreed
# with that would not reconcile against a statement.
_DAYS_IN_MONTH = Decimal(30)

# What counts as rounding rather than debt on the last row. A schedule that
# quantizes every row to the cent cannot land on exactly zero — one instalment
# computed once and repeated leaves a few cents behind after enough of them —
# so without this the table grows a final row owing a single peso, which is a
# row nobody can reconcile against a payoff quote. A bank's última cuota
# absorbs the same residue. A thousandth of a payment rather than a fixed
# amount, so the rule means the same thing in pesos and in dollars.
_SETTLEMENT_TOLERANCE = Decimal("0.001")


def _quantized(amount: Decimal, currency: Currency) -> Money:
    """A cent-exact, never-negative amount.

    Clamped at zero rather than allowed to go negative: every figure this
    module produces is a charge or a portion of one, and the only way to reach
    a negative is a balance that is already the wrong side of zero — a settled
    loan, an overdrawn investment — where the honest answer is that nothing
    accrued.
    """
    rounded = amount.quantize(_CENTS, rounding=ROUND_HALF_UP)

    return Money(amount=max(rounded, Decimal(0)), currency=currency)


class RateBasis(enum.Enum):
    """How a rate is quoted, because the same product quotes it three ways.

    Explicit string values: the basis is persisted with the account, so
    reordering the members must not re-read anybody's loan at another rate.
    """

    # `% E.A.` — the figure a Colombian bank leads with.
    EFFECTIVE_ANNUAL = "effective_annual"
    # `% N.A. M.V.` — a nominal annual rate capitalizing monthly, so the
    # monthly rate is a twelfth of it and the effective annual is higher.
    NOMINAL_ANNUAL = "nominal_annual"
    # `% M.V.` — the monthly rate itself, which is what everything converts to.
    MONTHLY = "monthly"


@dataclasses.dataclass(frozen=True, slots=True)
class InterestRate(ValueObject):
    """What a balance costs, or earns, per unit of time.

    Carries the basis it was quoted in rather than converting on the way in,
    so the account still shows its owner the number their bank showed them —
    `19.56 % E.A.` and `1.4999 % mensual` are the same rate, and only one of
    them appears on a statement.
    """

    value: Decimal
    basis: RateBasis

    def __post_init__(self) -> None:
        if self.value < 0:
            raise FinancingTermsError(
                f"An interest rate cannot be negative: {self.value}"
            )

        if self.value > MAX_RATE:
            raise FinancingTermsError(
                f"An interest rate of {self.value} is a fraction typed as a "
                "percentage: 19.56 % E.A. is 0.1956, not 19.56",
            )

    @property
    def monthly(self) -> Decimal:
        """The effective monthly rate, which is what every calculation uses."""
        if self.basis is RateBasis.MONTHLY:
            return self.value

        if self.basis is RateBasis.NOMINAL_ANNUAL:
            return self.value / _MONTHS_IN_YEAR

        return (Decimal(1) + self.value) ** (Decimal(1) / _MONTHS_IN_YEAR) - 1

    @property
    def effective_annual(self) -> Decimal:
        """What the monthly rate compounds to over a year.

        The comparable figure: two products quoted differently are only
        comparable here, which is the whole reason a bank is made to publish
        it.
        """
        if self.basis is RateBasis.EFFECTIVE_ANNUAL:
            return self.value

        return (Decimal(1) + self.monthly) ** _MONTHS_IN_YEAR - 1

    def factor(self, *, days: int | None = None) -> Decimal:
        """How far one period moves a balance, as a rate.

        A whole statement period is a month and accrues `monthly` exactly —
        that is the number the bank quotes and the number its instalment is
        built from. `days` is for the two periods that are not whole months:
        the stub between disbursement and the first cut, and the part-month
        between the last cut and today. Those prorate 30/360 rather than
        compounding daily, because the two disagree only in the rounding and
        only the whole-period figure has to reconcile against a statement.
        """
        if days is None:
            return self.monthly

        if days <= 0:
            return Decimal(0)

        return self.monthly * Decimal(days) / _DAYS_IN_MONTH

    def to_dict(self) -> JsonValue:
        return {"value": str(self.value), "basis": self.basis.value}


class ChargeBasis(enum.Enum):
    """What a recurring charge is a proportion of.

    Four bases because the four charges a Colombian loan or investment
    actually carries are each a proportion of something different, and
    flattening them into "an amount per month" is what makes a projection
    drift: the life insurance falls with the debt, the fire insurance does
    not, and the withholding exists only when something was earned.
    """

    # A flat amount every period: an administration fee, a card handling fee.
    FIXED = "fixed"
    # A rate on what is owed or held — *seguro de vida deudores*, a fund's
    # management fee. Falls as the debt is paid down.
    OUTSTANDING_BALANCE = "outstanding_balance"
    # A rate on the amount originally disbursed, which does not move.
    ORIGINAL_PRINCIPAL = "original_principal"
    # A rate on a value that is not the balance at all: *seguro de incendio y
    # terremoto* is charged on what the property is insured for.
    INSURED_VALUE = "insured_value"
    # A rate on what the period earned — *retención en la fuente* on an
    # investment's yield. Zero in a period that earned nothing.
    EARNINGS = "earnings"


MAX_CHARGE_NAME_LENGTH = 80


@dataclasses.dataclass(frozen=True, slots=True)
class RecurringCharge(ValueObject):
    """One thing that is charged every period besides the interest.

    Named, because the owner has to recognise it on their statement: "Seguro
    de vida deudores", "Comisión de administración", "Retención en la fuente".

    `charged_to_balance` is the field that decides whether this is part of the
    account's own arithmetic or only part of what has to be paid. True — the
    ordinary case — means the bank adds it to what is owed and the instalment
    clears it, so it belongs in the ledger and moves the balance. False means
    it is collected somewhere else entirely: from a savings account, by its
    own direct debit, and that movement arrives as its own alert. Posting it
    here as well would charge the same insurance twice.
    """

    name: str
    basis: ChargeBasis
    # `FIXED` only. Its currency has to be the account's, checked where the
    # account is known.
    amount: Money | None = None
    # Every other basis: a monthly rate, as a fraction.
    rate: Decimal | None = None
    # `INSURED_VALUE` only: what the policy insures, which is not a balance
    # this app holds.
    base: Money | None = None
    charged_to_balance: bool = True

    def __post_init__(self) -> None:
        name = self.name.strip()

        if not name:
            raise FinancingTermsError("A recurring charge needs a name")

        if len(name) > MAX_CHARGE_NAME_LENGTH:
            raise FinancingTermsError(
                f"A charge name exceeds {MAX_CHARGE_NAME_LENGTH} characters",
            )

        object.__setattr__(self, "name", name)

        if self.basis is ChargeBasis.FIXED:
            if self.amount is None:
                raise FinancingTermsError(
                    f"{name} is a fixed charge and needs an amount"
                )

            if self.rate is not None:
                raise FinancingTermsError(
                    f"{name} is a fixed charge: it has an amount, not a rate",
                )
        else:
            if self.rate is None:
                raise FinancingTermsError(f"{name} is charged as a rate and needs one")

            if self.amount is not None:
                raise FinancingTermsError(
                    f"{name} is charged as a rate: it has a rate, not an amount",
                )

            if self.rate < 0 or self.rate > MAX_RATE:
                raise FinancingTermsError(
                    f"{name} has a rate of {self.rate}, which is a fraction "
                    "typed as a percentage",
                )

        if self.basis is ChargeBasis.INSURED_VALUE and self.base is None:
            raise FinancingTermsError(
                f"{name} is charged on an insured value, so it needs one: the "
                "balance is not what that policy covers",
            )

        if self.basis is not ChargeBasis.INSURED_VALUE and self.base is not None:
            raise FinancingTermsError(f"{name} is not charged on an insured value")

    def due(self, base: ChargeBase) -> Money:
        """What this charge costs for one period.

        Never raises for a base it cannot use: a charge on the original
        principal of a loan whose disbursement the owner never stated is zero,
        not an error, because the alternative is a schedule that refuses to
        render over a field nobody had to hand.
        """
        currency = base.outstanding.currency

        if self.basis is ChargeBasis.FIXED:
            amount = self.amount

            if amount is None:  # pragma: no cover - refused at construction
                return Money(amount=Decimal(0), currency=currency)

            if amount.currency is not currency:
                raise CurrencyMismatchError(
                    f"{self.name} is charged in {amount.currency.value} on a "
                    f"{currency.value} account",
                )

            return amount

        rate = self.rate or Decimal(0)
        against = self._base_amount(base)

        if against is None:
            return Money(amount=Decimal(0), currency=currency)

        if against.currency is not currency:
            raise CurrencyMismatchError(
                f"{self.name} is charged on a {against.currency.value} amount "
                f"on a {currency.value} account",
            )

        return _quantized(against.amount * rate, currency)

    def _base_amount(self, base: ChargeBase) -> Money | None:
        if self.basis is ChargeBasis.OUTSTANDING_BALANCE:
            return base.outstanding

        if self.basis is ChargeBasis.ORIGINAL_PRINCIPAL:
            return base.original_principal

        if self.basis is ChargeBasis.INSURED_VALUE:
            return self.base

        return base.earned


@dataclasses.dataclass(frozen=True, slots=True)
class ChargeBase(ValueObject):
    """Everything a charge might be a proportion of, for one period."""

    outstanding: Money
    original_principal: Money | None = None
    # What the period's interest came to. Only the withholding reads it, and
    # it is zero rather than absent in a period that earned nothing.
    earned: Money | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ChargeAmount(ValueObject):
    """One charge, priced for one period.

    Keeps `charged_to_balance` beside the figure so a reader never has to go
    back to the terms to know whether this number is already inside the
    balance or is money leaving somewhere else.
    """

    name: str
    amount: Money
    charged_to_balance: bool = True


class AmortizationStyle(enum.Enum):
    """How a loan's instalment is put together.

    Persisted with the account, so the values are explicit.
    """

    # Cuota fija: the instalment is constant and the split between interest
    # and capital moves through the term. What almost every consumer loan and
    # peso-denominated mortgage here uses.
    FRENCH = "french"
    # Abono constante a capital: the capital portion is fixed and the
    # instalment falls every month. Cheaper in total, and offered as an option
    # on mortgages.
    CONSTANT_PRINCIPAL = "constant_principal"
    # Only the interest is paid; the debt does not move. A grace period, and
    # the shape of some *rotativo* products.
    INTEREST_ONLY = "interest_only"


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def on_day(year: int, month: int, day: int) -> dt.date:
    """That day of that month, clamped to the last one it has.

    A statement cut on the 31st falls on the 28th in February and does not
    skip the month. Clamping rather than rolling forward keeps every period
    inside its own month, which is what makes twelve of them a year.
    """
    return dt.date(year, month, min(day, _last_day(year, month)))


def add_months(day: dt.date, months: int, *, on: int | None = None) -> dt.date:
    """The same day-of-month, `months` later, clamped the same way."""
    total = day.month - 1 + months
    year = day.year + total // _MONTHS_IN_YEAR
    month = total % _MONTHS_IN_YEAR + 1

    return on_day(year, month, day.day if on is None else on)


def is_cut(day: dt.date, statement_day: int) -> bool:
    """Whether that day is itself a statement date, clamping included."""
    return day == on_day(day.year, day.month, statement_day)


def last_cut_on_or_before(day: dt.date, statement_day: int) -> dt.date:
    """The most recent statement date up to and including `day`.

    Where a part-month begins. Interest between two cuts is charged whole on
    the second of them, so the only interest that has genuinely accrued and
    not been charged is the interest since this date — never since some older
    cursor, however far behind the posting has fallen.
    """
    return add_months(first_cut_after(day, statement_day), -1, on=statement_day)


def first_cut_after(day: dt.date, statement_day: int) -> dt.date:
    """The first statement date strictly after `day`.

    Strictly: a period is half-open, so a movement on the cut day belongs to
    the period that opens there, and a cursor sitting on a cut date must not
    re-accrue the period that closed on it.
    """
    candidate = on_day(day.year, day.month, statement_day)

    if candidate > day:
        return candidate

    return add_months(candidate, 1, on=statement_day)


@dataclasses.dataclass(frozen=True, slots=True)
class StatementPeriod(ValueObject):
    """One cut-to-cut window: `[starts_on, ends_on)`.

    `partial` is the stub between the account's own start and its first cut.
    It is the only period that does not accrue a whole month, and saying so
    here keeps that rule out of every caller.
    """

    starts_on: dt.date
    ends_on: dt.date
    partial: bool = False

    def __post_init__(self) -> None:
        if self.ends_on <= self.starts_on:
            raise FinancingTermsError("A statement period ends after it starts")

    @property
    def days(self) -> int:
        return (self.ends_on - self.starts_on).days

    def to_dict(self) -> JsonValue:
        return {
            "starts_on": self.starts_on.isoformat(),
            "ends_on": self.ends_on.isoformat(),
            "partial": self.partial,
        }


def statement_periods(
    *,
    since: dt.date,
    through: dt.date,
    statement_day: int,
    limit: int = MAX_PROJECTED_PERIODS,
) -> tuple[StatementPeriod, ...]:
    """Every period that closed between `since` and `through`, in order.

    Closed, never the one still running: a period that has not reached its cut
    date has charged nothing yet, and posting a part-month as though it had
    would have to be taken back by the next run — on a balance whose whole
    point is that nothing ever takes anything back.

    A period that does not *start* on a cut date is a stub and accrues pro
    rata. There are only two of those and they are the same shape: the days
    between disbursement and the first cut, and the days between the day
    somebody declared their terms and the first cut after it.
    """
    periods: list[StatementPeriod] = []
    cursor = since

    for cut in _cuts(
        after=since,
        through=through,
        statement_day=statement_day,
        limit=limit,
    ):
        periods.append(
            StatementPeriod(
                starts_on=cursor,
                ends_on=cut,
                partial=not is_cut(cursor, statement_day),
            ),
        )
        cursor = cut

    return tuple(periods)


def _cuts(
    *,
    after: dt.date,
    through: dt.date,
    statement_day: int,
    limit: int,
) -> Iterator[dt.date]:
    cut = first_cut_after(after, statement_day)

    for _ in range(max(limit, 0)):
        if cut > through:
            return

        yield cut
        cut = add_months(cut, 1, on=statement_day)


@dataclasses.dataclass(frozen=True, slots=True)
class LoanTerms(ValueObject):
    """What has to be known about a loan to say what it really costs.

    Everything here is asked of the owner, because none of it can be read out
    of a bank alert: an alert says a payment was made, never what the payment
    was made of.

    * `rate` — the one the bank quotes, in the basis it quotes it in.
    * `disbursed_on` — when the money was handed over. The first period runs
      from here to the first cut, and it is shorter than a month.
    * `term_months` — the plazo, which is what a missing instalment is solved
      from.
    * `statement_day` — la fecha de corte: the day interest is charged and the
      statement closes.
    * `payment_day` — when the instalment is due, usually a few days after the
      cut. Defaults to the cut itself.
    * `principal` — what was disbursed. Optional, because somebody declaring a
      loan halfway through its life knows what they owe and not always what
      they borrowed; a charge quoted on the original principal is zero without
      it.
    * `installment` — la cuota, when the bank fixed one. Left out, it is
      computed from the balance, the rate and what is left of the term.
    * `installment_covers_charges` — whether the number on the statement
      already includes the insurance. This is the field that decides what a
      payment is made of, and getting it backwards misstates the capital
      portion by exactly the insurance.
    * `charges` — the seguros and fees, priced per period.
    """

    rate: InterestRate
    disbursed_on: dt.date
    term_months: int
    statement_day: int
    payment_day: int | None = None
    style: AmortizationStyle = AmortizationStyle.FRENCH
    principal: Money | None = None
    installment: Money | None = None
    installment_covers_charges: bool = False
    charges: tuple[RecurringCharge, ...] = ()

    def __post_init__(self) -> None:
        _check_day(self.statement_day, "statement day")
        _check_date(self.disbursed_on, "disbursement date")

        if self.payment_day is not None:
            _check_day(self.payment_day, "payment day")

        if self.term_months < 1 or self.term_months > MAX_TERM_MONTHS:
            raise FinancingTermsError(
                f"A term of {self.term_months} months is not one this can "
                f"schedule: it has to be between 1 and {MAX_TERM_MONTHS}",
            )

        if (
            any(
                charge.basis is ChargeBasis.ORIGINAL_PRINCIPAL
                for charge in self.charges
            )
            and self.principal is None
        ):
            raise FinancingTermsError(
                "A charge quoted on the original principal needs the amount "
                "that was disbursed",
            )

        if any(charge.basis is ChargeBasis.EARNINGS for charge in self.charges):
            raise FinancingTermsError(
                "A loan earns nothing: a charge on earnings belongs to an investment",
            )

        _check_distinct(self.charges)

    @property
    def due_day(self) -> int:
        """When the instalment is due, which is the cut unless stated apart."""
        return self.statement_day if self.payment_day is None else self.payment_day

    def matures_on(self) -> dt.date:
        """The last cut of the term, as agreed at disbursement."""
        return first_cut_after(
            add_months(self.disbursed_on, self.term_months - 1),
            self.statement_day,
        )

    def to_dict(self) -> JsonValue:
        return {
            "rate": self.rate.to_dict(),
            "disbursed_on": self.disbursed_on.isoformat(),
            "term_months": self.term_months,
            "statement_day": self.statement_day,
            "payment_day": self.payment_day,
            "style": self.style.value,
            "principal": None if self.principal is None else self.principal.to_dict(),
            "installment": (
                None if self.installment is None else self.installment.to_dict()
            ),
            "installment_covers_charges": self.installment_covers_charges,
            "charges": [charge.to_dict() for charge in self.charges],
        }


@dataclasses.dataclass(frozen=True, slots=True)
class InvestmentTerms(ValueObject):
    """What has to be known about an investment for its value to move on time.

    `rate` is what separates the two kinds of investment, and it is optional
    for exactly that reason. **Fixed income** — a CDT, a remunerated savings
    account, a fund with an agreed return — has one, and its yield can be
    computed the same way a loan's interest is. **Variable income** — shares,
    a fund whose unit price moves — has none, and no arithmetic can tell you
    what it is worth: it is worth what the market says today, which is why the
    owner restates its value instead and the difference is recorded as the
    period's return.

    `matures_on` is the CDT's vencimiento: nothing accrues past it, because
    the money stopped being invested.

    Withholding is a charge like any other, quoted on `EARNINGS` — retención
    en la fuente is 4 % of the yield, not of the balance, and modelling it as
    a field of its own would have made it the one charge nobody could name on
    a statement.
    """

    opened_on: dt.date
    statement_day: int
    rate: InterestRate | None = None
    matures_on: dt.date | None = None
    charges: tuple[RecurringCharge, ...] = ()

    def __post_init__(self) -> None:
        _check_day(self.statement_day, "statement day")
        _check_date(self.opened_on, "opening date")

        if self.matures_on is not None:
            _check_date(self.matures_on, "maturity date")

        if self.matures_on is not None and self.matures_on <= self.opened_on:
            raise FinancingTermsError("An investment matures after it is opened")

        if self.rate is None and any(
            charge.basis is ChargeBasis.EARNINGS for charge in self.charges
        ):
            raise FinancingTermsError(
                "Nothing is withheld from a return this app does not compute: "
                "state a rate, or record the value instead",
            )

        if any(
            charge.basis is ChargeBasis.ORIGINAL_PRINCIPAL for charge in self.charges
        ):
            raise FinancingTermsError(
                "An investment has no original principal: its contributions "
                "are movements, and a charge on them would price the wrong one",
            )

        _check_distinct(self.charges)

    @property
    def accrues(self) -> bool:
        """Whether a period can be priced at all, or the value has to be told."""
        return self.rate is not None

    def to_dict(self) -> JsonValue:
        return {
            "opened_on": self.opened_on.isoformat(),
            "statement_day": self.statement_day,
            "rate": None if self.rate is None else self.rate.to_dict(),
            "matures_on": None
            if self.matures_on is None
            else self.matures_on.isoformat(),
            "charges": [charge.to_dict() for charge in self.charges],
        }


def _check_day(day: int, what: str) -> None:
    if day < 1 or day > 31:
        raise FinancingTermsError(f"A {what} is a day of the month: {day} is not")


def _check_date(day: dt.date, what: str) -> None:
    if day < MIN_DATE or day > MAX_DATE:
        raise FinancingTermsError(
            f"A {what} of {day.isoformat()} is not a date this can schedule "
            f"from: it has to fall between {MIN_DATE.year} and {MAX_DATE.year}",
        )


def _check_distinct(charges: Sequence[RecurringCharge]) -> None:
    """Two charges may not share a name, and it is not a matter of tidiness.

    A posted charge is identified by the account, the period and the charge's
    own name folded — that is what makes running an accrual twice a no-op. Two
    charges under one name are one key: the first would be written, the second
    refused as a duplicate, and the owner would be short one insurance every
    month with nothing anywhere saying so.
    """
    seen = [charge.name.casefold() for charge in charges]

    if len(set(seen)) != len(seen):
        raise FinancingTermsError(
            "Two charges cannot share a name: each is posted once per period "
            "under it, so the second would never be charged at all",
        )


@dataclasses.dataclass(frozen=True, slots=True)
class AccruedPeriod(ValueObject):
    """What one closed period charged, and on what.

    The items are kept apart rather than summed because each becomes its own
    ledger row: a balance that grew by 920 000 explains nothing, while
    900 000 of interest and 20 000 of insurance explains everything. The
    caller decides each row's direction from the account's own category —
    interest owed grows a debt, interest earned grows a balance, and a charge
    always costs money either way.
    """

    period: StatementPeriod
    opening_balance: Money
    interest: Money
    charges: tuple[ChargeAmount, ...] = ()

    @property
    def charged_to_balance(self) -> tuple[ChargeAmount, ...]:
        return tuple(charge for charge in self.charges if charge.charged_to_balance)

    @property
    def total_charges(self) -> Money:
        return _sum(
            (charge.amount for charge in self.charges),
            self.opening_balance.currency,
        )


def accrue_period(
    *,
    period: StatementPeriod,
    opening: Money,
    rate: InterestRate | None,
    charges: Sequence[RecurringCharge] = (),
    original_principal: Money | None = None,
) -> AccruedPeriod:
    """Price one closed period against the balance it closed with.

    A balance of zero accrues nothing at all — not even a flat fee. A loan
    that has been paid off keeps its history and stops charging; charging a
    settled account a monthly administration fee forever is the shape of bug
    that turns a finished loan into a growing one nobody is watching.
    """
    currency = opening.currency

    if opening.amount <= 0:
        return AccruedPeriod(
            period=period,
            opening_balance=opening,
            interest=Money(amount=Decimal(0), currency=currency),
        )

    factor = (
        Decimal(0)
        if rate is None
        else rate.factor(days=period.days if period.partial else None)
    )
    interest = _quantized(opening.amount * factor, currency)
    base = ChargeBase(
        outstanding=opening,
        original_principal=original_principal,
        earned=interest,
    )

    return AccruedPeriod(
        period=period,
        opening_balance=opening,
        interest=interest,
        charges=tuple(
            ChargeAmount(
                name=charge.name,
                amount=charge.due(base),
                charged_to_balance=charge.charged_to_balance,
            )
            for charge in charges
        ),
    )


def _sum(amounts: Iterable[Money], currency: Currency) -> Money:
    total = Decimal(0)

    for amount in amounts:
        if amount.currency is not currency:
            raise CurrencyMismatchError(
                f"Cannot add {amount.currency.value} to a {currency.value} total",
            )

        total += amount.amount

    return Money(amount=total, currency=currency)


def installment_for(
    *,
    outstanding: Money,
    rate: InterestRate,
    periods: int,
) -> Money:
    """The fixed instalment that clears this balance in this many periods.

    The French formula, `P·i / (1 - (1+i)^-n)`, which is what a bank's cuota
    fija is. At a rate of zero it degenerates to the balance split evenly,
    which the formula cannot express and which is a real case — an
    interest-free employer loan.
    """
    if periods < 1:
        raise FinancingTermsError(
            "An instalment clears a balance over at least one period"
        )

    monthly = rate.monthly
    # `1 - (1+i)^-n` is what the formula divides by, and for a rate small
    # enough that `(1+i)^-n` rounds to one at this precision it is zero. A
    # rate of 1e-30 is not a loan product; it is somebody's typo, and the
    # honest answer for it is the same as for a rate of zero — the balance
    # split evenly — rather than a `DivisionByZero` escaping as a 500.
    divisor = Decimal(1) - (Decimal(1) + monthly) ** -periods

    if monthly == 0 or divisor == 0:
        return _quantized(outstanding.amount / periods, outstanding.currency)

    return _quantized(
        outstanding.amount * monthly / divisor,
        outstanding.currency,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduledPayment(ValueObject):
    """One row of the amortization table.

    `principal` is the only figure that moves the debt, and the reason the
    table exists: it is what the payment was worth once the month had taken
    what it was owed.
    """

    period: StatementPeriod
    due_on: dt.date
    opening_balance: Money
    interest: Money
    charges: tuple[ChargeAmount, ...]
    principal: Money
    # What has to be paid: capital, interest and every charge, whether or not
    # the charge passes through this balance.
    due: Money
    closing_balance: Money


@dataclasses.dataclass(frozen=True, slots=True)
class LoanSchedule(ValueObject):
    """What the loan does from here, month by month.

    A projection, never a record. It assumes every instalment is paid on time
    and nothing else happens, so the moment a real payment lands the balance
    it starts from moves and the table is rebuilt from the new one — which is
    why nothing here is stored.
    """

    payments: tuple[ScheduledPayment, ...]
    total_interest: Money
    total_charges: Money
    total_due: Money
    # When the balance reaches zero, or None when it does not inside the
    # horizon asked for.
    settles_on: dt.date | None
    # The instalment does not cover the interest, so the debt grows every
    # month no matter how long it is paid. The one answer worth interrupting
    # somebody for.
    negatively_amortizing: bool


def project_loan(
    *,
    terms: LoanTerms,
    outstanding: Money,
    as_of: dt.date,
    periods: int,
) -> LoanSchedule:
    """Amortize `outstanding` forward from `as_of`, at most `periods` rows.

    The balance comes from the ledger, not from the terms: what is owed today
    is the running total of everything recorded, and a table built from the
    original principal would describe a loan nobody has.
    """
    currency = outstanding.currency
    horizon = min(max(periods, 0), MAX_PROJECTED_PERIODS)
    remaining = max(_periods_left(terms, as_of), 1)
    installment = terms.installment or installment_for(
        outstanding=outstanding,
        rate=terms.rate,
        periods=remaining,
    )
    fixed_principal = _quantized(outstanding.amount / remaining, currency)

    balance = outstanding
    rows: list[ScheduledPayment] = []
    interest_total = Decimal(0)
    charges_total = Decimal(0)
    due_total = Decimal(0)
    settles_on: dt.date | None = None
    starves = False
    cursor = as_of

    for _ in range(horizon):
        if balance.amount <= 0:
            break

        cut = first_cut_after(cursor, terms.statement_day)
        period = StatementPeriod(
            starts_on=cursor,
            ends_on=cut,
            partial=not is_cut(cursor, terms.statement_day),
        )
        accrued = accrue_period(
            period=period,
            opening=balance,
            rate=terms.rate,
            charges=terms.charges,
            original_principal=terms.principal,
        )
        charges = _sum([charge.amount for charge in accrued.charges], currency)
        principal, due = _split(
            terms=terms,
            balance=balance,
            installment=installment,
            fixed_principal=fixed_principal,
            interest=accrued.interest,
            charges=charges,
            charges_in_installment=_sum(
                [charge.amount for charge in accrued.charged_to_balance],
                currency,
            ),
        )
        starves = starves or (
            terms.style is not AmortizationStyle.INTEREST_ONLY and principal.amount <= 0
        )
        # `opening - principal`, and nothing else: whatever the month
        # charged was either cleared by the instalment or never passed through
        # this balance at all. Spelling it any other way invites the two
        # halves of `_split` to disagree with the table beside them.
        closing = Money(amount=balance.amount - principal.amount, currency=currency)
        rows.append(
            ScheduledPayment(
                period=period,
                due_on=on_day(cut.year, cut.month, terms.due_day),
                opening_balance=balance,
                interest=accrued.interest,
                charges=accrued.charges,
                principal=principal,
                due=due,
                closing_balance=closing,
            ),
        )
        interest_total += accrued.interest.amount
        charges_total += charges.amount
        due_total += due.amount

        if closing.amount <= 0 and settles_on is None:
            settles_on = cut

        balance = closing
        cursor = cut

    return LoanSchedule(
        payments=tuple(rows),
        total_interest=Money(amount=interest_total, currency=currency),
        total_charges=Money(amount=charges_total, currency=currency),
        total_due=Money(amount=due_total, currency=currency),
        settles_on=settles_on,
        negatively_amortizing=starves,
    )


def _periods_left(terms: LoanTerms, as_of: dt.date) -> int:
    """How many cuts remain of the agreed term, counted from `as_of`.

    Never below one: a loan still carrying a balance past its term is in
    arrears, not finished, and a schedule that divided by zero rather than
    saying so would be the least useful answer available.
    """
    elapsed = (as_of.year - terms.disbursed_on.year) * _MONTHS_IN_YEAR + (
        as_of.month - terms.disbursed_on.month
    )

    return max(terms.term_months - max(elapsed, 0), 1)


def _split(
    *,
    terms: LoanTerms,
    balance: Money,
    installment: Money,
    fixed_principal: Money,
    interest: Money,
    charges: Money,
    charges_in_installment: Money,
) -> tuple[Money, Money]:
    """How one instalment divides into capital, and what has to be paid.

    The whole point of the module in six lines: the month takes its interest
    and its charges first, and only what survives that reduces the debt. An
    instalment smaller than the interest reduces nothing and the debt grows,
    which is reported rather than corrected — it is a fact about the loan, and
    inventing a bigger payment would hide it.

    A charge added to the balance cancels out of the *closing balance* and is
    deliberately absent from it: the month adds it to the debt and the
    instalment clears it in the same breath, so what is left is
    `opening - principal` whichever side of the payment the insurance is
    quoted on.

    It is not absent from the split, though, and the two charges are not the
    same charge. `installment_covers_charges` says the number on the statement
    already contains the insurance the *credit* carries — never one the bank
    debits from another account, which is a separate payment on a separate
    day and was never inside this instalment. Subtracting that one too would
    take it out of the capital portion every month, understating what the
    payment actually amortizes by exactly its amount.
    """
    currency = balance.currency
    covered = (
        charges_in_installment.amount
        if terms.installment_covers_charges
        else Decimal(0)
    )

    if terms.style is AmortizationStyle.INTEREST_ONLY:
        principal = Decimal(0)
    elif terms.style is AmortizationStyle.CONSTANT_PRINCIPAL:
        principal = fixed_principal.amount
    else:
        principal = installment.amount - interest.amount - covered

    # Capped by the debt itself: a payment larger than what is owed settles it
    # and no more, and a row whose closing balance went negative would be an
    # account the bank owes money to.
    principal = min(max(principal, Decimal(0)), balance.amount)

    # And the last instalment takes whatever is left, which is what clears the
    # cents that rounding every row leaves behind.
    if (
        Decimal(0)
        < balance.amount - principal
        <= installment.amount * _SETTLEMENT_TOLERANCE
    ):
        principal = balance.amount

    due = principal + interest.amount + charges.amount

    return (
        Money(amount=principal, currency=currency),
        Money(amount=due, currency=currency),
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ProjectedReturn(ValueObject):
    """One period of an investment's own arithmetic."""

    period: StatementPeriod
    opening_balance: Money
    earned: Money
    charges: tuple[ChargeAmount, ...]
    closing_balance: Money


@dataclasses.dataclass(frozen=True, slots=True)
class InvestmentProjection(ValueObject):
    """What a fixed-income position is worth over the periods ahead.

    Empty for variable income, and that is the honest answer: nothing about a
    share price can be projected from a rate nobody agreed to.
    """

    periods: tuple[ProjectedReturn, ...]
    total_earned: Money
    total_charges: Money
    value_at_end: Money
    matures_on: dt.date | None


def project_investment(
    *,
    terms: InvestmentTerms,
    value: Money,
    as_of: dt.date,
    periods: int,
) -> InvestmentProjection:
    """Compound `value` forward at the agreed rate, net of what is withheld."""
    currency = value.currency
    horizon = min(max(periods, 0), MAX_PROJECTED_PERIODS)
    balance = value
    rows: list[ProjectedReturn] = []
    earned_total = Decimal(0)
    charges_total = Decimal(0)
    cursor = as_of

    for _ in range(horizon if terms.accrues else 0):
        cut = first_cut_after(cursor, terms.statement_day)

        if terms.matures_on is not None and cut > terms.matures_on:
            break

        period = StatementPeriod(
            starts_on=cursor,
            ends_on=cut,
            partial=not is_cut(cursor, terms.statement_day),
        )
        accrued = accrue_period(
            period=period,
            opening=balance,
            rate=terms.rate,
            charges=terms.charges,
        )
        withheld = _sum(
            [charge.amount for charge in accrued.charged_to_balance],
            currency,
        )
        closing = Money(
            amount=max(
                balance.amount + accrued.interest.amount - withheld.amount,
                Decimal(0),
            ),
            currency=currency,
        )
        rows.append(
            ProjectedReturn(
                period=period,
                opening_balance=balance,
                earned=accrued.interest,
                charges=accrued.charges,
                closing_balance=closing,
            ),
        )
        earned_total += accrued.interest.amount
        charges_total += accrued.total_charges.amount
        balance = closing
        cursor = cut

    return InvestmentProjection(
        periods=tuple(rows),
        total_earned=Money(amount=earned_total, currency=currency),
        total_charges=Money(amount=charges_total, currency=currency),
        value_at_end=balance,
        matures_on=terms.matures_on,
    )


# --------------------------------------------------------------- posting


class AccrualKind(enum.Enum):
    """What a computed row is, for a reader deciding whether it is negotiable.

    Interest follows from a rate somebody agreed to; a charge is a product the
    loan carries. Both are real money, and telling them apart is what lets an
    owner see that a mortgage costs more than its rate.
    """

    INTEREST = "interest"
    CHARGE = "charge"


# The key an accrued row is identified by, which is deliberately not the words
# beside it: renaming a charge must not re-post the periods already written.
INTEREST_ITEM = "interest"


def charge_item(name: str) -> str:
    return f"charge:{name.strip().casefold()}"


# What a person reads in their own ledger, so it is in the language this
# deployment speaks rather than the one its code is written in: this text
# lands on the movement as its counterparty, beside `ÉXITO` and `RAPPI`. A
# charge brings its own name, because the owner copied it off a statement.
INTEREST_LABEL = "Intereses"
EARNINGS_LABEL = "Rendimientos"
VALUATION_LABEL = "Valoración"


def valuation_item(*, held: Decimal, stated: Decimal, turn: int = 1) -> str:
    """The key one revaluation is identified by: the move it makes, and which
    turn that move is taking today.

    Both ends of it, not the target alone. Stating the same figure twice from
    the same balance is one revaluation, so a double submit lands on a key the
    ledger already holds and is refused — which is the protection this exists
    for, because a doubled revaluation records a gain that never happened.

    Keying on the target alone looked equivalent and is not: state 11 000 000,
    then 15 000 000, then 11 000 000 again on one day, and the third writes
    the first one's key. It would be refused, the balance would stay at 15
    million, and the answer would report success. Two ends make every step of
    that sequence its own row.

    Two ends are not enough for the step after that one. Going back up to
    15 000 000 repeats the first move exactly — same balance, same target,
    same day — so it collided in turn, leaving the fund at 11 million under a
    successful answer. `turn` is what tells those apart: the caller takes the
    first turn, and only moves to the next one when the key is taken *and* the
    balance is not already the figure asked for. A double submit fails that
    second test — the winning request left the balance exactly there — so it
    is still refused on the first turn, unchanged. The first turn carries no
    suffix, which leaves every key already written alone.
    """
    if turn < 1:
        raise ValueError(f"A revaluation takes at least one turn, not {turn}")

    move = f"valuation:{held}->{stated}"

    return move if turn == 1 else f"{move}#{turn}"


@dataclasses.dataclass(frozen=True, slots=True)
class PostedAccrual(ValueObject):
    """One computed charge, ready to become a ledger row.

    Carries its direction rather than leaving it to the caller: which way
    interest moves a balance depends on which side of net worth the account
    sits on, and a caller free to decide that is a caller free to record a
    mortgage that pays its owner every month.
    """

    kind: AccrualKind
    item: str
    label: str
    direction: MovementDirection
    amount: Money


def postings(
    accrued: AccruedPeriod,
    *,
    category: AccountCategory,
) -> tuple[PostedAccrual, ...]:
    """The ledger rows one closed period becomes.

    The direction rule lives here, once. Interest follows the account's own
    side: on a liability it grows what is owed, on an asset it grows what is
    held. A charge always costs money, which on a liability is more owed and
    on an asset is less held — so a charge is outgoing either way, and
    `Account.apply` turns that into the right sign without being told.

    A charge the bank collects somewhere else is left out entirely: its own
    direct debit arrives as an alert on the account it was taken from, and
    posting it here as well would charge one insurance twice.

    Rows worth nothing are dropped. A period that charged no insurance because
    the debt is settled should leave nothing behind but the silence.
    """
    earns = category is AccountCategory.ASSET
    rows = [
        PostedAccrual(
            kind=AccrualKind.INTEREST,
            item=INTEREST_ITEM,
            label=EARNINGS_LABEL if earns else INTEREST_LABEL,
            direction=(
                MovementDirection.INCOMING if earns else MovementDirection.OUTGOING
            ),
            amount=accrued.interest,
        ),
    ]
    rows.extend(
        PostedAccrual(
            kind=AccrualKind.CHARGE,
            item=charge_item(charge.name),
            label=charge.name,
            direction=MovementDirection.OUTGOING,
            amount=charge.amount,
        )
        for charge in accrued.charged_to_balance
    )

    return tuple(row for row in rows if row.amount.amount > 0)
