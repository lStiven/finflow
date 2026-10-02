"""Which budgets a movement counts against, and how each stands — the answer
an alert puts beside a purchase. Only a budget that would count the movement
may be named; naming one that does not is a wrong number on somebody's phone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.budget_standing import (
    CoveringBudget,
    MovementBudgetsQuery,
    ReadMovementBudgetsUseCase,
)
from personal_finance.contexts.financial.application.budgets import ReadBudgetsUseCase
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.budgets import (
    Budget,
    BudgetId,
    BudgetScope,
    BudgetState,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.new()
STRANGER = UserId.new()
TIMEZONE = "America/Bogota"

# 2026-09-10 12:00 in Bogotá.
SEPTEMBER = PosixTime.from_datetime(dt.datetime(2026, 9, 10, 17, tzinfo=dt.UTC))
# 2026-08-31 20:00 in Bogotá — already 1 September in UTC.
AUGUST_LAST_NIGHT = PosixTime.from_datetime(dt.datetime(2026, 9, 1, 1, tzinfo=dt.UTC))

ARA = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Ara",
    category="groceries",
    needs_review=False,
)
ANDRES = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000002",
    display_name="Andrés Carne de Res",
    category="restaurants",
    needs_review=False,
)


class FakeBudgets:
    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, BudgetId], Budget] = {}

    def list_for_user(self, *, user_id: UserId) -> Sequence[Budget]:
        return [budget for (owner, _), budget in self.rows.items() if owner == user_id]

    def get(self, *, user_id: UserId, budget_id: BudgetId) -> Budget | None:
        return self.rows.get((user_id, budget_id))

    def save(self, budget: Budget) -> None:
        self.rows[(budget.user_id, budget.id)] = budget

    def remove(self, *, user_id: UserId, budget_id: BudgetId) -> bool:
        return self.rows.pop((user_id, budget_id), None) is not None


class FakeDirectory:
    def __init__(self, known: Mapping[str, MerchantAttribution]) -> None:
        self._known = known

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            text: self._known[text] for text in counterparties if text in self._known
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return frozenset({"groceries", "restaurants", "uncategorized"})

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        raise NotImplementedError


class InMemoryLedger:
    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def keep(self, movement: Transaction) -> Transaction:
        self.rows[movement.id.value] = movement

        return movement

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row if row is not None and row.user_id == user_id else None

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id == account_id
        ]

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id is None
        ]

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        raise NotImplementedError

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        raise NotImplementedError

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        raise NotImplementedError

    def save(self, transaction: Transaction) -> None:
        raise NotImplementedError

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        raise NotImplementedError


class FakeAccounts:
    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return None

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return []

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        raise NotImplementedError

    def save(self, account: Account) -> None:
        raise NotImplementedError

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        raise NotImplementedError

    def overwrite_balance(self, account: Account) -> None:
        raise NotImplementedError

    def restate_balance(self, account: Account) -> None:
        raise NotImplementedError

    def add(self, account: Account) -> bool:
        raise NotImplementedError


class World:
    def __init__(self) -> None:
        self.ledger = InMemoryLedger()
        self.budgets = FakeBudgets()
        self.directory = FakeDirectory({"ARA 123": ARA, "ANDRES DC": ANDRES})

    def spend(
        self,
        amount: str,
        *,
        counterparty: str = "ARA 123",
        at: PosixTime = SEPTEMBER,
        currency: Currency = Currency.COP,
        direction: MovementDirection = MovementDirection.OUTGOING,
        account_id: AccountId | None = None,
        user_id: UserId = USER,
    ) -> Transaction:
        return self.ledger.keep(
            Transaction.enter_manually(
                user_id=user_id,
                direction=direction,
                amount=Money(amount=Decimal(amount), currency=currency),
                occurred_at=at,
                counterparty=counterparty,
                account_id=account_id,
            ),
        )

    def cap(
        self,
        name: str,
        *,
        limit: str = "600000",
        categories: frozenset[str] | None = None,
        accounts: frozenset[AccountId] | None = None,
        month: str | None = None,
        currency: Currency = Currency.COP,
    ) -> Budget:
        budget = Budget.declare(
            user_id=USER,
            name=name,
            limit=Money(amount=Decimal(limit), currency=currency),
            scope=BudgetScope.of(categories=categories, accounts=accounts),
            month=month,
        )
        self.budgets.save(budget)

        return budget

    def covering(self, movement: Transaction, *, user_id: UserId = USER) -> list[str]:
        return [budget.name for budget in self.standing(movement, user_id=user_id)]

    def standing(
        self,
        movement: Transaction,
        *,
        user_id: UserId = USER,
    ) -> list[CoveringBudget]:
        use_case = ReadMovementBudgetsUseCase(
            ledger=self.ledger,
            budgets=ReadBudgetsUseCase(
                budgets=self.budgets,
                spending=SummarizeSpendingUseCase(
                    ledger=self.ledger,
                    accounts=FakeAccounts(),
                    merchants=self.directory,
                ),
                merchants=self.directory,
            ),
            merchants=self.directory,
        )

        return list(
            use_case.execute(
                MovementBudgetsQuery(
                    user_id=user_id,
                    movement_id=movement.id.value,
                    timezone=TIMEZONE,
                ),
            ),
        )


def _account() -> AccountId:
    return Account.open(
        user_id=USER,
        name="Tarjeta",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=SEPTEMBER,
    ).id


# ---------------------------------------------------------------- coverage


def test_nothing_is_said_when_no_budget_exists() -> None:
    world = World()

    assert world.covering(world.spend("50000")) == []


def test_a_budget_on_the_movements_category_covers_it_with_the_months_figures() -> None:
    world = World()
    world.spend(
        "100000", at=PosixTime.from_datetime(dt.datetime(2026, 9, 2, 17, tzinfo=dt.UTC))
    )
    purchase = world.spend("84300")
    world.cap("Mercado", categories=frozenset({"groceries"}))

    [mercado] = world.standing(purchase)

    assert mercado.name == "Mercado"
    assert mercado.month == "2026-09"
    # The movement itself is already spent: it is in the ledger.
    assert mercado.spent == Decimal("184300")
    assert mercado.remaining == Decimal("415700")
    assert mercado.limit == Decimal("600000")
    assert mercado.state is BudgetState.OK


def test_a_budget_on_another_category_says_nothing() -> None:
    world = World()
    purchase = world.spend("84300")
    world.cap("Salidas", categories=frozenset({"restaurants"}))

    assert world.covering(purchase) == []


def test_a_budget_over_every_category_covers_any_purchase() -> None:
    world = World()
    world.cap("Todo el mes", limit="2000000")

    assert world.covering(world.spend("84300")) == ["Todo el mes"]


def test_a_purchase_no_merchant_owns_yet_counts_only_against_the_whole_month() -> None:
    # Nobody has said which category it is, so a budget naming categories
    # cannot claim it; one over everything does.
    world = World()
    world.cap("Mercado", categories=frozenset({"groceries"}))
    world.cap("Todo el mes", limit="2000000")

    assert world.covering(world.spend("20000", counterparty="ALGO NUEVO")) == [
        "Todo el mes",
    ]


def test_income_counts_against_no_budget() -> None:
    world = World()
    world.cap("Todo el mes")

    salary = world.spend("5000000", direction=MovementDirection.INCOMING)

    assert world.covering(salary) == []


def test_a_transfer_counts_against_no_budget() -> None:
    world = World()
    world.cap("Todo el mes")
    account = _account()
    payment = world.spend("900000", counterparty="PAGO TARJETA", account_id=account)
    payment.declare_transfer()

    assert world.covering(payment) == []


def test_another_persons_movement_is_answered_as_nothing() -> None:
    world = World()
    world.cap("Todo el mes")
    theirs = world.spend("84300", user_id=STRANGER)

    assert world.covering(theirs, user_id=USER) == []


def test_a_movement_that_no_longer_exists_is_answered_as_nothing() -> None:
    world = World()
    world.cap("Todo el mes")
    gone = world.spend("84300")
    del world.ledger.rows[gone.id.value]

    assert world.covering(gone) == []


def test_a_budget_narrowed_to_one_account_covers_only_that_account() -> None:
    world = World()
    card = _account()
    savings = _account()
    world.cap("Tarjeta", accounts=frozenset({card}))

    assert world.covering(world.spend("50000", account_id=card)) == ["Tarjeta"]
    assert world.covering(world.spend("50000", account_id=savings)) == []
    # On no account at all: only a budget watching every account covers it.
    assert world.covering(world.spend("50000")) == []


def test_a_cap_in_another_currency_says_nothing() -> None:
    world = World()
    world.cap("Viaje", currency=Currency.USD, limit="500")

    assert world.covering(world.spend("84300")) == []


def test_a_budget_for_another_month_does_not_cover_this_one() -> None:
    world = World()
    world.cap("Diciembre", month="2026-12")
    world.cap("Septiembre", month="2026-09")

    assert world.covering(world.spend("84300")) == ["Septiembre"]


def test_a_late_evening_purchase_is_read_against_the_month_it_was_spent_in() -> None:
    world = World()
    world.cap("Agosto", month="2026-08")
    world.cap("Septiembre", month="2026-09")

    [august] = world.standing(world.spend("84300", at=AUGUST_LAST_NIGHT))

    assert august.name == "Agosto"
    assert august.month == "2026-08"


# ------------------------------------------------------------------ figures


def test_a_budget_passed_reports_by_how_much_rather_than_zero() -> None:
    world = World()
    world.cap("Mercado", limit="100000", categories=frozenset({"groceries"}))
    purchase = world.spend("130000")

    [mercado] = world.standing(purchase)

    assert mercado.remaining == Decimal("-30000")
    assert mercado.state is BudgetState.OVER


def test_several_budgets_come_back_worst_first() -> None:
    world = World()
    world.cap("Todo el mes", limit="5000000")
    world.cap("Mercado", limit="100000", categories=frozenset({"groceries"}))

    assert world.covering(world.spend("90000")) == ["Mercado", "Todo el mes"]


def test_spending_on_another_category_does_not_count_toward_this_budget() -> None:
    world = World()
    world.cap("Mercado", categories=frozenset({"groceries"}))
    world.spend("300000", counterparty="ANDRES DC")

    [mercado] = world.standing(world.spend("50000"))

    assert mercado.spent == Decimal("50000")
