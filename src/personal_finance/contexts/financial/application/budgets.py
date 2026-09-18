"""Putting a ceiling on a category, and reading how the month is doing.

Three reads and no writes on the way out, which is the whole shape of this
module. A cap is two numbers somebody declared; what has been spent against it
is a question for the ledger, asked through **the same use case that draws the
breakdown on the summary screen**. Wiring a second one would be a second set of
rules about what counts as spending, and the budgets screen would quietly
disagree with Reportes about the same category on the same day.

Three subtleties carry everything here.

**This month's cap shadows the recurring one.** Somebody who allows themselves
$600.000 for restaurants every month and $900.000 in December has two rows, and
December reads the second. Resolving that here rather than in storage is what
lets the screen say «este mes: $900.000 · normalmente $600.000» instead of just
showing a number that is silently different from the one declared.

**What is compared is `outgoing`, never `net`.** A refund inside the category
would make the net read as under budget while the money did leave and came
back. Two facts, not none.

**And the caps are not a partition of the month.** `/summary` buckets movements
whose counterparty no merchant owns yet under a key of `None` — unknown, which
is not the same thing as the `uncategorized` category — so the capped
categories will not add up to the month's total outgoing. That is the honest
answer and the screen says so; a module that folded the unknown bucket into
`uncategorized` to make the arithmetic look tidy would be filing spending under
a category nobody chose.

A cap against a category its owner has since deleted is not an error and does
not take the screen down. It comes back marked `retired`, with a spend of
nothing — because deleting a category refiles its merchants under
`uncategorized`, so no movement is attributed to it any more — and the screen
offers to drop it. Merchant publishes nothing on a delete that Financial could
listen for, so degrading on read is the only place this can be handled.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.financing import (
    local_midnight,
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.ports import (
    CategoryBudgetRepository,
    MerchantDirectory,
)
from personal_finance.contexts.financial.application.queries import (
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryGrouping,
    SummaryQuery,
    TransferView,
)
from personal_finance.contexts.financial.domain.budgets import (
    DEFAULT_WARN_PERCENT,
    BudgetId,
    BudgetProgress,
    BudgetState,
    CategoryBudget,
    keyable_category,
    month_bounds,
    month_containing,
    valid_month,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    UserId,
)


# How many uncapped categories are offered as somewhere a cap might go. Five,
# because the point is the handful worth capping and not a second copy of the
# breakdown — the summary screen already draws that one, in full and ordered
# the same way.
MAX_SUGGESTIONS = 5

# Merchant's word for "nobody has filed this yet". Spelled here rather than
# imported, like every other category value that crosses the port: importing
# Merchant's enum would hand that context a veto over renaming its own members.
UNCATEGORIZED = "uncategorized"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SetBudgetCommand:
    user_id: UserId
    category: str
    limit: Money
    #: None caps every month. A key like `2026-09` caps that one only.
    month: str | None = None
    warn_at: int = DEFAULT_WARN_PERCENT


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReadBudgetsQuery:
    user_id: UserId
    timezone: str
    #: None reads the month today falls in, where the owner lives.
    month: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetLine:
    """One cap and the month against it.

    `retired` is the one thing the domain cannot answer: whether the category
    this caps is still in its owner's vocabulary. Merchant owns that list, and
    half of it is whatever this person wrote for themselves.
    """

    progress: BudgetProgress
    #: True when the category no longer exists. The cap is still shown — it is
    #: the record of a decision — but nothing will ever be spent against it.
    retired: bool = False


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UncappedCategory:
    """Somewhere a cap is missing, ranked by what actually goes out there.

    Offered rather than created. Nothing here declares a cap, exactly as the
    recurring detector declares no bills: the number is a suggestion in a field
    somebody edits.
    """

    category: str
    currency: Currency
    spent: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetTotals:
    """Every cap of one currency, added up, and how the three states split.

    Never summed across currencies — the rule this whole context keeps, because
    there is no exchange rate anywhere in it. The counts are what the card on
    the dashboard says out loud («3 de 5 en verde»), computed here so that card
    and the budgets screen cannot arrive at different tallies.
    """

    currency: Currency
    limit: Decimal
    spent: Decimal
    #: Can be negative: the caps together were passed by this much.
    remaining: Decimal
    ok: int
    warning: int
    over: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetsView:
    month: str
    since: dt.date
    until: dt.date
    #: Worst first: the closest to its ceiling leads, because that is the only
    #: one with something to do about it. Ties fall back to the category value
    #: so the order is stable between two reads of the same month.
    budgets: Sequence[BudgetLine]
    totals: Sequence[BudgetTotals]
    suggestions: Sequence[UncappedCategory]


class ManageBudgetsUseCase:
    """Declaring a cap and taking it back.

    No amend, like the monthly plan has none: a ceiling and the point it warns
    at are one statement, and half an update leaves a warning standing against
    a ceiling it was never set against.
    """

    def __init__(self, *, budgets: CategoryBudgetRepository) -> None:
        self._budgets = budgets

    def declare(self, command: SetBudgetCommand) -> CategoryBudget:
        budget = CategoryBudget.declare(
            user_id=command.user_id,
            category=command.category,
            limit=command.limit,
            month=command.month,
            warn_at=command.warn_at,
        )
        self._budgets.save(budget)

        return budget

    def forget(
        self,
        *,
        user_id: UserId,
        category: str,
        month: str | None = None,
    ) -> bool:
        """Drop one cap. False when there was nothing to drop.

        The month matters and is not a detail: dropping December's exception
        leaves the recurring cap exactly where it was, which is the point of
        the two being separate rows rather than one row with an override on it.
        """
        return self._budgets.remove(
            user_id=user_id,
            budget_id=BudgetId(
                # Through the domain's own rule rather than a bare `strip()`:
                # what may be half of a cap's identity is the domain's answer,
                # and a second spelling of it here is a gap the day something
                # other than the router calls this.
                category=keyable_category(category),
                month=valid_month(month),
            ),
        )


class ReadBudgetsUseCase:
    """Every cap that governs a month, and what the ledger did to it.

    Two reads that a screen already makes on its own — the caps, and the
    month's spending grouped by category — joined here because the join is
    where the mistakes live: which of two caps applies, which of two figures is
    compared, and which bucket of the breakdown is not a category at all.
    """

    def __init__(
        self,
        *,
        budgets: CategoryBudgetRepository,
        spending: SummarizeSpendingUseCase,
        merchants: MerchantDirectory,
    ) -> None:
        self._budgets = budgets
        self._spending = spending
        self._merchants = merchants

    def execute(self, query: ReadBudgetsQuery) -> BudgetsView:
        zone = zone_of(query.timezone)
        month = valid_month(query.month) or month_containing(today_in(zone))
        since, until = month_bounds(month)

        declared = _applicable(
            self._budgets.list_for_month(user_id=query.user_id, month=month),
            month=month,
        )
        spent = self._spent_by_category(
            query,
            since=since,
            until=until,
            zone=zone,
        )
        # Asked once, never once per cap: the vocabulary is a real read of this
        # person's category rows through the Merchant adapter.
        vocabulary = self._merchants.categories(user_id=query.user_id)

        lines = [
            BudgetLine(
                progress=budget.progress(
                    spent.get((budget.category, budget.currency), Decimal(0)),
                ),
                retired=budget.category not in vocabulary,
            )
            for budget in declared
        ]

        ordered = sorted(lines, key=_urgency)

        return BudgetsView(
            month=month,
            since=since,
            until=until,
            budgets=ordered,
            totals=_totals(ordered),
            suggestions=_suggestions(
                spent,
                capped={budget.category for budget in declared},
                vocabulary=vocabulary,
            ),
        )

    def _spent_by_category(
        self,
        query: ReadBudgetsQuery,
        *,
        since: dt.date,
        until: dt.date,
        zone: dt.tzinfo,
    ) -> dict[tuple[str, Currency], Decimal]:
        """What went out of each category this month, keyed with its currency.

        `TransferView.EXCLUDE`, spelled out because the type refuses a default:
        paying a card from savings is not spending, and a cap that counted it
        would be eaten by a movement that left its owner exactly as rich as
        before.

        The currency is **not** pinned in the filter. Each cap carries its own,
        and one query answering every currency is what lets a cap in dollars
        and one in pesos be read in the same request without either of them
        being compared against the other's spending.
        """
        summary = self._spending.execute(
            SummaryQuery(
                filter=MovementFilter(
                    user_id=query.user_id,
                    transfers=TransferView.EXCLUDE,
                    since=local_midnight(since, zone),
                    # Half-open, like every window in this context: the first
                    # instant of the next month is excluded, so a purchase at
                    # ten to midnight on the last day is still this month.
                    until=local_midnight(until + dt.timedelta(days=1), zone),
                ),
                group_by=SummaryGrouping.CATEGORY,
                timezone=query.timezone,
            ),
        )

        return {
            (group.key, totals.currency): totals.outgoing
            for group in summary.groups
            # The bucket with no key is the movements no merchant owns yet.
            # Unknown is not `uncategorized`, and folding one into the other
            # would file somebody's spending under a category they never chose.
            if group.key is not None
            for totals in group.totals
        }


def _applicable(
    declared: Sequence[CategoryBudget],
    *,
    month: str,
) -> Sequence[CategoryBudget]:
    """One cap per category: this month's own when there is one, else the
    recurring one.

    Written as two passes rather than one, because the order of the rows coming
    out of storage must not decide which cap wins. A dict built from the
    recurring ones and then updated with the month's own says the rule out
    loud: the exception is what the month reads.
    """
    winners = {budget.category: budget for budget in declared if budget.recurring}
    winners.update(
        {budget.category: budget for budget in declared if budget.month == month},
    )

    return list(winners.values())


def _urgency(line: BudgetLine) -> tuple[Decimal, str]:
    """Worst first, and stable.

    The share of the cap already spent, negated so the biggest sorts first,
    with the category value breaking a tie — two caps at the same share must
    not swap places between two reads of the same month.

    The cap cannot be zero: the domain refuses one, which is what makes this
    division safe.
    """
    progress = line.progress

    return (-(progress.spent / progress.limit), progress.category)


def _totals(lines: Sequence[BudgetLine]) -> Sequence[BudgetTotals]:
    """One entry per currency, in the order the currencies first appear.

    Handed the already-ordered lines, so insertion order means what it says:
    the currency somebody's worst cap is in leads.

    **A retired cap is left out.** Its category no longer exists, so nothing
    will ever be spent against it — counting it would report it as one more cap
    in the green and add its ceiling to what the month is allowed, which is a
    tally about a decision that has stopped being one. It is still in `budgets`,
    because it is the record of that decision and the screen has to offer to
    drop it.
    """
    running: dict[Currency, _Running] = {}

    for line in lines:
        if line.retired:
            continue

        running.setdefault(line.progress.currency, _Running()).add(line.progress)

    return [figures.close(currency) for currency, figures in running.items()]


@dataclasses.dataclass(slots=True)
class _Running:
    """One currency's caps while they are still being added up.

    Mutable and private, which is the one place this module allows it: the
    alternative is folding five figures through a tuple, and a tuple whose
    third slot means "how many are amber" is a line nobody can read.
    """

    limit: Decimal = Decimal(0)
    spent: Decimal = Decimal(0)
    states: dict[BudgetState, int] = dataclasses.field(
        default_factory=lambda: {},
    )

    def add(self, progress: BudgetProgress) -> None:
        self.limit += progress.limit
        self.spent += progress.spent
        self.states[progress.state] = self.states.get(progress.state, 0) + 1

    def close(self, currency: Currency) -> BudgetTotals:
        return BudgetTotals(
            currency=currency,
            limit=self.limit,
            spent=self.spent,
            remaining=self.limit - self.spent,
            ok=self.states.get(BudgetState.OK, 0),
            warning=self.states.get(BudgetState.WARNING, 0),
            over=self.states.get(BudgetState.OVER, 0),
        )


def _suggestions(
    spent: dict[tuple[str, Currency], Decimal],
    *,
    capped: set[str],
    vocabulary: frozenset[str],
) -> Sequence[UncappedCategory]:
    """Where the money actually goes and no cap is watching, biggest first.

    Filtered against the vocabulary as well as against the caps: a category
    somebody deleted can still be the one a movement was attributed to at the
    moment the summary was built, and offering to cap a category that no longer
    exists is offering a form that cannot be submitted.

    `uncategorized` is left out for the same reason and a different one. It is
    a real value the vocabulary carries and a cap on it would be stored — but
    it is the absence of a decision rather than a kind of spending, and a
    ceiling on "everything I have not filed yet" says nothing anybody can act
    on. It is also what the form's own picker leaves out, so offering it here
    would open a form with no category selected that would nonetheless submit.
    """
    offers = [
        UncappedCategory(category=category, currency=currency, spent=outgoing)
        for (category, currency), outgoing in spent.items()
        if outgoing > 0
        and category not in capped
        and category != UNCATEGORIZED
        and category in vocabulary
    ]

    return sorted(offers, key=lambda offer: (-offer.spent, offer.category))[
        :MAX_SUGGESTIONS
    ]
