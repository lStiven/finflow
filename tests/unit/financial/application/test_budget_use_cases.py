"""Which cap governs a month, and which figure it is compared against.

The arithmetic a cap needs is subtraction; what is not trivial is everything
around it, and this file is built around the four ways the join can be wrong:

* the **wrong cap** wins — a December exception ignored, or an exception that
  keeps applying in January;
* the **wrong figure** is compared — the net instead of the outgoing, or a card
  payment counted as spending;
* the **wrong bucket** is read — the movements no merchant owns yet folded in
  as if they were a category somebody chose;
* a cap survives its **category being deleted** and takes the screen with it.

So the summary underneath is the real `SummarizeSpendingUseCase`, not a double.
A budget assembled from fake per-category figures would prove the subtraction
and nothing at all about the figure it subtracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.budgets import (
    MAX_SUGGESTIONS,
    AmendBudgetCommand,
    BudgetNotFoundError,
    BudgetsView,
    DeclareBudgetCommand,
    ManageBudgetsUseCase,
    ReadBudgetsQuery,
    ReadBudgetsUseCase,
)
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
    month_containing,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    MovementDirection,
    TransferId,
    TransferLeg,
    TransferRole,
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
# Midday UTC is the same calendar day in Bogotá, so a fixture's date is the
# date the use case reads.
MIDDAY = dt.time(hour=17)

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
UBER = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000003",
    display_name="Uber",
    category="transport",
    needs_review=False,
)
GATOS = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000004",
    display_name="Veterinaria",
    category="custom:gatos",
    needs_review=False,
)

VOCABULARY = frozenset(
    {"groceries", "restaurants", "transport", "uncategorized", "custom:gatos"},
)


class FakeBudgets:
    """The port, in a dict. Keyed by owner as well as by id, which is the rule
    the real repository exists to keep: a budget id is a uuid somebody could
    paste, and a lookup on the id alone would cross between people.

    It returns every budget unfiltered, exactly as `list_for_user` promises.
    Which of them a month concerns is the domain's answer, and a fake that
    pre-filtered would hide a use case that forgot to ask.
    """

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
    """Answers what Merchant would, by exact counterparty text."""

    def __init__(
        self,
        known: Mapping[str, MerchantAttribution] | None = None,
        *,
        vocabulary: frozenset[str] = VOCABULARY,
    ) -> None:
        self._known = known or {}
        self._vocabulary = vocabulary
        self.vocabulary_reads = 0

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            counterparty: self._known[counterparty]
            for counterparty in counterparties
            if counterparty in self._known
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id
        self.vocabulary_reads += 1

        return self._vocabulary

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
    """Enough of the ledger for a summary. Everything a budget read never
    touches raises rather than pretending: a read that ever wrote something
    fails loudly here instead of passing quietly."""

    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def keep(self, movement: Transaction) -> Transaction:
        self.rows[movement.id.value] = movement

        return movement

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]

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

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        raise NotImplementedError

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        raise NotImplementedError

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        raise NotImplementedError


class FakeAccounts:
    def __init__(self, accounts: list[Account] | None = None) -> None:
        self.accounts = accounts or []

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return next(
            (
                account
                for account in self.accounts
                if account.user_id == user_id and account.id == account_id
            ),
            None,
        )

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return [account for account in self.accounts if account.user_id == user_id]

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


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def this_month() -> str:
    return month_containing(dt.datetime.now(tz=dt.UTC).date())


def day_of_this_month(day: int) -> dt.date:
    """A day of the month the test is running in, never past its end."""
    now = dt.datetime.now(tz=dt.UTC).date()
    first = now.replace(day=1)
    following = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    last = (following - dt.timedelta(days=1)).day

    return now.replace(day=min(day, last))


def spend(
    ledger: InMemoryLedger,
    amount: str,
    *,
    counterparty: str = "ARA",
    on: dt.date | None = None,
    currency: Currency = Currency.COP,
    direction: MovementDirection = MovementDirection.OUTGOING,
    transfer: TransferLeg | None = None,
    user_id: UserId = USER,
) -> Transaction:
    movement = Transaction.enter_manually(
        user_id=user_id,
        direction=direction,
        amount=money(amount, currency),
        occurred_at=PosixTime.from_datetime(
            dt.datetime.combine(on or day_of_this_month(5), MIDDAY, tzinfo=dt.UTC),
        ),
        counterparty=counterparty,
    )
    movement.transfer = transfer

    return ledger.keep(movement)


def cap(
    budgets: FakeBudgets,
    *,
    name: str | None = None,
    categories: frozenset[str] | None = frozenset({"groceries"}),
    accounts: frozenset[AccountId] | None = None,
    limit: str = "600000",
    month: str | None = None,
    warn_at: int = 80,
    currency: Currency = Currency.COP,
    user_id: UserId = USER,
) -> Budget:
    """A declared budget. `categories=None` is the one over everything."""
    budget = Budget.declare(
        user_id=user_id,
        name=name or ", ".join(sorted(categories or ())) or "Todo el mes",
        limit=money(limit, currency),
        scope=BudgetScope.of(categories=categories, accounts=accounts),
        month=month,
        warn_at=warn_at,
    )
    budgets.save(budget)

    return budget


def read(
    *,
    budgets: FakeBudgets | None = None,
    ledger: InMemoryLedger | None = None,
    directory: FakeDirectory | None = None,
    month: str | None = None,
) -> BudgetsView:
    ledger = ledger or InMemoryLedger()
    directory = directory or FakeDirectory()
    use_case = ReadBudgetsUseCase(
        budgets=budgets or FakeBudgets(),
        spending=SummarizeSpendingUseCase(
            ledger=ledger,
            accounts=FakeAccounts(),
            merchants=directory,
        ),
        merchants=directory,
    )

    return use_case.execute(
        ReadBudgetsQuery(user_id=USER, timezone=TIMEZONE, month=month),
    )


def known() -> FakeDirectory:
    return FakeDirectory(
        {"ARA": ARA, "ANDRES": ANDRES, "UBER": UBER, "VET": GATOS},
    )


class TestDeclaring:
    def test_every_budget_gets_its_own_row(self) -> None:
        """The reversal. Declaring the same scope twice used to leave one cap;
        it leaves two budgets now, because that is what somebody asking for it
        twice meant."""
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)

        first = use_case.declare(
            DeclareBudgetCommand(
                user_id=USER,
                name="Restaurantes",
                limit=money("600000"),
                scope=BudgetScope.of(categories=frozenset({"restaurants"})),
            ),
        )
        second = use_case.declare(
            DeclareBudgetCommand(
                user_id=USER,
                name="Restaurantes otra vez",
                limit=money("900000"),
                scope=BudgetScope.of(categories=frozenset({"restaurants"})),
            ),
        )

        assert first.id != second.id
        assert len(budgets.rows) == 2

    def test_a_budget_over_everything_needs_no_category(self) -> None:
        """«No quiero gastar más de X este mes» — the budget somebody declares
        before they have looked at a single category."""
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)

        budget = use_case.declare(
            DeclareBudgetCommand(
                user_id=USER,
                name="Todo el mes",
                limit=money("3000000"),
                scope=BudgetScope.everything(),
            ),
        )

        assert budget.scope.total is True
        assert budgets.rows[(USER, budget.id)].name == "Todo el mes"

    def test_what_was_declared_is_what_is_stored(self) -> None:
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)

        budget = use_case.declare(
            DeclareBudgetCommand(
                user_id=USER,
                name="Salidas",
                limit=money("600000"),
                scope=BudgetScope.of(categories=frozenset({"restaurants"})),
                icon="beer",
                month="2026-12",
                warn_at=70,
            ),
        )

        stored = budgets.rows[(USER, budget.id)]
        assert stored.warn_at == 70
        assert stored.month == "2026-12"
        assert stored.icon == "beer"


class TestAmending:
    def test_amending_keeps_the_row_rather_than_adding_one(self) -> None:
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)
        existing = cap(budgets, name="Mercado", limit="600000")

        amended = use_case.amend(
            AmendBudgetCommand(
                user_id=USER,
                budget_id=existing.id,
                name="Mercado y aseo",
                limit=money("900000"),
                scope=BudgetScope.of(categories=frozenset({"groceries"})),
            ),
        )

        assert amended.id == existing.id
        assert len(budgets.rows) == 1
        assert budgets.rows[(USER, existing.id)].name == "Mercado y aseo"

    def test_amending_something_that_is_not_there_is_refused(self) -> None:
        use_case = ManageBudgetsUseCase(budgets=FakeBudgets())

        with pytest.raises(BudgetNotFoundError):
            use_case.amend(
                AmendBudgetCommand(
                    user_id=USER,
                    budget_id=BudgetId.new(),
                    name="Mercado",
                    limit=money("600000"),
                    scope=BudgetScope.everything(),
                ),
            )

    def test_one_persons_budget_is_not_anothers_to_amend(self) -> None:
        """Read scoped to the owner, never by id alone: a uuid is something
        somebody could paste."""
        budgets = FakeBudgets()
        theirs = cap(budgets, user_id=STRANGER)

        with pytest.raises(BudgetNotFoundError):
            ManageBudgetsUseCase(budgets=budgets).amend(
                AmendBudgetCommand(
                    user_id=USER,
                    budget_id=theirs.id,
                    name="Mío ahora",
                    limit=money("1"),
                    scope=BudgetScope.everything(),
                ),
            )

        assert budgets.rows[(STRANGER, theirs.id)].name == theirs.name


class TestForgetting:
    def test_dropping_a_budget_says_there_was_one(self) -> None:
        budgets = FakeBudgets()
        existing = cap(budgets)

        dropped = ManageBudgetsUseCase(budgets=budgets).forget(
            user_id=USER,
            budget_id=existing.id,
        )

        assert dropped is True
        assert budgets.rows == {}

    def test_dropping_nothing_says_so_rather_than_raising(self) -> None:
        use_case = ManageBudgetsUseCase(budgets=FakeBudgets())

        assert use_case.forget(user_id=USER, budget_id=BudgetId.new()) is False

    def test_dropping_one_leaves_the_others(self) -> None:
        """December's exception and the usual ceiling are two budgets now, so
        dropping one cannot touch the other."""
        budgets = FakeBudgets()
        usual = cap(budgets, name="Restaurantes", limit="600000")
        december = cap(budgets, name="Diciembre", limit="900000", month="2026-12")

        ManageBudgetsUseCase(budgets=budgets).forget(
            user_id=USER,
            budget_id=december.id,
        )

        assert list(budgets.rows) == [(USER, usual.id)]

    def test_one_persons_budget_is_not_anothers_to_drop(self) -> None:
        budgets = FakeBudgets()
        theirs = cap(budgets, user_id=STRANGER)

        dropped = ManageBudgetsUseCase(budgets=budgets).forget(
            user_id=USER,
            budget_id=theirs.id,
        )

        assert dropped is False
        assert (STRANGER, theirs.id) in budgets.rows


class TestWhichBudgetsApply:
    def test_a_recurring_budget_governs_any_month(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, limit="600000")

        view = read(budgets=budgets, month="2026-03")

        assert [line.progress.limit for line in view.budgets] == [Decimal("600000")]

    def test_a_months_own_budget_is_read_beside_the_recurring_one(self) -> None:
        """No shadowing. The rule died with the identity it depended on: with
        scopes that overlap on purpose there is no honest answer to which of
        two budgets hides which, so both apply and both are shown."""
        budgets = FakeBudgets()
        cap(budgets, name="Restaurantes", limit="600000")
        cap(budgets, name="Diciembre", limit="900000", month="2026-12")

        view = read(budgets=budgets, month="2026-12")

        assert sorted(line.progress.limit for line in view.budgets) == [
            Decimal("600000"),
            Decimal("900000"),
        ]

    def test_a_months_own_budget_does_not_leak_into_another_month(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, name="Diciembre", limit="900000", month="2026-12")

        view = read(budgets=budgets, month="2027-01")

        assert view.budgets == []


class TestWhatIsCompared:
    def test_spending_in_the_scope_counts_against_the_budget(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}), limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("100000")

    def test_spending_outside_the_scope_does_not(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}))
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ANDRES")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_scope_over_several_categories_adds_them_up(self) -> None:
        """«Salidas» is restaurants and bars and delivery — one ceiling over
        three of somebody's categories, which is the whole point of a scope."""
        budgets = FakeBudgets()
        cap(
            budgets,
            name="Salidas",
            categories=frozenset({"restaurants", "transport"}),
            limit="600000",
        )
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ANDRES")
        spend(ledger, "50000", counterparty="UBER")
        spend(ledger, "700000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("150000")

    def test_a_budget_over_everything_counts_every_category(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=None, limit="3000000")
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ANDRES")
        spend(ledger, "50000", counterparty="UBER")
        spend(ledger, "700000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("850000")

    def test_a_budget_over_everything_counts_the_unknown_bucket(self) -> None:
        """Money no merchant owns yet is still money that left. A budget
        naming categories cannot count it — nobody said which one it is — but
        one over everything has to, or «todo el mes» is not all of it."""
        budgets = FakeBudgets()
        cap(budgets, categories=None, limit="3000000")
        ledger = InMemoryLedger()
        spend(ledger, "80000", counterparty="NOBODY KNOWS THIS ONE")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("80000")

    def test_a_scoped_budget_never_counts_the_unknown_bucket(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}))
        ledger = InMemoryLedger()
        spend(ledger, "80000", counterparty="NOBODY KNOWS THIS ONE")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_transfer_never_eats_a_budget(self) -> None:
        """Paying a card from savings leaves its owner exactly as rich as
        before, and a ceiling eaten by it would be eaten by nothing."""
        budgets = FakeBudgets()
        cap(budgets, categories=None, limit="600000")
        ledger = InMemoryLedger()
        spend(
            ledger,
            "300000",
            counterparty="ARA",
            transfer=TransferLeg(
                transfer_id=TransferId(value="transfer-1"),
                role=TransferRole.SOURCE,
            ),
        )

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_refund_does_not_make_the_budget_look_under(self) -> None:
        """The outgoing figure, never the net: the money did leave and came
        back, which is two facts and not none."""
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}), limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ARA")
        spend(
            ledger,
            "200000",
            counterparty="ARA",
            direction=MovementDirection.INCOMING,
        )

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("500000")

    def test_spending_in_another_month_does_not_count(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}))
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA", on=dt.date(2020, 3, 4))

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_budget_is_compared_against_its_own_currency(self) -> None:
        budgets = FakeBudgets()
        cap(
            budgets,
            categories=frozenset({"groceries"}),
            limit="500",
            currency=Currency.USD,
        )
        ledger = InMemoryLedger()
        spend(ledger, "400000", counterparty="ARA")
        spend(ledger, "100", counterparty="ARA", currency=Currency.USD)

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("100")

    def test_a_users_own_category_is_watched_like_any_other(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"custom:gatos"}), limit="200000")
        ledger = InMemoryLedger()
        spend(ledger, "90000", counterparty="VET")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("90000")

    def test_another_persons_spending_never_reaches_a_budget(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}))
        ledger = InMemoryLedger()
        spend(ledger, "400000", counterparty="ARA", user_id=STRANGER)

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)


class TestTheMonthRead:
    def test_with_no_month_asked_for_it_reads_the_one_today_is_in(self) -> None:
        assert read().month == this_month()

    def test_the_window_is_the_whole_calendar_month(self) -> None:
        view = read(month="2026-02")

        assert (view.since, view.until) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))


class TestTotals:
    def test_the_budgets_of_one_currency_are_added_up_and_tallied(self) -> None:
        budgets = FakeBudgets()
        cap(
            budgets, name="Mercado", categories=frozenset({"groceries"}), limit="600000"
        )
        cap(
            budgets,
            name="Restaurantes",
            categories=frozenset({"restaurants"}),
            limit="400000",
        )
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert len(view.totals) == 1
        assert view.totals[0].limit == Decimal("1000000")
        assert view.totals[0].spent == Decimal("500000")
        assert (view.totals[0].ok, view.totals[0].warning, view.totals[0].over) == (
            1,
            1,
            0,
        )

    def test_two_currencies_are_never_summed(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, name="Pesos", categories=frozenset({"groceries"}), limit="600000")
        cap(
            budgets,
            name="Dólares",
            categories=frozenset({"restaurants"}),
            limit="500",
            currency=Currency.USD,
        )

        view = read(budgets=budgets)

        assert {total.currency for total in view.totals} == {
            Currency.COP,
            Currency.USD,
        }

    def test_going_over_in_total_is_reported_negative(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}), limit="100000")
        ledger = InMemoryLedger()
        spend(ledger, "150000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.totals[0].remaining == Decimal("-50000")

    def test_no_budgets_means_no_totals_rather_than_a_zero_row(self) -> None:
        assert read().totals == []

    def test_a_retired_budget_is_not_counted_among_the_living(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}), limit="600000")
        cap(budgets, categories=frozenset({"gone"}), limit="400000")

        view = read(budgets=budgets, directory=known())

        assert view.totals[0].limit == Decimal("600000")
        assert view.totals[0].ok == 1

    def test_a_retired_budget_is_still_listed_so_it_can_be_dropped(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"gone"}))

        view = read(budgets=budgets, directory=known())

        assert [line.retired for line in view.budgets] == [True]


class TestOrder:
    def test_the_closest_to_its_ceiling_leads(self) -> None:
        budgets = FakeBudgets()
        cap(
            budgets, name="Mercado", categories=frozenset({"groceries"}), limit="600000"
        )
        cap(
            budgets,
            name="Restaurantes",
            categories=frozenset({"restaurants"}),
            limit="200000",
        )
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA")
        spend(ledger, "180000", counterparty="ANDRES")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert [line.progress.name for line in view.budgets] == [
            "Restaurantes",
            "Mercado",
        ]

    def test_a_tie_falls_back_to_the_name_so_the_order_is_stable(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, name="Zeta", categories=frozenset({"groceries"}), limit="600000")
        cap(budgets, name="Alfa", categories=frozenset({"restaurants"}), limit="600000")

        view = read(budgets=budgets, directory=known())

        assert [line.progress.name for line in view.budgets] == ["Alfa", "Zeta"]


class TestARetiredCategory:
    def test_a_budget_whose_categories_all_vanished_is_marked(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"gone"}))

        view = read(budgets=budgets, directory=known())

        assert view.budgets[0].retired is True
        assert view.budgets[0].missing == frozenset({"gone"})

    def test_losing_one_of_several_categories_marks_it_not_the_budget(self) -> None:
        """The partial case. «Salidas» lost one of its three: the ceiling is
        still a live decision about the two that remain, so the budget is not
        retired and the screen marks the category instead."""
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"restaurants", "gone"}))

        view = read(budgets=budgets, directory=known())

        assert view.budgets[0].retired is False
        assert view.budgets[0].missing == frozenset({"gone"})

    def test_a_budget_over_everything_can_never_retire(self) -> None:
        """There is no named category for its owner to delete out from under
        it."""
        budgets = FakeBudgets()
        cap(budgets, categories=None)

        view = read(budgets=budgets, directory=known())

        assert view.budgets[0].retired is False

    def test_a_budget_on_live_categories_is_not_marked(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"groceries"}))

        view = read(budgets=budgets, directory=known())

        assert view.budgets[0].retired is False
        assert view.budgets[0].missing == frozenset()

    def test_the_vocabulary_is_read_once_and_not_once_per_budget(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, name="A", categories=frozenset({"groceries"}))
        cap(budgets, name="B", categories=frozenset({"restaurants"}))
        cap(budgets, name="C", categories=frozenset({"transport"}))
        directory = known()

        read(budgets=budgets, directory=directory)

        assert directory.vocabulary_reads == 1


class TestSuggestions:
    def test_where_money_goes_and_no_budget_is_watching_is_offered(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "300000", counterparty="ANDRES")

        view = read(ledger=ledger, directory=known())

        assert [offer.category for offer in view.suggestions] == ["restaurants"]

    def test_a_category_a_budget_already_names_is_not_offered(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, categories=frozenset({"restaurants"}))
        ledger = InMemoryLedger()
        spend(ledger, "300000", counterparty="ANDRES")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.suggestions == []

    def test_a_budget_over_everything_suppresses_nothing(self) -> None:
        """It caps no category *by name*: somebody with a ceiling on the whole
        month may still want one on restaurants, and that is the second budget
        they will declare."""
        budgets = FakeBudgets()
        cap(budgets, categories=None, limit="3000000")
        ledger = InMemoryLedger()
        spend(ledger, "300000", counterparty="ANDRES")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert [offer.category for offer in view.suggestions] == ["restaurants"]

    def test_the_biggest_comes_first(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ANDRES")
        spend(ledger, "300000", counterparty="ARA")

        view = read(ledger=ledger, directory=known())

        assert [offer.category for offer in view.suggestions] == [
            "groceries",
            "restaurants",
        ]

    def test_a_category_nobody_spent_in_is_not_offered(self) -> None:
        view = read(directory=known())

        assert view.suggestions == []

    def test_a_deleted_category_is_not_offered(self) -> None:
        """Offering to cap a category that no longer exists is offering a form
        that cannot be submitted."""
        ledger = InMemoryLedger()
        spend(ledger, "300000", counterparty="VET")
        directory = FakeDirectory(
            {"VET": GATOS},
            vocabulary=frozenset({"groceries", "uncategorized"}),
        )

        view = read(ledger=ledger, directory=directory)

        assert view.suggestions == []

    def test_the_unknown_bucket_is_not_offered(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "300000", counterparty="NOBODY KNOWS THIS ONE")

        view = read(ledger=ledger, directory=known())

        assert view.suggestions == []

    def test_it_is_a_handful_and_not_a_second_breakdown(self) -> None:
        ledger = InMemoryLedger()
        directory = FakeDirectory(
            {
                f"M{index}": MerchantAttribution(
                    merchant_id=f"aaaaaaaa-0000-0000-0000-00000000{index:04d}",
                    display_name=f"M{index}",
                    category=f"category-{index}",
                    needs_review=False,
                )
                for index in range(8)
            },
            vocabulary=frozenset(f"category-{index}" for index in range(8)),
        )

        for index in range(8):
            spend(ledger, str(100000 + index), counterparty=f"M{index}")

        view = read(ledger=ledger, directory=directory)

        assert len(view.suggestions) == MAX_SUGGESTIONS
