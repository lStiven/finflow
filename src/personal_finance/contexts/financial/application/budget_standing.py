"""The budgets one movement counts against, and how each stands now.

Published for another context to call — Alerts, to say beside a purchase how
much is left of the budget it just ate into. That is why it answers from a
movement id rather than from a scope: which budgets a movement belongs to is a
question about categories, accounts and months, and all three are Financial's
to decide.

**A budget covers a movement when it would count it.** Nothing looser: the same
rules `ReadBudgetsUseCase` uses to add a month up are the ones that pick the
budgets here, and the figures come from that same read, so a message can never
say «te quedan $120.000» while the budgets screen says something else.

* Only spending. Income is not spent against anything, and a transfer moved
  money between the owner's own accounts — no budget counts either.
* The movement's category is the merchant's answer *now*. A movement no
  merchant owns yet has no category, and then only a budget over every
  category covers it; one naming categories cannot, since nobody has said
  which of them it belongs to.
* A budget narrowed to some accounts covers only movements on them. A movement
  on no account is covered only by a budget that watches every account.
* The month is the movement's own, where its owner lives: a bank alert for the
  31st that arrives on the 1st is read against the month it was spent in.
* Same currency, or not at all: a cap in pesos says nothing about dollars.

An empty answer is the ordinary one — most movements fall under no budget.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from decimal import Decimal

from personal_finance.contexts.financial.application.budgets import (
    ReadBudgetsQuery,
    ReadBudgetsUseCase,
)
from personal_finance.contexts.financial.application.financing import zone_of
from personal_finance.contexts.financial.application.ports import (
    MerchantDirectory,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.budgets import (
    BudgetState,
    month_containing,
)
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MovementBudgetsQuery:
    user_id: UserId
    movement_id: str
    timezone: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CoveringBudget:
    """One budget the movement counts against, with the month's figures.

    `spent` already includes the movement itself — it is in the ledger by the
    time anybody asks — and `remaining` is negative once the cap was passed,
    reported that way rather than floored, exactly as the budgets screen does.
    """

    name: str
    currency: Currency
    limit: Decimal
    spent: Decimal
    remaining: Decimal
    state: BudgetState
    #: The month read, `2026-09`: the movement's own.
    month: str


class ReadMovementBudgetsUseCase:
    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        budgets: ReadBudgetsUseCase,
        merchants: MerchantDirectory,
    ) -> None:
        self._ledger = ledger
        self._budgets = budgets
        self._merchants = merchants

    def execute(self, query: MovementBudgetsQuery) -> Sequence[CoveringBudget]:
        """Worst first, the order the budgets screen uses.

        Empty for a movement that does not exist, belongs to somebody else,
        is not spending, or falls under no budget — four answers a caller
        treats the same way, by saying nothing about budgets.
        """
        movement = self._ledger.find(
            user_id=query.user_id,
            transaction_id=query.movement_id,
        )

        if (
            movement is None
            or movement.direction is not MovementDirection.OUTGOING
            or movement.is_transfer
        ):
            return []

        attribution = self._merchants.attribute(
            user_id=query.user_id,
            counterparties=[movement.counterparty],
        ).get(movement.counterparty)
        category = None if attribution is None else attribution.category

        zone = zone_of(query.timezone)
        month = month_containing(
            movement.occurred_at.to_datetime().astimezone(zone).date()
        )
        view = self._budgets.execute(
            ReadBudgetsQuery(
                user_id=query.user_id, timezone=query.timezone, month=month
            ),
        )

        covering: list[CoveringBudget] = []

        for line in view.budgets:
            progress = line.progress
            scope = progress.scope

            if line.retired or progress.currency is not movement.amount.currency:
                continue

            if not scope.watches_category(category):
                continue

            if not scope.every_account and (
                movement.account_id is None or movement.account_id not in scope.accounts
            ):
                continue

            covering.append(
                CoveringBudget(
                    name=progress.name,
                    currency=progress.currency,
                    limit=progress.limit,
                    spent=progress.spent,
                    remaining=progress.remaining,
                    state=progress.state,
                    month=view.month,
                ),
            )

        return covering
