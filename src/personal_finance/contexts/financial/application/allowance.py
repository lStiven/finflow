"""«¿Cuánto puedo gastar?» — one number, and what it is made of.

The number the rest of this context was building towards, and **the most
dangerous one in the app**: if it lies once, nobody looks at it again. So two
rules shape everything here.

**It never invents a component.** Four figures go in and all four are already
somebody's answer: the income and the savings floor the owner declared, the
spending the ledger recorded, and the charges the bills say are still unpaid.
Nothing is estimated, and with no plan declared there is no number at all —
the screen shows nothing rather than a zero that reads like an answer.

**It says what it is made of.** The breakdown travels with the figure, every
component named, because a number somebody cannot take apart is a number they
cannot check — and the first time it disagrees with their own arithmetic they
stop believing it.

The subtraction that is easy to get wrong, and the reason this module exists
rather than a line in a router:

* **What is still owed, not what the month costs.** The bills view answers two
  figures per currency. `expected` is everything the month holds, paid charges
  included; `outstanding` is what nobody has answered for yet. Only the second
  one is subtracted. Taking the first would discount a confirmed charge twice
  — once as a commitment, and again as the ledger row confirming it wrote.
* **Only this month's spending, and never the transfers.** Moving money from
  savings to a card is not spending, and counting it would eat an allowance
  with a payment that left the owner exactly as rich as before.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.bills import (
    ListBillsQuery,
    ListBillsUseCase,
)
from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.ports import (
    MonthlyPlanRepository,
)
from personal_finance.contexts.financial.application.queries import (
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryGrouping,
    SummaryQuery,
    TransferView,
)
from personal_finance.contexts.financial.domain.plan import MonthlyPlan
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeclarePlanCommand:
    user_id: UserId
    expected_income: Money
    savings_target: Money | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MonthlyAllowance:
    """The figure, and every piece it was built from.

    Each component is here because the screen has to be able to show the
    subtraction rather than assert its result. `available` is what is left;
    everything above it is why.

    `days_left` counts today, because today is a day somebody still has to eat
    on. It is what turns the figure into the question actually being asked —
    "what can I spend *today*" — without this module having to answer that
    question itself.
    """

    currency: Currency
    #: Declared: what the month is expected to bring in.
    expected_income: Decimal
    #: Declared: the part that is not meant to be spent.
    savings_target: Decimal
    #: Recorded: what has already gone out this month, transfers excluded.
    spent: Decimal
    #: Derived from the declared bills: charges of this month nobody has
    #: answered for yet. Never the month's whole bill total — see the module
    #: docstring.
    committed: Decimal
    #: What is left. Can be negative, and is reported negative rather than
    #: floored at zero: somebody who has overspent needs to see by how much.
    available: Decimal
    #: The calendar month this is about, read in the caller's zone.
    since: dt.date
    until: dt.date
    days_left: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReadAllowanceQuery:
    user_id: UserId
    timezone: str


class ManageMonthlyPlanUseCase:
    """Declaring what the month is supposed to look like, and forgetting it.

    No amend. A plan is two figures that are both guesses, so restating it
    whole is the honest operation — and it makes it impossible to leave a
    savings target standing against an income it was never set against.
    """

    def __init__(self, *, plans: MonthlyPlanRepository) -> None:
        self._plans = plans

    def declare(self, command: DeclarePlanCommand) -> MonthlyPlan:
        plan = MonthlyPlan.declare(
            user_id=command.user_id,
            expected_income=command.expected_income,
            savings_target=command.savings_target,
        )
        self._plans.save(plan)

        return plan

    def read(self, user_id: UserId) -> MonthlyPlan | None:
        return self._plans.find(user_id=user_id)

    def forget(self, user_id: UserId) -> bool:
        """Take the plan back. The card disappears; nothing else changes.

        Deleting is right here for the reason it is right on a bill: a plan
        never wrote anything, so there is nothing left behind to explain.
        Returns False when there was nothing to forget, which the endpoint
        answers the same way either way — an undo whose work is already done
        has done its job.
        """
        return self._plans.remove(user_id=user_id)


class ReadMonthlyAllowanceUseCase:
    """What is left to spend this month, and the four figures behind it.

    Three reads, each of them one a screen already makes: the plan, the
    month's spending and the month's bills. They are joined here rather than
    in the browser because the join is where the mistake lives — subtracting
    the wrong one of the bills' two totals is invisible in the payload and
    wrong by exactly the charges already paid.

    None comes back when no plan is declared, and that is a real answer: the
    card is absent, not zero. A zero would read as "you have nothing left".
    """

    def __init__(
        self,
        *,
        plans: MonthlyPlanRepository,
        spending: SummarizeSpendingUseCase,
        bills: ListBillsUseCase,
    ) -> None:
        self._plans = plans
        self._spending = spending
        self._bills = bills

    def execute(self, query: ReadAllowanceQuery) -> MonthlyAllowance | None:
        plan = self._plans.find(user_id=query.user_id)

        if plan is None:
            return None

        zone = zone_of(query.timezone)
        today = today_in(zone)
        since, until = _month_of(today)
        spent = self._spent_this_month(
            plan,
            query=query,
            since=since,
            until=until,
            zone=zone,
        )
        committed = self._outstanding_this_month(
            plan,
            query=query,
            since=since,
            until=until,
        )

        return MonthlyAllowance(
            currency=plan.currency,
            expected_income=plan.expected_income.amount,
            savings_target=plan.savings_target.amount,
            spent=spent,
            committed=committed,
            available=plan.spendable - spent - committed,
            since=since,
            until=until,
            # Today counts: it is a day somebody still has to get through.
            days_left=(until - today).days + 1,
        )

    def _spent_this_month(
        self,
        plan: MonthlyPlan,
        *,
        query: ReadAllowanceQuery,
        since: dt.date,
        until: dt.date,
        zone: dt.tzinfo,
    ) -> Decimal:
        """What has gone out, in the plan's currency and nothing else.

        `TransferView.EXCLUDE`, spelled out because the type refuses a default:
        paying a card from savings is not spending, and an allowance that
        counted it would be eaten by a movement that left its owner exactly as
        rich as before.

        Grouped by month and read off the totals rather than the buckets —
        the grouping is irrelevant here, and `totals` is the same figure
        whatever it is.
        """
        summary = self._spending.execute(
            SummaryQuery(
                filter=MovementFilter(
                    user_id=query.user_id,
                    transfers=TransferView.EXCLUDE,
                    currency=plan.currency,
                    since=_local_start(since, zone),
                    # Half-open, like every window in this context: the first
                    # instant of the next month is excluded, so a purchase at
                    # ten to midnight on the last day is still this month.
                    until=_local_start(until + dt.timedelta(days=1), zone),
                ),
                group_by=SummaryGrouping.MONTH,
                timezone=query.timezone,
            ),
        )

        return next(
            (
                totals.outgoing
                for totals in summary.totals
                if totals.currency is plan.currency
            ),
            Decimal(0),
        )

    def _outstanding_this_month(
        self,
        plan: MonthlyPlan,
        *,
        query: ReadAllowanceQuery,
        since: dt.date,
        until: dt.date,
    ) -> Decimal:
        """The charges of this month nobody has answered for yet.

        **The one figure of the two that may be subtracted.** `expected` is
        what the month costs, paid charges included, and taking it would
        discount a confirmed charge twice: once here as a commitment, and
        again through the ledger row that confirming it wrote, which `spent`
        already counts.
        """
        view = self._bills.execute(
            ListBillsQuery(
                user_id=query.user_id,
                since=since,
                until=until,
                timezone=query.timezone,
            ),
        )

        return next(
            (
                total.outstanding
                for total in view.totals
                if total.currency is plan.currency
            ),
            Decimal(0),
        )


def _month_of(day: dt.date) -> tuple[dt.date, dt.date]:
    """The calendar month `day` falls in, both ends inclusive."""
    first = day.replace(day=1)
    following = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )

    return first, following - dt.timedelta(days=1)


def _local_start(day: dt.date, zone: dt.tzinfo) -> PosixTime:
    """The instant a calendar day begins where the owner lives.

    In UTC a Bogotá month would start five hours early, which puts the last
    evening of the previous month inside this one — and an allowance that
    counts last month's dinner is an allowance that is wrong on the 1st, which
    is the day somebody is most likely to look at it.
    """
    return PosixTime.from_datetime(
        dt.datetime.combine(day, dt.time(), tzinfo=zone),
    )
