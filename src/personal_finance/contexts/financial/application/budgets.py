"""Putting a ceiling on part of somebody's spending, and reading how the month
is doing against it.

Reads on the way out and no writes, which is the whole shape of this module. A
budget is a number and what it watches; what has been spent against it is a
question for the ledger, asked through **the same use case that draws the
breakdown on the summary screen**. Wiring a second one would be a second set of
rules about what counts as spending, and the budgets screen would quietly
disagree with Reportes about the same category on the same day.

Three subtleties carry everything here.

**Every budget that governs the month applies.** There is no shadowing any
more. A cap for one month used to hide the recurring one for its category, and
that rule could not survive scopes that overlap on purpose: with «Salidas» over
three categories and «Restaurantes» over one of them, there is no honest answer
to which hides which. December's exception is now its own budget, read beside
the usual one rather than instead of it.

**What is compared is `outgoing`, never `net`.** A refund inside the scope
would make the net read as under budget while the money did leave and came
back. Two facts, not none.

**And the budgets are not a partition of the month.** They may overlap each
other and they may leave gaps, so nothing here adds up to the month's total
outgoing and nothing should try. A module that forced the sum to tie would be
filing somebody's spending under a decision they never made.

Reading what a scope spent
--------------------------

One summary query per **distinct account scope**, not one per budget. Almost
everybody's budgets all watch every account, which is one query for the whole
screen; a budget narrowed to a card costs one more. The bound is the number of
distinct account scopes somebody declared, never the number of budgets.

Within a scope the answer is grouped by category once and then summed per
budget, because the categories a budget names are a set membership test and not
a second trip to the database.

A budget whose categories its owner has since deleted is not an error and does
not take the screen down. It comes back marked `retired`, with a spend of
nothing — deleting a category refiles its merchants under `uncategorized`, so
no movement is attributed to it any more — and the screen offers to drop it.
Merchant publishes nothing on a delete that Financial could listen for, so
degrading on read is the only place this can be handled.
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
    BudgetRepository,
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
    Budget,
    BudgetId,
    BudgetProgress,
    BudgetScope,
    BudgetState,
    month_bounds,
    month_containing,
    valid_month,
)
from personal_finance.contexts.financial.domain.value_objects import AccountId
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    UserId,
)


# How many uncapped categories are offered as somewhere a budget might go.
# Five, because the point is the handful worth capping and not a second copy of
# the breakdown — the summary screen already draws that one, in full.
MAX_SUGGESTIONS = 5

# Merchant's word for "nobody has filed this yet". Spelled here rather than
# imported, like every other category value that crosses the port: importing
# Merchant's enum would hand that context a veto over renaming its own members.
UNCATEGORIZED = "uncategorized"

#: What `SummarizeSpendingUseCase` keys the bucket of movements no merchant
#: owns yet. Unknown, which is not the `uncategorized` category.
UNKNOWN: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeclareBudgetCommand:
    user_id: UserId
    name: str
    limit: Money
    scope: BudgetScope
    icon: str = ""
    #: None governs every month. A key like `2026-09` governs that one only.
    month: str | None = None
    warn_at: int = DEFAULT_WARN_PERCENT


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AmendBudgetCommand:
    """Restating a budget whole, which is the only way to change one.

    Every field, never a subset — the shape the monthly plan uses, for the same
    reason: a ceiling and the point it warns at are one statement.
    """

    user_id: UserId
    budget_id: BudgetId
    name: str
    limit: Money
    scope: BudgetScope
    icon: str = ""
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
    """One budget and the month against it.

    `retired` and `missing` are the two things the domain cannot answer:
    whether the categories this watches are still in its owner's vocabulary.
    Merchant owns that list, and half of it is whatever this person wrote.
    """

    progress: BudgetProgress
    #: True when **every** category this names has been deleted. The budget is
    #: still shown — it is the record of a decision — but nothing will ever be
    #: spent against it. A budget over everything can never be retired.
    retired: bool = False
    #: The categories this names that no longer exist. Non-empty without
    #: `retired` is the partial case: «Salidas» lost one of its three, and the
    #: screen marks that one rather than the budget.
    missing: frozenset[str] = frozenset()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UncappedCategory:
    """Somewhere a budget is missing, ranked by what actually goes out there.

    Offered rather than created. Nothing here declares anything, exactly as the
    recurring detector declares no bills: the number is a suggestion in a field
    somebody edits.
    """

    category: str
    currency: Currency
    spent: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BudgetTotals:
    """Every budget of one currency, added up, and how the three states split.

    Never summed across currencies — the rule this whole context keeps, because
    there is no exchange rate anywhere in it. The counts are what the card on
    the dashboard says out loud («3 de 5 en verde»), computed here so that card
    and the budgets screen cannot arrive at different tallies.

    **The added-up ceiling is not what the month allows.** Budgets may overlap,
    so two of them can count the same peso. It is a tally of declarations, and
    the screen says so.
    """

    currency: Currency
    limit: Decimal
    spent: Decimal
    #: Can be negative: the budgets together were passed by this much.
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
    #: one with something to do about it. Ties fall back to the name so the
    #: order is stable between two reads of the same month.
    budgets: Sequence[BudgetLine]
    totals: Sequence[BudgetTotals]
    suggestions: Sequence[UncappedCategory]


class BudgetNotFoundError(Exception):
    """Amending or dropping something that is not there.

    Its own error rather than a `False` return, because amending has a body to
    validate and a caller that cannot tell «no existe» from «no cambió nada»
    would answer 200 to an edit that went nowhere.
    """


class ManageBudgetsUseCase:
    """Declaring a budget, restating it, and taking it back."""

    def __init__(self, *, budgets: BudgetRepository) -> None:
        self._budgets = budgets

    def declare(self, command: DeclareBudgetCommand) -> Budget:
        budget = Budget.declare(
            user_id=command.user_id,
            name=command.name,
            limit=command.limit,
            scope=command.scope,
            icon=command.icon,
            month=command.month,
            warn_at=command.warn_at,
        )
        self._budgets.save(budget)

        return budget

    def amend(self, command: AmendBudgetCommand) -> Budget:
        """Restate one budget, keeping its identity.

        Read before written, and the read is scoped to the owner: a budget id
        is a uuid somebody could paste, and loading by id alone would let one
        person amend another's ceiling.
        """
        budget = self._budgets.get(
            user_id=command.user_id,
            budget_id=command.budget_id,
        )

        if budget is None:
            raise BudgetNotFoundError(str(command.budget_id))

        budget.amend(
            name=command.name,
            limit=command.limit,
            scope=command.scope,
            icon=command.icon,
            month=command.month,
            warn_at=command.warn_at,
        )
        self._budgets.save(budget)

        return budget

    def forget(self, *, user_id: UserId, budget_id: BudgetId) -> bool:
        """Drop one budget. False when there was nothing to drop."""
        return self._budgets.remove(user_id=user_id, budget_id=budget_id)


class ReadBudgetsUseCase:
    """Every budget that governs a month, and what the ledger did to each.

    Two reads that a screen already makes on its own — the budgets, and the
    month's spending grouped by category — joined here because the join is
    where the mistakes live: which budgets apply, which figure is compared, and
    which bucket of the breakdown is not a category at all.
    """

    def __init__(
        self,
        *,
        budgets: BudgetRepository,
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

        declared = [
            budget
            for budget in self._budgets.list_for_user(user_id=query.user_id)
            if budget.governs(month)
        ]
        spent = self._spent_by_scope(
            query,
            scopes={budget.scope.accounts for budget in declared},
            since=since,
            until=until,
            zone=zone,
        )
        # Asked once, never once per budget: the vocabulary is a real read of
        # this person's category rows through the Merchant adapter.
        vocabulary = self._merchants.categories(user_id=query.user_id)

        lines = [self._line(budget, spent, vocabulary) for budget in declared]
        ordered = sorted(lines, key=_urgency)

        return BudgetsView(
            month=month,
            since=since,
            until=until,
            budgets=ordered,
            totals=_totals(ordered),
            suggestions=_suggestions(
                spent.get(frozenset(), {}),
                capped=_capped(declared),
                vocabulary=vocabulary,
            ),
        )

    def _line(
        self,
        budget: Budget,
        spent: dict[frozenset[AccountId], dict[tuple[str | None, Currency], Decimal]],
        vocabulary: frozenset[str],
    ) -> BudgetLine:
        """One budget, its spending and whether its vocabulary still exists."""
        within = spent.get(budget.scope.accounts, {})
        total = sum(
            (
                outgoing
                for (category, currency), outgoing in within.items()
                if currency == budget.currency
                and budget.scope.watches_category(category)
            ),
            Decimal(0),
        )
        missing = frozenset(
            category
            for category in budget.scope.categories
            if category not in vocabulary
        )

        return BudgetLine(
            progress=budget.progress(total),
            # A budget over everything can never retire: there is no named
            # category for its owner to delete out from under it.
            retired=(
                bool(budget.scope.categories) and missing == budget.scope.categories
            ),
            missing=missing,
        )

    def _spent_by_scope(
        self,
        query: ReadBudgetsQuery,
        *,
        scopes: set[frozenset[AccountId]],
        since: dt.date,
        until: dt.date,
        zone: dt.tzinfo,
    ) -> dict[frozenset[AccountId], dict[tuple[str | None, Currency], Decimal]]:
        """What went out of each account scope this month, grouped by category.

        One entry per distinct account scope, so two budgets watching the same
        accounts share one query. The empty scope — every account — is always
        computed, because the suggestions are drawn from it and because most
        budgets are in it.

        The currency is **not** pinned in the filter. Each budget carries its
        own, and one query answering every currency is what lets a budget in
        dollars and one in pesos be read in the same request without either
        being compared against the other's spending.
        """
        # Spelled out rather than written inline: an empty `frozenset()` in a
        # set literal has no element type for the checker to infer.
        every_account: frozenset[AccountId] = frozenset()

        return {
            scope: self._outgoing(query, scope, since=since, until=until, zone=zone)
            for scope in scopes | {every_account}
        }

    def _outgoing(
        self,
        query: ReadBudgetsQuery,
        accounts: frozenset[AccountId],
        *,
        since: dt.date,
        until: dt.date,
        zone: dt.tzinfo,
    ) -> dict[tuple[str | None, Currency], Decimal]:
        """One account scope's spending, by category and currency.

        An empty scope is one query over everything. A scope naming accounts is
        one query per account, added together: `MovementFilter` narrows to a
        single account, and asking for two is two questions.
        """
        windows = (
            [None]
            if not accounts
            else sorted(accounts, key=lambda account: str(account.value))
        )
        gathered: dict[tuple[str | None, Currency], Decimal] = {}

        for account in windows:
            summary = self._spending.execute(
                SummaryQuery(
                    filter=MovementFilter(
                        user_id=query.user_id,
                        transfers=TransferView.EXCLUDE,
                        account_id=account,
                        since=local_midnight(since, zone),
                        # Half-open, like every window in this context: the
                        # first instant of the next month is excluded, so a
                        # purchase at ten to midnight on the last day is still
                        # this month.
                        until=local_midnight(until + dt.timedelta(days=1), zone),
                    ),
                    group_by=SummaryGrouping.CATEGORY,
                    timezone=query.timezone,
                ),
            )

            for group in summary.groups:
                for totals in group.totals:
                    key = (group.key, totals.currency)
                    gathered[key] = gathered.get(key, Decimal(0)) + totals.outgoing

        return gathered


def _capped(declared: Sequence[Budget]) -> frozenset[str]:
    """Every category some budget already watches.

    A budget over everything caps no category *by name*, so it never suppresses
    a suggestion: somebody with a ceiling on the whole month may still want one
    on restaurants, and that is the second budget they will declare.
    """
    return frozenset(
        category for budget in declared for category in budget.scope.categories
    )


def _urgency(line: BudgetLine) -> tuple[Decimal, str]:
    """Worst first, and stable.

    The share of the ceiling already spent, negated so the biggest sorts first,
    with the name breaking a tie — two budgets at the same share must not swap
    places between two reads of the same month.

    The ceiling cannot be zero: the domain refuses one, which is what makes
    this division safe.
    """
    progress = line.progress

    return (-(progress.spent / progress.limit), progress.name)


def _totals(lines: Sequence[BudgetLine]) -> Sequence[BudgetTotals]:
    """One entry per currency, in the order the currencies first appear.

    Handed the already-ordered lines, so insertion order means what it says:
    the currency somebody's worst budget is in leads.

    **A retired budget is left out.** Every category it named is gone, so
    nothing will ever be spent against it — counting it would report it as one
    more budget in the green and add its ceiling to what the month is allowed,
    which is a tally about a decision that has stopped being one. It is still
    in `budgets`, because it is the record of that decision and the screen has
    to offer to drop it.
    """
    running: dict[Currency, _Running] = {}

    for line in lines:
        if line.retired:
            continue

        running.setdefault(line.progress.currency, _Running()).add(line.progress)

    return [figures.close(currency) for currency, figures in running.items()]


@dataclasses.dataclass(slots=True)
class _Running:
    """One currency's budgets while they are still being added up.

    Mutable and private, which is the one place this module allows it: the
    alternative is folding five figures through a tuple, and a tuple whose
    third slot means "how many are amber" is a line nobody can read.
    """

    limit: Decimal = Decimal(0)
    spent: Decimal = Decimal(0)
    states: dict[BudgetState, int] = dataclasses.field(default_factory=lambda: {})

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
    spent: dict[tuple[str | None, Currency], Decimal],
    *,
    capped: frozenset[str],
    vocabulary: frozenset[str],
) -> Sequence[UncappedCategory]:
    """Where the money actually goes and no budget is watching, biggest first.

    Drawn from the every-account scope, which is the only one that sees all of
    somebody's spending.

    Filtered against the vocabulary as well as against the budgets: a category
    somebody deleted can still be the one a movement was attributed to at the
    moment the summary was built, and offering to cap a category that no longer
    exists is offering a form that cannot be submitted.

    The unknown bucket is left out because it is not a category at all, and
    `uncategorized` for a different reason: it is a real value the vocabulary
    carries and a budget on it would store — but it is the absence of a
    decision rather than a kind of spending, and a ceiling on "everything I
    have not filed yet" says nothing anybody can act on.
    """
    offers = [
        UncappedCategory(category=category, currency=currency, spent=outgoing)
        for (category, currency), outgoing in spent.items()
        if category is not UNKNOWN
        and outgoing > 0
        and category not in capped
        and category != UNCATEGORIZED
        and category in vocabulary
    ]

    return sorted(offers, key=lambda offer: (-offer.spent, offer.category))[
        :MAX_SUGGESTIONS
    ]
