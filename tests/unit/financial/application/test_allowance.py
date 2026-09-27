"""The most dangerous number in the app, and the ways it could lie.

The arithmetic is trivial; what is not trivial is *which* figures go into it.
This file is built around the one mistake that is invisible in the payload and
wrong by exactly the charges already paid: **subtracting what the month costs
instead of what is still owed**, which discounts a confirmed charge twice —
once as a commitment and again through the ledger row that confirming it
wrote.

So the use cases underneath are the real ones, not doubles. An allowance
assembled from fake summaries would prove the subtraction and nothing about
the two answers it subtracts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.allowance import (
    DeclarePlanCommand,
    ManageMonthlyPlanUseCase,
    MonthlyAllowance,
    ReadAllowanceQuery,
    ReadMonthlyAllowanceUseCase,
)
from personal_finance.contexts.financial.application.bills import ListBillsUseCase
from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.plan import MonthlyPlan
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
TIMEZONE = "America/Bogota"
# Midday UTC is the same calendar day in Bogotá, so a fixture's date is the
# date the use case reads.
MIDDAY = dt.time(hour=17)


class FakePlans:
    def __init__(self) -> None:
        self.rows: dict[UserId, MonthlyPlan] = {}

    def find(self, *, user_id: UserId) -> MonthlyPlan | None:
        return self.rows.get(user_id)

    def save(self, plan: MonthlyPlan) -> None:
        self.rows[plan.user_id] = plan

    def remove(self, *, user_id: UserId) -> bool:
        return self.rows.pop(user_id, None) is not None


class FakeBills:
    def __init__(self) -> None:
        self.rows: dict[BillId, ScheduledBill] = {}

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        bill = self.rows.get(bill_id)

        return bill if bill is not None and bill.user_id == user_id else None

    def list_by_user(self, user_id: UserId) -> list[ScheduledBill]:
        return [bill for bill in self.rows.values() if bill.user_id == user_id]

    def save(self, bill: ScheduledBill) -> None:
        self.rows[bill.id] = bill

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        del user_id

        return self.rows.pop(bill_id, None) is not None


class InMemoryLedger:
    """Enough of the ledger for a summary and for «is this charge paid»."""

    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def keep(self, movement: Transaction) -> Transaction:
        self.rows[movement.id.value] = movement

        return movement

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        del balance_delta
        self.rows[transaction.id.value] = transaction

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = transaction

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        del reversals

        for transaction in transactions:
            self.rows.pop(transaction.id.value, None)

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row if row is not None and row.user_id == user_id else None

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        return {
            movement_id: self.rows[movement_id]
            for movement_id in movement_ids
            if movement_id in self.rows and self.rows[movement_id].user_id == user_id
        }

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
        return [
            row
            for row in self.list_unassigned(user_id)
            if row.account_fingerprint == fingerprint
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]


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

    # The rest of the port, which neither of the two use cases under test
    # reads. They raise rather than pretend: a summary or a bills listing that
    # ever wrote something would fail here loudly instead of passing quietly.

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


def today() -> dt.date:
    """Today where the use case is asked about, not in UTC.

    The two disagree from 19:00 to midnight in Bogotá, and a test reading the
    UTC date there compared `days_left` against tomorrow.
    """
    return today_in(zone_of(TIMEZONE))


def day_of_this_month(day: int) -> dt.date:
    """A day of the month the test is running in, never past its end."""
    now = today()
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
    on: dt.date | None = None,
    currency: Currency = Currency.COP,
    direction: MovementDirection = MovementDirection.OUTGOING,
    transfer: TransferLeg | None = None,
) -> Transaction:
    movement = Transaction.enter_manually(
        user_id=USER,
        direction=direction,
        amount=money(amount, currency),
        occurred_at=PosixTime.from_datetime(
            dt.datetime.combine(on or day_of_this_month(5), MIDDAY, tzinfo=dt.UTC),
        ),
        counterparty="Mercado",
    )
    movement.transfer = transfer

    return ledger.keep(movement)


def declare_bill(
    bills: FakeBills,
    *,
    amount: str = "120000",
    day: int = 4,
) -> ScheduledBill:
    bill = ScheduledBill.declare(
        user_id=USER,
        name="Gimnasio",
        amount=money(amount),
        cadence=BillCadence.MONTHLY,
        starts_on=day_of_this_month(day),
    )
    bills.save(bill)

    return bill


def pay(ledger: InMemoryLedger, bill: ScheduledBill, *, amount: str) -> Transaction:
    """The ledger row a confirmation would have written.

    Written straight into the ledger under the id the bill derives, because
    that derivation *is* how «paid» is answered — there is no flag anywhere to
    set instead.
    """
    period = bill.starts_on
    movement = Transaction.enter_manually(
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=money(amount),
        occurred_at=PosixTime.from_datetime(
            dt.datetime.combine(period, MIDDAY, tzinfo=dt.UTC),
        ),
        counterparty=bill.name,
    )
    movement.id = bill.charge_id(period)

    return ledger.keep(movement)


def read(
    *,
    plans: FakePlans | None = None,
    ledger: InMemoryLedger | None = None,
    bills: FakeBills | None = None,
    accounts: FakeAccounts | None = None,
) -> MonthlyAllowance | None:
    accounts = accounts or FakeAccounts()
    ledger = ledger or InMemoryLedger()
    use_case = ReadMonthlyAllowanceUseCase(
        plans=plans or FakePlans(),
        spending=SummarizeSpendingUseCase(ledger=ledger, accounts=accounts),
        bills=ListBillsUseCase(
            bills=bills or FakeBills(),
            accounts=accounts,
            charges=ledger,
        ),
    )

    return use_case.execute(ReadAllowanceQuery(user_id=USER, timezone=TIMEZONE))


def with_plan(income: str = "5000000", savings: str = "0") -> FakePlans:
    plans = FakePlans()
    ManageMonthlyPlanUseCase(plans=plans).declare(
        DeclarePlanCommand(
            user_id=USER,
            expected_income=money(income),
            savings_target=money(savings),
        ),
    )

    return plans


class TestWithoutAPlan:
    def test_no_plan_is_no_number_at_all(self) -> None:
        """Not zero. A zero reads as «you have nothing left to spend»."""
        assert read() is None

    def test_forgetting_the_plan_takes_the_number_away(self) -> None:
        plans = with_plan()
        manage = ManageMonthlyPlanUseCase(plans=plans)

        assert manage.forget(USER) is True
        assert read(plans=plans) is None

    def test_forgetting_twice_is_not_an_error(self) -> None:
        plans = with_plan()
        manage = ManageMonthlyPlanUseCase(plans=plans)
        manage.forget(USER)

        assert manage.forget(USER) is False


class TestTheSubtraction:
    def test_nothing_spent_and_nothing_owed_leaves_the_whole_income(self) -> None:
        allowance = read(plans=with_plan("5000000"))

        assert allowance is not None
        assert allowance.available == Decimal("5000000")
        assert allowance.spent == Decimal(0)
        assert allowance.committed == Decimal(0)

    def test_what_is_kept_never_becomes_spendable(self) -> None:
        allowance = read(plans=with_plan("5000000", savings="1000000"))

        assert allowance is not None
        assert allowance.available == Decimal("4000000")

    def test_this_month_spending_comes_off(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "300000")

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.spent == Decimal("300000")
        assert allowance.available == Decimal("4700000")

    def test_last_month_spending_does_not(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "300000", on=day_of_this_month(1) - dt.timedelta(days=1))

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.spent == Decimal(0)

    def test_money_coming_in_is_not_a_negative_expense(self) -> None:
        """The income is declared, not counted. A refund does not raise the
        allowance above what the plan says the month brings in."""
        ledger = InMemoryLedger()
        spend(ledger, "80000", direction=MovementDirection.INCOMING)

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.spent == Decimal(0)
        assert allowance.available == Decimal("5000000")

    def test_moving_money_between_your_own_accounts_is_not_spending(self) -> None:
        ledger = InMemoryLedger()
        spend(
            ledger,
            "900000",
            transfer=TransferLeg(
                transfer_id=TransferId(value="transfer-1"),
                role=TransferRole.SOURCE,
            ),
        )

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.spent == Decimal(0)

    def test_another_currency_is_not_subtracted_from_pesos(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "40", currency=Currency.USD)

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.spent == Decimal(0)
        assert allowance.currency is Currency.COP

    def test_overspending_is_reported_negative_rather_than_floored(self) -> None:
        ledger = InMemoryLedger()
        spend(ledger, "6000000")

        allowance = read(plans=with_plan("5000000"), ledger=ledger)

        assert allowance is not None
        assert allowance.available == Decimal("-1000000")


class TestCommitments:
    def test_a_charge_nobody_has_answered_for_comes_off(self) -> None:
        bills = FakeBills()
        declare_bill(bills, amount="120000")

        allowance = read(plans=with_plan("5000000"), bills=bills)

        assert allowance is not None
        assert allowance.committed == Decimal("120000")
        assert allowance.available == Decimal("4880000")

    def test_a_paid_charge_is_subtracted_exactly_once(self) -> None:
        """The mistake this module exists to avoid.

        Once it is confirmed the charge is a ledger row, so it is in `spent`.
        Subtracting what the month *costs* as well would take it off twice and
        leave the allowance short by exactly the charges already paid.
        """
        ledger = InMemoryLedger()
        bills = FakeBills()
        bill = declare_bill(bills, amount="120000")
        pay(ledger, bill, amount="120000")

        allowance = read(plans=with_plan("5000000"), ledger=ledger, bills=bills)

        assert allowance is not None
        assert allowance.spent == Decimal("120000")
        assert allowance.committed == Decimal(0)
        assert allowance.available == Decimal("4880000")

    def test_a_charge_confirmed_for_more_than_declared_counts_what_moved(
        self,
    ) -> None:
        ledger = InMemoryLedger()
        bills = FakeBills()
        bill = declare_bill(bills, amount="120000")
        pay(ledger, bill, amount="130000")

        allowance = read(plans=with_plan("5000000"), ledger=ledger, bills=bills)

        assert allowance is not None
        assert allowance.spent == Decimal("130000")
        assert allowance.committed == Decimal(0)

    def test_a_skipped_charge_is_owed_by_nobody(self) -> None:
        bills = FakeBills()
        bill = declare_bill(bills, amount="120000")
        bill.skip(bill.starts_on)
        bills.save(bill)

        allowance = read(plans=with_plan("5000000"), bills=bills)

        assert allowance is not None
        assert allowance.committed == Decimal(0)
        assert allowance.available == Decimal("5000000")


class TestTheWindow:
    def test_it_answers_for_the_calendar_month_and_counts_today(self) -> None:
        allowance = read(plans=with_plan())

        assert allowance is not None
        assert allowance.since == day_of_this_month(1)
        assert allowance.until >= allowance.since
        assert allowance.days_left == (allowance.until - today()).days + 1
        assert allowance.days_left >= 1
