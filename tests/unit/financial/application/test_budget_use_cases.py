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

from personal_finance.contexts.financial.application.budgets import (
    BudgetsView,
    ManageBudgetsUseCase,
    ReadBudgetsQuery,
    ReadBudgetsUseCase,
    SetBudgetCommand,
)
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.budgets import (
    BudgetId,
    BudgetState,
    CategoryBudget,
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
    """The port, in a dict. Keyed by owner as well as by the cap's identity,
    which is the rule the real repository exists to keep."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, BudgetId], CategoryBudget] = {}

    def list_for_month(
        self,
        *,
        user_id: UserId,
        month: str,
    ) -> Sequence[CategoryBudget]:
        return [
            budget
            for (owner, key), budget in self.rows.items()
            if owner == user_id and key.month in (None, month)
        ]

    def save(self, budget: CategoryBudget) -> None:
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
    category: str = "groceries",
    limit: str = "600000",
    month: str | None = None,
    warn_at: int = 80,
    currency: Currency = Currency.COP,
    user_id: UserId = USER,
) -> CategoryBudget:
    budget = CategoryBudget.declare(
        user_id=user_id,
        category=category,
        limit=money(limit, currency),
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
    def test_a_cap_is_stored_under_its_category_and_month(self) -> None:
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)

        budget = use_case.declare(
            SetBudgetCommand(
                user_id=USER,
                category="restaurants",
                limit=money("600000"),
                month="2026-12",
                warn_at=70,
            ),
        )

        assert budget.id == BudgetId(category="restaurants", month="2026-12")
        assert budgets.rows[(USER, budget.id)].warn_at == 70

    def test_declaring_twice_leaves_one_cap(self) -> None:
        """Two caps on the same category for the same month are one cap
        declared twice — there is no second row to disagree with."""
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)
        command = SetBudgetCommand(
            user_id=USER,
            category="groceries",
            limit=money("600000"),
        )

        use_case.declare(command)
        use_case.declare(
            SetBudgetCommand(
                user_id=USER,
                category="groceries",
                limit=money("900000"),
            ),
        )

        assert len(budgets.rows) == 1
        assert next(iter(budgets.rows.values())).limit.amount == Decimal("900000")

    def test_a_recurring_cap_and_a_months_exception_are_two_rows(self) -> None:
        budgets = FakeBudgets()
        use_case = ManageBudgetsUseCase(budgets=budgets)

        use_case.declare(
            SetBudgetCommand(
                user_id=USER,
                category="groceries",
                limit=money("600000"),
            ),
        )
        use_case.declare(
            SetBudgetCommand(
                user_id=USER,
                category="groceries",
                limit=money("900000"),
                month="2026-12",
            ),
        )

        assert len(budgets.rows) == 2


class TestForgetting:
    def test_dropping_a_cap_says_there_was_one(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries")
        use_case = ManageBudgetsUseCase(budgets=budgets)

        assert use_case.forget(user_id=USER, category="groceries") is True
        assert budgets.rows == {}

    def test_dropping_nothing_says_so_rather_than_raising(self) -> None:
        use_case = ManageBudgetsUseCase(budgets=FakeBudgets())

        assert use_case.forget(user_id=USER, category="groceries") is False

    def test_dropping_this_months_exception_leaves_the_recurring_cap(self) -> None:
        """The point of the two being separate rows: December can stop being
        special without anybody having to re-declare the usual ceiling."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="groceries", limit="900000", month="2026-12")
        use_case = ManageBudgetsUseCase(budgets=budgets)

        assert (
            use_case.forget(user_id=USER, category="groceries", month="2026-12") is True
        )
        assert [budget.limit.amount for budget in budgets.rows.values()] == [
            Decimal("600000"),
        ]

    def test_one_persons_cap_is_not_anothers_to_drop(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", user_id=STRANGER)
        use_case = ManageBudgetsUseCase(budgets=budgets)

        assert use_case.forget(user_id=USER, category="groceries") is False
        assert len(budgets.rows) == 1


class TestWhichCapWins:
    def test_a_recurring_cap_governs_a_month_with_no_exception(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")

        view = read(budgets=budgets, month="2026-09")

        assert [line.progress.limit for line in view.budgets] == [Decimal("600000")]
        assert view.budgets[0].progress.recurring is True

    def test_this_months_exception_shadows_the_recurring_cap(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="groceries", limit="900000", month="2026-12")

        view = read(budgets=budgets, month="2026-12")

        assert [line.progress.limit for line in view.budgets] == [Decimal("900000")]
        assert view.budgets[0].progress.recurring is False
        assert view.budgets[0].progress.month == "2026-12"

    def test_the_exception_does_not_leak_into_another_month(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="groceries", limit="900000", month="2026-12")

        view = read(budgets=budgets, month="2026-11")

        assert [line.progress.limit for line in view.budgets] == [Decimal("600000")]

    def test_a_category_capped_only_for_one_month_appears_only_there(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="travel", limit="2000000", month="2026-12")

        assert [
            line.progress.category
            for line in read(
                budgets=budgets,
                month="2026-12",
            ).budgets
        ] == ["travel"]
        assert read(budgets=budgets, month="2026-11").budgets == []

    def test_the_winner_does_not_depend_on_the_order_rows_come_back(self) -> None:
        """Written as two passes for exactly this reason: storage order must
        not decide which of two caps a month reads."""
        forwards = FakeBudgets()
        cap(forwards, category="groceries", limit="600000")
        cap(forwards, category="groceries", limit="900000", month="2026-12")

        backwards = FakeBudgets()
        cap(backwards, category="groceries", limit="900000", month="2026-12")
        cap(backwards, category="groceries", limit="600000")

        assert (
            read(budgets=forwards, month="2026-12").budgets[0].progress.limit
            == read(budgets=backwards, month="2026-12").budgets[0].progress.limit
            == Decimal("900000")
        )


class TestWhatIsCompared:
    def test_spending_in_the_category_counts_against_the_cap(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "150000", counterparty="ARA")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("150000")
        assert view.budgets[0].progress.remaining == Decimal("450000")
        assert view.budgets[0].progress.state is BudgetState.OK

    def test_spending_in_another_category_does_not(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="UBER")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_transfer_never_eats_a_cap(self) -> None:
        """Paying a card from savings left its owner exactly as rich as before.
        Counting it would burn a budget for a movement that is not spending."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(
            ledger,
            "500000",
            counterparty="ARA",
            transfer=TransferLeg(
                transfer_id=TransferId(value="transfer-1"),
                role=TransferRole.SOURCE,
            ),
        )

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_refund_does_not_make_the_cap_look_under(self) -> None:
        """`outgoing` and never `net`: the money left and came back, which is
        two facts and not none."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ARA")
        spend(
            ledger,
            "500000",
            counterparty="ARA",
            direction=MovementDirection.INCOMING,
        )

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("500000")

    def test_spending_in_another_month_does_not_count(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ARA", on=dt.date(2020, 3, 14))

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_a_cap_is_compared_against_its_own_currency(self) -> None:
        """Nothing here converts. A cap in dollars must not be eaten by pesos,
        and one read has to answer both."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="400", currency=Currency.USD)
        cap(budgets, category="transport", limit="300000")
        ledger = InMemoryLedger()
        spend(ledger, "120", counterparty="ARA", currency=Currency.USD)
        spend(ledger, "900000", counterparty="ARA")
        spend(ledger, "90000", counterparty="UBER")

        view = read(budgets=budgets, ledger=ledger, directory=known())
        by_category = {line.progress.category: line.progress for line in view.budgets}

        assert by_category["groceries"].spent == Decimal("120")
        assert by_category["groceries"].currency is Currency.USD
        assert by_category["transport"].spent == Decimal("90000")

    def test_a_users_own_category_is_capped_like_any_other(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="custom:gatos", limit="200000")
        ledger = InMemoryLedger()
        spend(ledger, "180000", counterparty="VET")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal("180000")
        assert view.budgets[0].progress.state is BudgetState.WARNING

    def test_movements_no_merchant_owns_yet_are_not_a_category(self) -> None:
        """`/summary` buckets them under a key of None — unknown, which is not
        the `uncategorized` category. Folding one into the other would file
        somebody's spending under a category they never chose."""
        budgets = FakeBudgets()
        cap(budgets, category="uncategorized", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="SOMETHING NOBODY OWNS")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)

    def test_another_persons_spending_never_reaches_a_cap(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ARA", user_id=STRANGER)

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert view.budgets[0].progress.spent == Decimal(0)


class TestTheMonthRead:
    def test_with_no_month_asked_for_it_reads_the_one_today_is_in(self) -> None:
        view = read()

        assert view.month == this_month()

    def test_the_window_is_the_whole_calendar_month(self) -> None:
        view = read(month="2026-02")

        assert view.since == dt.date(2026, 2, 1)
        assert view.until == dt.date(2026, 2, 28)


class TestTotals:
    def test_the_caps_of_one_currency_are_added_up_and_tallied(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="restaurants", limit="400000")
        cap(budgets, category="transport", limit="200000")
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA")
        spend(ledger, "350000", counterparty="ANDRES")
        spend(ledger, "260000", counterparty="UBER")

        totals = read(budgets=budgets, ledger=ledger, directory=known()).totals

        assert len(totals) == 1
        assert totals[0].limit == Decimal("1200000")
        assert totals[0].spent == Decimal("710000")
        assert totals[0].remaining == Decimal("490000")
        assert (totals[0].ok, totals[0].warning, totals[0].over) == (1, 1, 1)

    def test_two_currencies_are_never_summed(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="transport", limit="400", currency=Currency.USD)

        totals = read(budgets=budgets).totals

        assert {total.currency for total in totals} == {Currency.COP, Currency.USD}
        assert {total.limit for total in totals} == {
            Decimal("600000"),
            Decimal("400"),
        }

    def test_going_over_in_total_is_reported_negative(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="100000")
        ledger = InMemoryLedger()
        spend(ledger, "160000", counterparty="ARA")

        assert read(budgets=budgets, ledger=ledger, directory=known()).totals[
            0
        ].remaining == Decimal("-60000")

    def test_no_caps_means_no_totals_rather_than_a_zero_row(self) -> None:
        assert read().totals == []

    def test_a_retired_cap_is_not_counted_among_the_living(self) -> None:
        """Nothing will ever be spent against it, so tallying it as one more in
        the green is a tally about a decision that stopped being one — and its
        ceiling would inflate what the month is allowed."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="custom:gone", limit="400000")

        totals = read(budgets=budgets, directory=known()).totals

        assert totals[0].limit == Decimal("600000")
        assert (totals[0].ok, totals[0].warning, totals[0].over) == (1, 0, 0)

    def test_a_retired_cap_is_still_listed_so_it_can_be_dropped(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="custom:gone", limit="400000")

        view = read(budgets=budgets, directory=known())

        assert [line.progress.category for line in view.budgets] == ["custom:gone"]
        assert view.totals == []

    def test_the_currency_of_the_worst_cap_leads(self) -> None:
        """The totals are handed the ordered lines, so insertion order means
        what it says rather than repeating whatever storage answered."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="600000")
        cap(budgets, category="transport", limit="400", currency=Currency.USD)
        ledger = InMemoryLedger()
        spend(ledger, "390", counterparty="UBER", currency=Currency.USD)

        totals = read(budgets=budgets, ledger=ledger, directory=known()).totals

        assert [total.currency for total in totals] == [Currency.USD, Currency.COP]


class TestOrder:
    def test_the_closest_to_its_ceiling_leads(self) -> None:
        """The only one with something to do about it."""
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="1000000")
        cap(budgets, category="restaurants", limit="400000")
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA")
        spend(ledger, "380000", counterparty="ANDRES")

        view = read(budgets=budgets, ledger=ledger, directory=known())

        assert [line.progress.category for line in view.budgets] == [
            "restaurants",
            "groceries",
        ]

    def test_a_tie_falls_back_to_the_category_so_the_order_is_stable(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="transport", limit="100000")
        cap(budgets, category="groceries", limit="100000")

        view = read(budgets=budgets)

        assert [line.progress.category for line in view.budgets] == [
            "groceries",
            "transport",
        ]


class TestARetiredCategory:
    def test_a_cap_on_a_deleted_category_is_marked_rather_than_dropped(self) -> None:
        """Merchant publishes nothing Financial could listen for, so this is
        the only place it can be handled — and the cap is still the record of
        a decision somebody made."""
        budgets = FakeBudgets()
        cap(budgets, category="custom:gone", limit="200000")

        view = read(budgets=budgets, directory=known())

        assert view.budgets[0].retired is True
        assert view.budgets[0].progress.category == "custom:gone"

    def test_a_cap_on_a_live_category_is_not_marked(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="groceries", limit="200000")

        assert read(budgets=budgets, directory=known()).budgets[0].retired is False

    def test_the_vocabulary_is_read_once_and_not_once_per_cap(self) -> None:
        """A real read of this person's category rows through the Merchant
        adapter. Once per cap would multiply it by the screen."""
        budgets = FakeBudgets()
        for category in ("groceries", "restaurants", "transport"):
            cap(budgets, category=category, limit="200000")
        directory = known()

        read(budgets=budgets, directory=directory)

        assert directory.vocabulary_reads == 1


class TestSuggestions:
    def test_where_money_goes_and_no_cap_is_watching_is_offered(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ANDRES")

        view = read(ledger=ledger, directory=known())

        assert [(offer.category, offer.spent) for offer in view.suggestions] == [
            ("restaurants", Decimal("500000")),
        ]

    def test_a_category_that_already_has_a_cap_is_not_offered(self) -> None:
        budgets = FakeBudgets()
        cap(budgets, category="restaurants", limit="600000")
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="ANDRES")

        assert read(budgets=budgets, ledger=ledger, directory=known()).suggestions == []

    def test_the_biggest_comes_first(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "100000", counterparty="ARA")
        spend(ledger, "500000", counterparty="ANDRES")
        spend(ledger, "300000", counterparty="UBER")

        view = read(ledger=ledger, directory=known())

        assert [offer.category for offer in view.suggestions] == [
            "restaurants",
            "transport",
            "groceries",
        ]

    def test_a_category_nobody_spent_in_is_not_offered(self) -> None:
        """Money that came *in* is not somewhere a ceiling belongs."""
        ledger = InMemoryLedger()
        spend(
            ledger,
            "500000",
            counterparty="ARA",
            direction=MovementDirection.INCOMING,
        )

        assert read(ledger=ledger, directory=known()).suggestions == []

    def test_a_deleted_category_is_not_offered(self) -> None:
        """Offering to cap a category that no longer exists is offering a form
        that cannot be submitted."""
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="VET")

        view = read(
            ledger=ledger,
            directory=FakeDirectory(
                {"VET": GATOS},
                vocabulary=frozenset({"groceries"}),
            ),
        )

        assert view.suggestions == []

    def test_the_default_bucket_is_not_offered(self) -> None:
        """`uncategorized` is a real value the API would store, but it is the
        absence of a decision rather than a kind of spending — and it is what
        the form's own picker leaves out, so offering it would open a form with
        no category selected that would nonetheless submit."""
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="PLAIN")

        view = read(
            ledger=ledger,
            directory=FakeDirectory(
                {
                    "PLAIN": MerchantAttribution(
                        merchant_id="aaaaaaaa-0000-0000-0000-00000000000f",
                        display_name="Algo",
                        category="uncategorized",
                        needs_review=False,
                    ),
                },
            ),
        )

        assert view.suggestions == []

    def test_the_unknown_bucket_is_not_offered(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "500000", counterparty="SOMETHING NOBODY OWNS")

        assert read(ledger=ledger, directory=known()).suggestions == []

    def test_it_is_a_handful_and_not_a_second_breakdown(self) -> None:
        ledger = InMemoryLedger()
        directory = FakeDirectory(
            {
                f"M{index}": MerchantAttribution(
                    merchant_id=f"aaaaaaaa-0000-0000-0000-00000000000{index}",
                    display_name=f"M{index}",
                    category=f"cat{index}",
                    needs_review=False,
                )
                for index in range(8)
            },
            vocabulary=frozenset(f"cat{index}" for index in range(8)),
        )
        for index in range(8):
            spend(ledger, f"{index + 1}00000", counterparty=f"M{index}")

        assert len(read(ledger=ledger, directory=directory).suggestions) == 5
