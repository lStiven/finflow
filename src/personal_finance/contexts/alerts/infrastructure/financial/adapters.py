"""The one place Alerts is allowed to know Financial exists.

Alerts asks three questions only Financial can answer — which budgets a
movement ate into, what a week of spending looked like against the ones
before, and which movements still exist — and it asks them through
Financial's own published use cases, never its repositories or its rules.
This module translates both ways: Alerts' vocabulary goes in, Financial's
answer comes back as Alerts' own types, and nothing of Financial's escapes
past these classes.

Building the use cases needs Financial's storage, which is why the wiring
lives here, in Alerts' infrastructure, rather than anywhere a use case could
see it — the same shape as Financial's own adapter to Merchant.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
import functools

from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    BudgetState,
    CategoryRise,
    WeeklySummary,
)
from personal_finance.contexts.financial.application.budget_standing import (
    MovementBudgetsQuery,
    ReadMovementBudgetsUseCase,
)
from personal_finance.contexts.financial.application.budgets import ReadBudgetsUseCase
from personal_finance.contexts.financial.application.movement_presence import (
    ExistingMovementsUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.application.weekly_spending import (
    ReadWeeklySpendingUseCase,
    WeeklySpendingQuery,
)
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    build_merchant_directory,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    DynamoDBAccountRepository,
    DynamoDBBudgetRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_financial_settings,
)


class FinancialBudgetStandings:
    """`BudgetStandings` over Financial's `ReadMovementBudgetsUseCase`."""

    def __init__(self, *, use_case: ReadMovementBudgetsUseCase, timezone: str) -> None:
        self._use_case = use_case
        self._timezone = timezone

    def covering(
        self,
        *,
        user_id: UserId,
        movement_id: str,
    ) -> Sequence[BudgetStanding]:
        return [
            BudgetStanding(
                name=budget.name,
                currency=budget.currency,
                limit=budget.limit,
                spent=budget.spent,
                remaining=budget.remaining,
                # By value: Financial's enum stops at this line.
                state=BudgetState(budget.state.value),
            )
            for budget in self._use_case.execute(
                MovementBudgetsQuery(
                    user_id=user_id,
                    movement_id=movement_id,
                    timezone=self._timezone,
                ),
            )
        ]


class FinancialMovementPresence:
    """`MovementPresence` over Financial's `ExistingMovementsUseCase`."""

    def __init__(self, *, use_case: ExistingMovementsUseCase) -> None:
        self._use_case = use_case

    def existing(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> frozenset[str]:
        return self._use_case.execute(user_id=user_id, movement_ids=movement_ids)


class FinancialWeeklySpending:
    """`WeeklySpendingSource` over Financial's `ReadWeeklySpendingUseCase`."""

    def __init__(self, *, use_case: ReadWeeklySpendingUseCase, timezone: str) -> None:
        self._use_case = use_case
        self._timezone = timezone

    def week(self, *, user_id: UserId, week_of: dt.date) -> Sequence[WeeklySummary]:
        spending = self._use_case.execute(
            WeeklySpendingQuery(
                user_id=user_id,
                timezone=self._timezone,
                week_of=week_of,
            ),
        )

        return [
            WeeklySummary(
                week_start=spending.week_start,
                week_end=spending.week_end,
                currency=week.currency,
                spent=week.spent,
                movements=week.movements,
                typical=week.typical,
                rise=(
                    None
                    if week.rise is None
                    else CategoryRise(
                        category=week.rise.category,
                        label=week.rise.label,
                        spent=week.rise.spent,
                        typical=week.rise.typical,
                    )
                ),
            )
            for week in spending.currencies
        ]


@functools.lru_cache(maxsize=1)
def _financial_parts() -> tuple[
    DynamoDBTransactionLedger,
    SummarizeSpendingUseCase,
    ReadBudgetsUseCase,
]:
    client = get_dynamodb_client()
    table_name = get_financial_settings().accounts_table
    ledger = DynamoDBTransactionLedger(client=client, table_name=table_name)
    directory = build_merchant_directory()
    spending = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=DynamoDBAccountRepository(client=client, table_name=table_name),
        merchants=directory,
    )
    budgets = ReadBudgetsUseCase(
        budgets=DynamoDBBudgetRepository(client=client, table_name=table_name),
        spending=spending,
        merchants=directory,
    )

    return ledger, spending, budgets


def build_budget_standings(*, timezone: str) -> FinancialBudgetStandings:
    ledger, _, budgets = _financial_parts()

    return FinancialBudgetStandings(
        use_case=ReadMovementBudgetsUseCase(
            ledger=ledger,
            budgets=budgets,
            merchants=build_merchant_directory(),
        ),
        timezone=timezone,
    )


def build_weekly_spending(*, timezone: str) -> FinancialWeeklySpending:
    ledger, spending, _ = _financial_parts()

    return FinancialWeeklySpending(
        use_case=ReadWeeklySpendingUseCase(
            ledger=ledger,
            spending=spending,
            categories=build_merchant_directory(),
        ),
        timezone=timezone,
    )


def build_movement_presence() -> FinancialMovementPresence:
    ledger, _, _ = _financial_parts()

    return FinancialMovementPresence(use_case=ExistingMovementsUseCase(ledger=ledger))
