"""One week of spending beside the weeks before it — against its owner, never
against an ideal.

Published for Alerts, which turns it into Monday's summary. The comparison is
the whole point: «gastaste $820.000» means nothing on its own, «12 % menos que
tu semana normal» is something somebody can act on, and the only honest
«normal» is their own.

**What "normal" is.** The average of the `history_weeks` weeks before this one,
counting a week only if the owner already had movements by then. Somebody who
started using the app two weeks ago has two weeks of history, not four with two
of them zero — averaging in weeks before they arrived would call every week of
theirs expensive. No history at all means there is no normal yet, and the answer
says so rather than inventing one.

Spending is what `/financial/summary` calls spending: outgoing, transfers
excluded, grouped by the merchant's category now. Nothing here is a second set
of rules.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from personal_finance.contexts.financial.application.export import CategoryNamer
from personal_finance.contexts.financial.application.financing import (
    local_midnight,
    zone_of,
)
from personal_finance.contexts.financial.application.ports import TransactionLedger
from personal_finance.contexts.financial.application.queries import (
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryGrouping,
    SummaryQuery,
    TransferView,
)
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, UserId


DEFAULT_HISTORY_WEEKS = 4

# Not a category anybody chose, so never "what went up": saying «lo que más
# subió: Sin categoría» tells its reader nothing they can do anything about.
_UNNAMED = frozenset({None, "uncategorized"})

_CENTS = Decimal("0.01")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WeeklySpendingQuery:
    user_id: UserId
    timezone: str
    #: Any day of the week to read; the week is Monday to Sunday around it.
    week_of: dt.date
    history_weeks: int = DEFAULT_HISTORY_WEEKS


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CategoryRise:
    """The category that went up the most against its own normal."""

    category: str
    #: What the category is called: the owner's own name for one they wrote,
    #: the API's English label for a shipped one, which a reader in Spanish
    #: restates from `category`.
    label: str
    spent: Decimal
    typical: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CurrencyWeek:
    currency: Currency
    spent: Decimal
    #: Outgoing movements this week, transfers excluded.
    movements: int
    #: The average week before this one, or None when there is no history yet.
    typical: Decimal | None
    #: How many weeks `typical` averages. Zero exactly when `typical` is None.
    history_weeks: int
    rise: CategoryRise | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WeeklySpending:
    #: Monday.
    week_start: dt.date
    #: Sunday, inclusive.
    week_end: dt.date
    #: The busiest first. Empty when nothing was spent this week or before it.
    currencies: Sequence[CurrencyWeek]

    @property
    def has_anything_to_say(self) -> bool:
        return bool(self.currencies)


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


class ReadWeeklySpendingUseCase:
    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        spending: SummarizeSpendingUseCase,
        categories: CategoryNamer | None = None,
    ) -> None:
        self._ledger = ledger
        self._spending = spending
        self._categories = categories

    def execute(self, query: WeeklySpendingQuery) -> WeeklySpending:
        zone = zone_of(query.timezone)
        start = monday_of(query.week_of)
        end = start + dt.timedelta(days=6)

        this_week = self._by_category(query, start, zone)
        arrived = self._first_movement_at(query.user_id)
        history = [
            self._by_category(query, start - dt.timedelta(weeks=back), zone)
            for back in range(1, query.history_weeks + 1)
            # Counted only if the owner had a movement before that week ended.
            if arrived is not None
            and arrived
            < local_midnight(
                start - dt.timedelta(weeks=back - 1),
                zone,
            ).as_epoch_seconds()
        ]
        labels: dict[str, str] = (
            {}
            if self._categories is None
            else dict(self._categories.category_labels(user_id=query.user_id))
        )

        currencies = {currency for (_, currency) in this_week}
        if history:
            currencies |= {currency for week in history for (_, currency) in week}

        weeks = [
            _currency_week(currency, this_week, history, labels)
            for currency in currencies
        ]
        weeks = [week for week in weeks if week.spent > 0 or week.typical]

        return WeeklySpending(
            week_start=start,
            week_end=end,
            # By how many movements, never by amount: 100 dollars against 300 000
            # pesos is a comparison of units. A count means the same in both.
            currencies=sorted(
                weeks,
                key=lambda week: (-week.movements, week.currency.value),
            ),
        )

    def _first_movement_at(self, user_id: UserId) -> int | None:
        """When the owner's first movement happened, of any kind, if any."""
        return min(
            (
                movement.occurred_at.as_epoch_seconds()
                for movement in self._ledger.list_all(user_id)
            ),
            default=None,
        )

    def _by_category(
        self,
        query: WeeklySpendingQuery,
        monday: dt.date,
        zone: dt.tzinfo,
    ) -> dict[tuple[str | None, Currency], tuple[Decimal, int]]:
        summary = self._spending.execute(
            SummaryQuery(
                filter=MovementFilter(
                    user_id=query.user_id,
                    transfers=TransferView.EXCLUDE,
                    direction=MovementDirection.OUTGOING,
                    since=local_midnight(monday, zone),
                    until=local_midnight(monday + dt.timedelta(weeks=1), zone),
                ),
                group_by=SummaryGrouping.CATEGORY,
                timezone=query.timezone,
            ),
        )

        return {
            (group.key, totals.currency): (totals.outgoing, totals.movements)
            for group in summary.groups
            for totals in group.totals
        }


def _currency_week(
    currency: Currency,
    this_week: dict[tuple[str | None, Currency], tuple[Decimal, int]],
    history: Sequence[dict[tuple[str | None, Currency], tuple[Decimal, int]]],
    labels: dict[str, str],
) -> CurrencyWeek:
    def spent(week: dict[tuple[str | None, Currency], tuple[Decimal, int]]) -> Decimal:
        return sum(
            (amount for (_, unit), (amount, _) in week.items() if unit is currency),
            Decimal(0),
        )

    weeks = len(history)
    typical = (
        None
        if weeks == 0
        else (sum((spent(week) for week in history), Decimal(0)) / weeks).quantize(
            _CENTS,
            rounding=ROUND_HALF_UP,
        )
    )

    return CurrencyWeek(
        currency=currency,
        spent=spent(this_week),
        movements=sum(
            count for (_, unit), (_, count) in this_week.items() if unit is currency
        ),
        typical=typical,
        history_weeks=weeks,
        rise=_rise(currency, this_week, history, labels),
    )


def _rise(
    currency: Currency,
    this_week: dict[tuple[str | None, Currency], tuple[Decimal, int]],
    history: Sequence[dict[tuple[str | None, Currency], tuple[Decimal, int]]],
    labels: dict[str, str],
) -> CategoryRise | None:
    """The named category furthest above its own average, if any went up."""
    if not history:
        return None

    best: CategoryRise | None = None
    best_by = Decimal(0)

    for (category, unit), (amount, _) in this_week.items():
        if unit is not currency or category in _UNNAMED or category is None:
            continue

        typical = (
            sum(
                (week.get((category, unit), (Decimal(0), 0))[0] for week in history),
                Decimal(0),
            )
            / len(history)
        ).quantize(_CENTS, rounding=ROUND_HALF_UP)
        by = amount - typical

        # Ties go to the name, so two reads of the same week agree.
        if by > best_by or (
            best is not None and by == best_by and category < best.category
        ):
            best = CategoryRise(
                category=category,
                label=labels.get(category, category),
                spent=amount,
                typical=typical,
            )
            best_by = by

    return best
