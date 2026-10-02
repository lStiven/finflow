"""Declaring bills, and the two numbers the month adds up to.

The domain already pins the calendar. What is pinned here is everything the
calendar cannot answer on its own: that a bill pointing at a closed account
reads as frozen without anything having written that down, that income is
declared but never netted against spending, and that the totals stay apart by
currency.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.autopay import (
    SettleDueChargesCommand,
    SettleDueChargesUseCase,
    SettlementAction,
    SettlementView,
)
from personal_finance.contexts.financial.application.bills import (
    AmendBillCommand,
    BillsView,
    ConfirmChargeCommand,
    DeclareBillCommand,
    ListBillsQuery,
    ListBillsUseCase,
    ManageBillsUseCase,
    NoSuchBillError,
    SettleBillChargeUseCase,
)
from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    ManageTransactionsUseCase,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    BillStatus,
    ChargeSource,
    OccurrenceState,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import AccountClosedError
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
    TransactionOrigin,
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


class FakeBills:
    """The port, in a dict. Keyed by owner as well as id, which is the rule
    the real one exists to keep."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, BillId], ScheduledBill] = {}

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        return self.rows.get((user_id, bill_id))

    def list_by_user(self, user_id: UserId) -> list[ScheduledBill]:
        return [bill for (owner, _), bill in self.rows.items() if owner == user_id]

    def save(self, bill: ScheduledBill) -> None:
        self.rows[(bill.user_id, bill.id)] = bill

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        return self.rows.pop((user_id, bill_id), None) is not None


class FakeCharges:
    """The ledger, as the bills side of it sees it: a dict of rows by id.

    Nothing writes here from the bills code; rows arrive because something
    recorded a movement. Which is the point — "paid" has no storage of its own
    and this fake has no way to fake one.
    """

    def __init__(self, rows: list[Transaction] | None = None) -> None:
        self.rows: dict[tuple[UserId, str], Transaction] = {
            (row.user_id, row.id.value): row for row in rows or []
        }
        self.calls = 0

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> dict[str, Transaction]:
        self.calls += 1

        return {
            movement_id: self.rows[(user_id, movement_id)]
            for movement_id in movement_ids
            if (user_id, movement_id) in self.rows
        }

    def add(self, transaction: Transaction) -> None:
        self.rows[(transaction.user_id, transaction.id.value)] = transaction


class FakeAccounts:
    def __init__(self, accounts: list[Account] | None = None) -> None:
        self.accounts = accounts or []

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        for account in self.accounts:
            if account.user_id == user_id and account.id == account_id:
                return account

        return None

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return [a for a in self.accounts if a.user_id == user_id]


def _account(*, closed: bool = False, user_id: UserId = USER) -> Account:
    account = Account.open(
        user_id=user_id,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.now(),
    )

    if closed:
        account.close(PosixTime.now())

    return account


def _money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def _declare(
    *,
    name: str = "Gimnasio",
    amount: str = "120000",
    currency: Currency = Currency.COP,
    starts_on: dt.date = dt.date(2026, 9, 4),
    **rest: object,
) -> DeclareBillCommand:
    return DeclareBillCommand(
        user_id=USER,
        name=name,
        amount=_money(amount, currency),
        cadence=BillCadence.MONTHLY,
        starts_on=starts_on,
        **rest,  # type: ignore[arg-type]
    )


def _manage(
    bills: FakeBills | None = None,
    accounts: FakeAccounts | None = None,
    charges: FakeCharges | None = None,
) -> tuple[ManageBillsUseCase, FakeBills, FakeAccounts]:
    bills = bills or FakeBills()
    accounts = accounts or FakeAccounts()

    return (
        ManageBillsUseCase(
            bills=bills,
            accounts=accounts,
            charges=charges or FakeCharges(),
        ),
        bills,
        accounts,
    )


def _view(
    bills: FakeBills,
    accounts: FakeAccounts | None = None,
    charges: FakeCharges | None = None,
    **query: object,
) -> BillsView:
    use_case = ListBillsUseCase(
        bills=bills,
        accounts=accounts or FakeAccounts(),
        charges=charges or FakeCharges(),
    )

    return use_case.execute(
        ListBillsQuery(user_id=USER, timezone=TIMEZONE, **query),  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# Declaring and correcting
# ----------------------------------------------------------------------


def test_declaring_stores_the_bill_and_nothing_else() -> None:
    use_case, bills, _ = _manage()

    bill = use_case.declare(_declare()).bill

    assert bills.find(user_id=USER, bill_id=bill.id) is bill
    assert bill.pull_events() == []


def test_a_bill_cannot_name_an_account_its_owner_does_not_have() -> None:
    """A typo here would produce a bill that can never be paid into anything,
    and the mistake would only surface the day it is confirmed."""
    use_case, _, _ = _manage()

    with pytest.raises(AccountNotFoundError):
        use_case.declare(_declare(account_id=AccountId.new()))


def test_a_bill_cannot_name_somebody_elses_account() -> None:
    accounts = FakeAccounts([_account(user_id=STRANGER)])
    use_case, _, _ = _manage(accounts=accounts)

    with pytest.raises(AccountNotFoundError):
        use_case.declare(_declare(account_id=accounts.accounts[0].id))


def test_amending_a_bill_that_is_not_yours_reads_as_missing() -> None:
    """Never "forbidden": that would confirm the bill exists to whoever
    guessed its id."""
    use_case, _, _ = _manage()
    bill = use_case.declare(_declare()).bill

    with pytest.raises(NoSuchBillError):
        use_case.amend(
            AmendBillCommand(user_id=STRANGER, bill_id=bill.id, name="Otro"),
        )


def test_forgetting_a_bill_that_is_not_there_is_an_error_not_a_silence() -> None:
    use_case, _, _ = _manage()

    with pytest.raises(NoSuchBillError):
        use_case.forget(user_id=USER, bill_id=BillId.new())


def test_an_amended_bill_is_stored() -> None:
    use_case, bills, _ = _manage()
    bill = use_case.declare(_declare()).bill

    use_case.amend(
        AmendBillCommand(user_id=USER, bill_id=bill.id, amount=_money("135000")),
    )

    stored = bills.find(user_id=USER, bill_id=bill.id)
    assert stored is not None
    assert stored.amount == _money("135000")


def test_pausing_and_resuming_are_stored() -> None:
    use_case, bills, _ = _manage()
    bill = use_case.declare(_declare()).bill

    use_case.pause(user_id=USER, bill_id=bill.id)
    paused = bills.find(user_id=USER, bill_id=bill.id)
    assert paused is not None
    assert paused.status is BillStatus.PAUSED

    use_case.resume(user_id=USER, bill_id=bill.id)
    resumed = bills.find(user_id=USER, bill_id=bill.id)
    assert resumed is not None
    assert resumed.status is BillStatus.ACTIVE


# ----------------------------------------------------------------------
# Reading the window
# ----------------------------------------------------------------------


def test_the_window_defaults_to_the_month_it_is_read_in() -> None:
    view = _view(FakeBills())

    assert view.since.day == 1
    assert view.until.month == view.since.month
    assert (view.until + dt.timedelta(days=1)).day == 1


def test_half_a_window_is_refused_rather_than_completed() -> None:
    """Completing it with a month would silently answer a different question
    than the one asked."""
    with pytest.raises(ValueError, match="both ends"):
        _view(FakeBills(), since=dt.date(2026, 9, 1))


def test_a_window_that_ends_before_it_starts_is_refused() -> None:
    with pytest.raises(ValueError, match="before it starts"):
        _view(FakeBills(), since=dt.date(2026, 9, 30), until=dt.date(2026, 9, 1))


def test_the_two_totals_answer_two_different_questions() -> None:
    use_case, bills, _ = _manage()
    use_case.declare(
        _declare(name="Arriendo", amount="900000", starts_on=dt.date(2026, 9, 1))
    )
    use_case.declare(
        _declare(name="Gimnasio", amount="120000", starts_on=dt.date(2026, 9, 28))
    )

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))
    [total] = view.totals

    assert total.expected == Decimal("1020000")
    # With nothing confirmed the two figures agree, which is the honest answer:
    # everything the month costs is still to pay. What separates them is
    # settling a charge, not the day going past — that is the whole change
    # from what `upcoming` used to mean.
    assert total.outstanding == Decimal("1020000")


def test_currencies_are_never_summed_into_each_other() -> None:
    use_case, bills, _ = _manage()
    use_case.declare(_declare(name="Arriendo", amount="900000"))
    use_case.declare(_declare(name="Dominio", amount="15", currency=Currency.USD))

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))

    assert {(t.currency, t.expected) for t in view.totals} == {
        (Currency.COP, Decimal("900000")),
        (Currency.USD, Decimal("15")),
    }


def test_declared_income_is_listed_but_never_netted_off_the_total() -> None:
    """A salary is a real recurring series and E3 will want it. Subtracting it
    here would report a month costing less than it costs."""
    use_case, bills, _ = _manage()
    use_case.declare(_declare(name="Gimnasio", amount="120000"))
    use_case.declare(
        _declare(
            name="Nómina",
            amount="4000000",
            direction=MovementDirection.INCOMING,
        ),
    )

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))
    [total] = view.totals

    assert total.expected == Decimal("120000")
    assert len(view.bills) == 2


def test_a_paused_bill_is_listed_and_adds_nothing() -> None:
    use_case, bills, _ = _manage()
    bill = use_case.declare(_declare()).bill
    use_case.pause(user_id=USER, bill_id=bill.id)

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))

    assert len(view.bills) == 1
    assert view.occurrences == ()
    assert view.totals == ()


def test_a_bill_whose_account_is_closed_reads_as_frozen() -> None:
    """Derived, never stored: an account is closed and never deleted, so
    reopening it has to un-freeze the bill without anything remembering to."""
    account = _account(closed=True)
    accounts = FakeAccounts([account])
    use_case, bills, _ = _manage(accounts=accounts)
    use_case.declare(_declare(account_id=account.id))

    [summary] = _view(bills, accounts).bills

    assert summary.frozen is True


def test_a_bill_on_an_open_account_is_not_frozen() -> None:
    account = _account()
    accounts = FakeAccounts([account])
    use_case, bills, _ = _manage(accounts=accounts)
    use_case.declare(_declare(account_id=account.id))

    [summary] = _view(bills, accounts).bills

    assert summary.frozen is False


def test_a_bill_without_an_account_is_not_frozen_either() -> None:
    """Cash, or somebody who declared no accounts at all. Absence is not the
    same as an account that went away."""
    use_case, bills, _ = _manage()
    use_case.declare(_declare())

    [summary] = _view(bills).bills

    assert summary.frozen is False


def test_each_bill_says_when_it_is_coming_next() -> None:
    use_case, bills, _ = _manage()
    use_case.declare(_declare(starts_on=dt.date(2020, 1, 4)))

    [summary] = _view(bills).bills

    assert summary.next_occurrence is not None
    assert summary.next_occurrence.due_on.day == 4


def test_a_bill_nobody_will_be_charged_for_again_says_so() -> None:
    use_case, bills, _ = _manage()
    bill = use_case.declare(_declare()).bill
    use_case.pause(user_id=USER, bill_id=bill.id)

    [summary] = _view(bills).bills

    assert summary.next_occurrence is None


def test_charges_come_back_in_date_order_across_every_bill() -> None:
    use_case, bills, _ = _manage()
    use_case.declare(_declare(name="Tarde", starts_on=dt.date(2026, 9, 28)))
    use_case.declare(_declare(name="Temprano", starts_on=dt.date(2026, 9, 2)))

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))

    assert [o.due_on for o in view.occurrences] == [
        dt.date(2026, 9, 2),
        dt.date(2026, 9, 28),
    ]


def test_one_persons_bills_are_never_in_anothers_view() -> None:
    bills = FakeBills()
    ManageBillsUseCase(
        bills=bills,
        accounts=FakeAccounts(),
        charges=FakeCharges(),
    ).declare(_declare())
    bills.rows[(STRANGER, BillId.new())] = ScheduledBill.declare(
        user_id=STRANGER,
        name="Ajeno",
        amount=_money("500000"),
        cadence=BillCadence.MONTHLY,
        starts_on=dt.date(2026, 9, 4),
    )

    view = _view(bills)

    assert [summary.bill.name for summary in view.bills] == ["Gimnasio"]


# ----------------------------------------------------------------------
# Answering for a charge
# ----------------------------------------------------------------------


class FakeLedgerAccounts(FakeAccounts):
    """`FakeAccounts` plus the rest of `AccountRepository`.

    Confirming a charge runs through the very use case that records a movement
    entered by hand, and that one writes balances. The routing half is stubbed:
    nothing a bill does matches an alert to an account.
    """

    def save(self, account: Account) -> None:
        if account not in self.accounts:
            self.accounts.append(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        for account in self.list_by_user(user_id):
            if fingerprint in account.fingerprints:
                return account

        return None

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        del fingerprint
        self.save(account)


class FakeLedger:
    """The ledger, with the one behaviour this delivery rests on: a key it
    already holds is refused rather than written twice."""

    def __init__(self, charges: FakeCharges) -> None:
        self.charges = charges
        self.records = 0

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        del balance_delta
        self.records += 1

        if (transaction.user_id, transaction.id.value) in self.charges.rows:
            return False

        self.charges.add(transaction)

        return True

    def save(self, transaction: Transaction) -> None:
        self.charges.add(transaction)

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[object],
    ) -> None:
        del reversals

        for transaction in transactions:
            self.charges.rows.pop((transaction.user_id, transaction.id.value), None)

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        return self.charges.rows.get((user_id, transaction_id))

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> list[Transaction]:
        return [
            row
            for (owner, _), row in self.charges.rows.items()
            if owner == user_id and row.account_id == account_id
        ]


class NullPublisher:
    def publish(self, events: Sequence[object]) -> None:
        del events


def _settling(
    *,
    account: Account | None = None,
) -> tuple[SettleBillChargeUseCase, FakeBills, FakeCharges, FakeLedgerAccounts]:
    bills = FakeBills()
    accounts = FakeLedgerAccounts([account] if account is not None else [])
    charges = FakeCharges()
    ledger = FakeLedger(charges)

    return (
        SettleBillChargeUseCase(
            bills=bills,
            accounts=accounts,
            charges=charges,
            transactions=ManageTransactionsUseCase(
                accounts=accounts,
                ledger=ledger,  # type: ignore[arg-type]
                event_publisher=NullPublisher(),
            ),
        ),
        bills,
        charges,
        accounts,
    )


def _keep(bills: FakeBills, **overrides: object) -> ScheduledBill:
    values: dict[str, object] = {
        "user_id": USER,
        "name": "Gimnasio",
        "amount": _money("120000"),
        "cadence": BillCadence.MONTHLY,
        "starts_on": dt.date(2026, 9, 4),
    }
    bill = ScheduledBill.declare(**(values | overrides))  # type: ignore[arg-type]
    bills.save(bill)

    return bill


def test_confirming_writes_one_movement_carrying_the_bills_name() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)

    settled = settle.confirm(
        ConfirmChargeCommand(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
        ),
    )

    assert settled.movement is not None
    assert settled.movement.counterparty == "Gimnasio"
    assert settled.movement.amount == _money("120000")
    assert settled.movement.origin is TransactionOrigin.SCHEDULED
    assert settled.occurrence.state is OccurrenceState.PAID
    assert len(charges.rows) == 1


def test_confirming_twice_is_one_charge() -> None:
    """Not because anything counts: the row's id comes from the bill and the
    period, so the second attempt finds the row already there."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    command = ConfirmChargeCommand(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
    )

    first = settle.confirm(command)
    second = settle.confirm(command)

    assert len(charges.rows) == 1
    assert first.movement is not None
    assert second.movement is not None
    assert first.movement.id == second.movement.id


def test_confirming_again_at_another_price_does_not_write_a_second_row() -> None:
    """The amount is out of the key on purpose. Were it in, a correction would
    take the money a second time."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)

    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )
    again = settle.confirm(
        ConfirmChargeCommand(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            amount=_money("130000"),
        ),
    )

    assert len(charges.rows) == 1
    assert again.movement is not None
    assert again.movement.amount == _money("120000")


def test_two_periods_of_one_bill_are_two_charges() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)

    for period in (dt.date(2026, 9, 4), dt.date(2026, 10, 4)):
        settle.confirm(
            ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=period),
        )

    assert len(charges.rows) == 2


def test_a_charge_moves_the_bills_account() -> None:
    account = _account()
    settle, bills, _, _ = _settling(account=account)
    bill = _keep(bills, account_id=account.id)

    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    assert account.balance.signed_amount == Decimal("-120000")


def test_a_period_the_bill_is_not_charged_on_is_refused() -> None:
    """The amount and the account come off the bill, so an invented period
    would be money moving for a charge that does not exist."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)

    with pytest.raises(ValueError, match="not charged on"):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=USER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 5),
            ),
        )

    assert charges.rows == {}


def test_a_paused_bill_charges_nothing() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    bill.pause()
    bills.save(bill)

    with pytest.raises(ValueError, match="paused"):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=USER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 4),
            ),
        )

    assert charges.rows == {}


def test_a_bill_on_a_closed_account_charges_nothing() -> None:
    """A frozen bill is visible and says what it costs. What it must not do is
    charge anything — the account cannot take the movement."""
    account = _account(closed=True)
    settle, bills, charges, _ = _settling(account=account)
    bill = _keep(bills, account_id=account.id)

    with pytest.raises(AccountClosedError, match="closed account"):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=USER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 4),
            ),
        )

    assert charges.rows == {}


def test_somebody_elses_bill_reads_as_missing() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)

    with pytest.raises(NoSuchBillError):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=STRANGER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 4),
            ),
        )


def test_a_charge_in_another_currency_is_refused_rather_than_converted() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)

    with pytest.raises(ValueError, match="cannot be confirmed in"):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=USER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 4),
                amount=_money("30", Currency.USD),
            ),
        )


def test_undoing_a_confirmation_erases_the_movement_and_the_answer() -> None:
    """Nothing else has to be undone: "paid" was never written down."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    settled = settle.undo_confirmation(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
    )

    assert charges.rows == {}
    assert settled.occurrence.state is not OccurrenceState.PAID


def test_undoing_a_confirmation_that_never_happened_is_silent() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)

    settled = settle.undo_confirmation(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
    )

    assert settled.occurrence.state is not OccurrenceState.PAID
    assert settled.movement is None


def test_undoing_a_confirmation_gives_the_balance_back() -> None:
    account = _account()
    settle, bills, _, _ = _settling(account=account)
    bill = _keep(bills, account_id=account.id)
    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    settle.undo_confirmation(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
    )

    assert account.balance.signed_amount == Decimal("0")


def test_skipping_stores_the_answer_and_writes_no_money() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)

    settled = settle.skip(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))

    assert settled.occurrence.state is OccurrenceState.SKIPPED
    assert charges.rows == {}
    stored = bills.find(user_id=USER, bill_id=bill.id)
    assert stored is not None
    assert dt.date(2026, 9, 4) in stored.skipped


def test_a_paid_charge_cannot_be_skipped() -> None:
    """A skip over a confirmed charge would hide a movement that moved a
    balance, and nothing on screen would say why the figures stopped adding
    up."""
    settle, bills, _, _ = _settling()
    bill = _keep(bills)
    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    with pytest.raises(ValueError, match="already paid"):
        settle.skip(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))


def test_a_skip_can_be_taken_back_through_the_use_case() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)
    settle.skip(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))

    settled = settle.undo_skip(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
    )

    assert settled.occurrence.state is not OccurrenceState.SKIPPED


# ----------------------------------------------------------------------
# What settling does to the two figures
# ----------------------------------------------------------------------


def _paid(bill: ScheduledBill, period: dt.date, amount: str = "120000") -> Transaction:
    return Transaction.confirm_scheduled(
        user_id=bill.user_id,
        bill_id=bill.id.value,
        period=period,
        direction=bill.direction,
        amount=_money(amount),
        occurred_at=PosixTime.now(),
        counterparty=bill.name,
    )


def test_a_paid_charge_leaves_what_is_outstanding_and_stays_in_what_it_cost() -> None:
    bills = FakeBills()
    charges = FakeCharges()
    bill = ScheduledBill.declare(
        user_id=USER,
        name="Arriendo",
        amount=_money("900000"),
        cadence=BillCadence.MONTHLY,
        starts_on=dt.date(2026, 9, 1),
    )
    bills.save(bill)
    charges.add(_paid(bill, dt.date(2026, 9, 1), amount="900000"))

    view = _view(
        bills,
        charges=charges,
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
    )
    [total] = view.totals

    assert total.expected == Decimal("900000")
    assert total.outstanding == Decimal("0")


def test_what_the_month_cost_is_what_actually_moved() -> None:
    """A price that went up is the figure that matches the account, not the
    one the bill still projects."""
    bills = FakeBills()
    charges = FakeCharges()
    bill = ScheduledBill.declare(
        user_id=USER,
        name="Gimnasio",
        amount=_money("120000"),
        cadence=BillCadence.MONTHLY,
        starts_on=dt.date(2026, 9, 4),
    )
    bills.save(bill)
    charges.add(_paid(bill, dt.date(2026, 9, 4), amount="130000"))

    view = _view(
        bills,
        charges=charges,
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
    )
    [total] = view.totals

    assert total.expected == Decimal("130000")


def test_a_skipped_charge_is_in_neither_figure() -> None:
    bills = FakeBills()
    bill = ScheduledBill.declare(
        user_id=USER,
        name="Gimnasio",
        amount=_money("120000"),
        cadence=BillCadence.MONTHLY,
        starts_on=dt.date(2026, 9, 4),
    )
    bill.skip(dt.date(2026, 9, 4))
    bills.save(bill)

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))

    assert view.totals == ()


def test_the_next_charge_steps_over_one_already_paid() -> None:
    """A card saying "el 4, hoy" for a charge confirmed this morning is the
    screen telling somebody to pay something twice."""
    bills = FakeBills()
    charges = FakeCharges()
    today = dt.datetime.now(dt.UTC).date()
    bill = ScheduledBill.declare(
        user_id=USER,
        name="Gimnasio",
        amount=_money("120000"),
        cadence=BillCadence.WEEKLY,
        starts_on=today,
    )
    bills.save(bill)
    charges.add(_paid(bill, today))

    view = _view(bills, charges=charges)
    [summary] = view.bills

    assert summary.next_occurrence is not None
    assert summary.next_occurrence.due_on == today + dt.timedelta(days=7)


def test_reading_a_month_of_bills_asks_the_ledger_once() -> None:
    """One call whatever is declared. A round trip per charge would put tens of
    them behind a screen somebody opens to read two numbers."""
    bills = FakeBills()
    charges = FakeCharges()

    for name in ("Arriendo", "Gimnasio", "Internet", "Streaming"):
        bills.save(
            ScheduledBill.declare(
                user_id=USER,
                name=name,
                amount=_money("100000"),
                cadence=BillCadence.WEEKLY,
                starts_on=dt.date(2026, 9, 1),
            ),
        )

    _view(bills, charges=charges, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))

    assert charges.calls == 1


class LaggingCharges(FakeCharges):
    """A lookup that has not caught up with what was just written.

    `BatchGetItem` is eventually consistent, so this is not a hypothetical: it
    is the ordinary state of the read that happens microseconds after the
    write it is reporting on.
    """

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> dict[str, Transaction]:
        del user_id, movement_ids
        self.calls += 1

        return {}


def test_confirming_answers_paid_even_when_the_lookup_has_not_caught_up() -> None:
    """The response reports the write, not a read of it.

    Re-reading here is how an endpoint answers `state: expected` and hands back
    the movement it just wrote in the same breath — two halves of one answer
    disagreeing, which is the shape of the `frozen` bug this family already
    had once.
    """
    bills = FakeBills()
    accounts = FakeLedgerAccounts([])
    charges = LaggingCharges()
    settle = SettleBillChargeUseCase(
        bills=bills,
        accounts=accounts,
        charges=charges,
        transactions=ManageTransactionsUseCase(
            accounts=accounts,
            ledger=FakeLedger(charges),  # type: ignore[arg-type]
            event_publisher=NullPublisher(),
        ),
    )
    bill = _keep(bills)

    settled = settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    assert settled.occurrence.state is OccurrenceState.PAID
    assert settled.occurrence.payment is not None
    assert settled.movement is not None
    assert settled.occurrence.payment.movement_id == settled.movement.id.value


def test_the_next_charge_steps_over_the_one_just_confirmed() -> None:
    """Same reason, one field along: a card still saying "hoy" for the charge
    somebody confirmed a second ago is the screen asking for it twice."""
    bills = FakeBills()
    accounts = FakeLedgerAccounts([])
    charges = LaggingCharges()
    settle = SettleBillChargeUseCase(
        bills=bills,
        accounts=accounts,
        charges=charges,
        transactions=ManageTransactionsUseCase(
            accounts=accounts,
            ledger=FakeLedger(charges),  # type: ignore[arg-type]
            event_publisher=NullPublisher(),
        ),
    )
    today = dt.datetime.now(dt.UTC).date()
    bill = _keep(bills, cadence=BillCadence.WEEKLY, starts_on=today)

    settled = settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=today),
    )

    assert settled.bill.next_occurrence is not None
    assert settled.bill.next_occurrence.due_on == today + dt.timedelta(days=7)


# ----------------------------------------------------------------------
# A movement that answered for a charge
# ----------------------------------------------------------------------


def _moved(
    *,
    counterparty: str = "Gimnasio",
    amount: str = "120000",
    currency: Currency = Currency.COP,
    day: dt.date = dt.date(2026, 9, 4),
    direction: MovementDirection = MovementDirection.OUTGOING,
    user_id: UserId = USER,
) -> Transaction:
    """A movement the bank announced, as the ledger already holds it."""
    return Transaction.enter_manually(
        user_id=user_id,
        direction=direction,
        amount=_money(amount, currency),
        occurred_at=PosixTime.from_epoch_seconds(
            int(dt.datetime.combine(day, dt.time(hour=12), tzinfo=dt.UTC).timestamp()),
        ),
        counterparty=counterparty,
    )


def test_linking_answers_the_charge_without_writing_anything() -> None:
    """The whole point. Confirming instead would record the same money twice
    — the drift this feature exists to remove, with the sign flipped."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    before = len(charges.rows)

    settled = settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    assert settled.occurrence.state is OccurrenceState.PAID
    assert settled.occurrence.payment is not None
    assert settled.occurrence.payment.source is ChargeSource.MATCHED
    assert settled.movement is None
    assert len(charges.rows) == before


def test_a_linked_charge_reads_paid_on_the_next_listing() -> None:
    """Not just in the answer to the write: the link is stored on the bill
    and read back against the ledger, so a reload agrees."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved(amount="126000")
    charges.add(paid)

    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )
    view = _view(
        bills, charges=charges, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30)
    )

    assert [each.state for each in view.occurrences] == [OccurrenceState.PAID]
    assert view.totals[0].expected == Decimal("126000")
    assert view.totals[0].outstanding == Decimal("0")


def test_erasing_the_movement_un_pays_a_linked_charge() -> None:
    """The same property a confirmed charge has, and for the same reason: the
    link is read against the ledger rather than believed."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    charges.rows.pop((USER, paid.id.value))
    view = _view(
        bills, charges=charges, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30)
    )

    assert [each.state for each in view.occurrences] != [OccurrenceState.PAID]


def test_unlinking_forgets_the_claim_and_erases_nothing() -> None:
    """The difference from undoing a confirmation: the movement is the bank's
    fact, and it stays spent and counted."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    settled = settle.unlink(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))

    assert settled.occurrence.state is not OccurrenceState.PAID
    assert (USER, paid.id.value) in charges.rows


def test_unlinking_what_was_never_linked_is_silent() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)

    settled = settle.unlink(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))

    assert settled.occurrence.state is not OccurrenceState.PAID


def test_a_charge_already_confirmed_cannot_also_be_linked() -> None:
    """Two answers claiming the same charge was paid, pointing at different
    money."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )
    paid = _moved()
    charges.add(paid)

    with pytest.raises(ValueError, match="already paid"):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            movement_id=paid.id.value,
        )


def test_a_movement_this_app_wrote_cannot_answer_for_a_charge() -> None:
    """Another bill's charge settling this one would be the feature
    confirming itself."""
    settle, bills, charges, _ = _settling()
    other = _keep(bills, name="Arriendo")
    bill = _keep(bills)
    settle.confirm(
        ConfirmChargeCommand(
            user_id=USER, bill_id=other.id, period=dt.date(2026, 9, 4)
        ),
    )
    written = charges.rows[(USER, other.charge_id(dt.date(2026, 9, 4)).value)]

    with pytest.raises(ValueError, match="wrote itself"):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            movement_id=written.id.value,
        )


def test_money_going_the_other_way_cannot_be_this_charge() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    arrived = _moved(direction=MovementDirection.INCOMING)
    charges.add(arrived)

    with pytest.raises(ValueError, match="other way"):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            movement_id=arrived.id.value,
        )


def test_a_movement_in_another_currency_cannot_be_this_charge() -> None:
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    abroad = _moved(amount="40", currency=Currency.USD)
    charges.add(abroad)

    with pytest.raises(ValueError, match="cannot be its charge"):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            movement_id=abroad.id.value,
        )


def test_a_movement_that_is_not_there_is_refused_as_missing() -> None:
    settle, bills, _, _ = _settling()
    bill = _keep(bills)

    with pytest.raises(TransactionNotFoundError):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 9, 4),
            movement_id="nothing",
        )


def test_one_movement_cannot_pay_three_months() -> None:
    """Without this, one charge linked to January, February and March would
    read as three months paid on the strength of one figure."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    with pytest.raises(ValueError, match="already the charge"):
        settle.link(
            user_id=USER,
            bill_id=bill.id,
            period=dt.date(2026, 10, 4),
            movement_id=paid.id.value,
        )


def test_linking_the_same_movement_to_the_same_charge_again_is_not_an_error() -> None:
    """A screen retrying a request it is unsure about must not be told off:
    the answer is the one already there."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)

    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )
    again = settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    assert again.occurrence.state is OccurrenceState.PAID


def test_a_charge_paid_by_a_movement_cannot_be_skipped_before_unlinking() -> None:
    """And the refusal names the right undo: erasing that movement would
    throw away the bank's own fact."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    with pytest.raises(ValueError, match="Unlink it"):
        settle.skip(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))


# ----------------------------------------------------------------------
# Arming a bill
# ----------------------------------------------------------------------


def test_arming_a_bill_records_the_day_it_was_armed() -> None:
    use_case, bills, _ = _manage()
    bill = use_case.declare(_declare()).bill

    armed = use_case.set_autopay(
        user_id=USER,
        bill_id=bill.id,
        enabled=True,
        timezone=TIMEZONE,
    ).bill

    assert armed.autopay is True
    assert armed.autopay_from is not None
    assert bills.find(user_id=USER, bill_id=bill.id) is armed


def test_a_bill_on_a_closed_account_cannot_be_armed() -> None:
    """A charge that cannot be paid into anything must not be armed to pay
    itself."""
    account = _account(closed=True)
    use_case, _, _ = _manage(accounts=FakeAccounts([account]))
    bill = use_case.declare(_declare()).bill
    bill.account_id = account.id

    with pytest.raises(AccountClosedError):
        use_case.set_autopay(
            user_id=USER,
            bill_id=bill.id,
            enabled=True,
            timezone=TIMEZONE,
        )


def test_undoing_an_automatic_charge_stops_it_coming_straight_back() -> None:
    """With autopay on, "expected" means "will be charged", so an undo that
    left the charge expected would be a button that undoes nothing."""
    settle, bills, _, _ = _settling()
    bill = _keep(bills)
    bill.start_autopay(today=dt.date(2026, 9, 1))
    period = dt.date(2026, 9, 4)
    settle.confirm(ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=period))

    settled = settle.undo_confirmation(user_id=USER, bill_id=bill.id, period=period)

    assert settled.occurrence.state is OccurrenceState.SKIPPED
    assert period in bills.rows[(USER, bill.id)].skipped


def test_undoing_a_charge_on_an_unarmed_bill_leaves_it_expected() -> None:
    """Nothing is going to write it back, so nothing has to be said about
    it."""
    settle, bills, _, _ = _settling()
    bill = _keep(bills)
    period = dt.date(2026, 9, 4)
    settle.confirm(ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=period))

    settled = settle.undo_confirmation(user_id=USER, bill_id=bill.id, period=period)

    assert settled.occurrence.state is not OccurrenceState.SKIPPED
    assert bills.rows[(USER, bill.id)].skipped == frozenset()


# ----------------------------------------------------------------------
# Settling what is due, when the screen opens
# ----------------------------------------------------------------------


class FakeHistory:
    """`MovementHistory`, over the same rows the ledger fake holds."""

    def __init__(self, charges: FakeCharges) -> None:
        self.charges = charges

    def list_all(self, user_id: UserId) -> list[Transaction]:
        return [
            row for (owner, _), row in self.charges.rows.items() if owner == user_id
        ]


def _sweeping() -> tuple[
    SettleDueChargesUseCase,
    FakeBills,
    FakeCharges,
    SettleBillChargeUseCase,
]:
    settle, bills, charges, _ = _settling()

    return (
        SettleDueChargesUseCase(
            bills=bills,
            charges=charges,
            ledger=FakeHistory(charges),
            settle=settle,
        ),
        bills,
        charges,
        settle,
    )


def _today() -> dt.date:
    """Read the way the use case reads it, so the two never disagree."""
    return today_in(zone_of(TIMEZONE))


def _sweep(use_case: SettleDueChargesUseCase) -> SettlementView:
    return use_case.execute(SettleDueChargesCommand(user_id=USER, timezone=TIMEZONE))


def test_an_armed_bill_charges_itself_once_its_window_has_closed() -> None:
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))

    view = _sweep(sweep)

    assert [each.action for each in view.settled] == [SettlementAction.CHARGED]
    assert (USER, bill.charge_id(due).value) in charges.rows


def test_a_charge_whose_window_is_still_open_is_left_alone() -> None:
    """Waiting is the safety: a movement arriving four days late is the
    charge, and writing before then records the same money twice."""
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=2)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))

    view = _sweep(sweep)

    assert view.settled == ()
    assert (USER, bill.charge_id(due).value) not in charges.rows


def test_an_unarmed_bill_is_never_charged_by_the_sweep() -> None:
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)

    view = _sweep(sweep)

    assert view.settled == ()
    assert (USER, bill.charge_id(due).value) not in charges.rows


def test_running_it_twice_charges_once() -> None:
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))

    _sweep(sweep)
    second = _sweep(sweep)

    assert second.settled == ()
    assert (
        len([row for row in charges.rows if row[1] == bill.charge_id(due).value]) == 1
    )


def test_a_movement_that_looks_like_the_charge_answers_it_instead() -> None:
    """The safety net: the bank did announce it, so the charge is answered by
    that movement and nothing is written."""
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))
    paid = _moved(day=due + dt.timedelta(days=2))
    charges.add(paid)
    before = len(charges.rows)

    view = _sweep(sweep)

    assert [each.action for each in view.settled] == [SettlementAction.MATCHED]
    assert len(charges.rows) == before
    assert bills.rows[(USER, bill.id)].linked_movement(due) == paid.id.value


def test_recognising_a_movement_does_not_need_the_bill_to_be_armed() -> None:
    """Recognising money that is already recorded is not acting on somebody's
    behalf — and it is what keeps them from confirming it a second time."""
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    charges.add(_moved(day=due))

    view = _sweep(sweep)

    assert [each.action for each in view.settled] == [SettlementAction.MATCHED]
    assert bills.rows[(USER, bill.id)].linked_movement(due) is not None


def test_two_plausible_movements_are_a_proposal_and_nothing_else() -> None:
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))
    charges.add(_moved(day=due))
    charges.add(_moved(day=due - dt.timedelta(days=1), amount="119000"))
    before = len(charges.rows)

    view = _sweep(sweep)

    assert view.settled == ()
    assert len(view.proposals) == 1
    assert len(view.proposals[0].candidates) == 2
    assert len(charges.rows) == before
    assert bills.rows[(USER, bill.id)].linked == {}


def test_a_movement_that_only_half_matches_blocks_the_automatic_charge() -> None:
    """The same merchant for a figure nowhere near the bill's: it may well be
    the charge, and writing another one would be the double count."""
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))
    charges.add(_moved(day=due, amount="260000"))

    view = _sweep(sweep)

    assert view.settled == ()
    assert [each.occurrence.due_on for each in view.proposals] == [due]
    assert (USER, bill.charge_id(due).value) not in charges.rows


def test_a_paused_bill_is_neither_charged_nor_matched() -> None:
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    bill = _keep(bills, starts_on=due)
    bill.start_autopay(today=due - dt.timedelta(days=1))
    bill.pause()
    charges.add(_moved(day=due))

    view = _sweep(sweep)

    assert view.settled == ()
    assert view.proposals == ()


def test_one_movement_does_not_answer_two_bills_in_one_run() -> None:
    """One payment settles one thing, and the second bill has to come back
    unanswered rather than share it."""
    sweep, bills, charges, _ = _sweeping()
    due = _today() - dt.timedelta(days=10)
    _keep(bills, name="Gimnasio", starts_on=due)
    _keep(bills, name="Gimnasio", starts_on=due)
    charges.add(_moved(day=due))

    view = _sweep(sweep)

    assert len(view.settled) == 1


def test_a_charge_a_movement_already_paid_cannot_also_be_confirmed() -> None:
    """The refusal that keeps the feature from causing the double count it
    exists to remove: the bank's row and this app's row are keyed in
    different spaces, so nothing else would have stopped the second one."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )
    before = len(charges.rows)

    with pytest.raises(ValueError, match="already paid by a movement"):
        settle.confirm(
            ConfirmChargeCommand(
                user_id=USER,
                bill_id=bill.id,
                period=dt.date(2026, 9, 4),
            ),
        )

    assert len(charges.rows) == before


def test_a_link_whose_movement_is_gone_does_not_block_confirming() -> None:
    """Read against the ledger rather than believed. A stored link pointing
    at an erased movement answers nothing, and refusing on it would leave a
    charge nobody could ever confirm."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )
    charges.rows.pop((USER, paid.id.value))

    settled = settle.confirm(
        ConfirmChargeCommand(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4)),
    )

    assert settled.occurrence.state is OccurrenceState.PAID


def test_undoing_a_linked_charge_on_an_armed_bill_does_not_skip_it() -> None:
    """`unpay` erases a row this app wrote; a linked charge has none. Writing
    a skip here would leave the bill linked *and* skipped for one period, and
    the charge would vanish from the month the moment it was unlinked."""
    settle, bills, charges, _ = _settling()
    bill = _keep(bills)
    bill.start_autopay(today=dt.date(2026, 9, 1))
    paid = _moved()
    charges.add(paid)
    settle.link(
        user_id=USER,
        bill_id=bill.id,
        period=dt.date(2026, 9, 4),
        movement_id=paid.id.value,
    )

    settle.undo_confirmation(user_id=USER, bill_id=bill.id, period=dt.date(2026, 9, 4))

    stored = bills.rows[(USER, bill.id)]
    assert stored.skipped == frozenset()
    assert stored.linked_movement(dt.date(2026, 9, 4)) == paid.id.value


def test_a_movement_the_lookup_no_longer_holds_does_not_break_the_screen() -> None:
    """The sweep runs on every visit, so one unlucky charge must not 404 the
    whole page. The listing and the lookup are two reads and can disagree —
    an index catching up, or a movement deleted a second ago."""

    class Vanishing(FakeHistory):
        """Lists a movement the ledger lookup will not find."""

        def __init__(self, charges: FakeCharges, ghost: Transaction) -> None:
            super().__init__(charges)
            self.ghost = ghost

        def list_all(self, user_id: UserId) -> list[Transaction]:
            return [*super().list_all(user_id), self.ghost]

    settle, bills, charges, _ = _settling()
    due = _today() - dt.timedelta(days=10)
    _keep(bills, starts_on=due)
    sweep = SettleDueChargesUseCase(
        bills=bills,
        charges=charges,
        ledger=Vanishing(charges, _moved(day=due)),
        settle=settle,
    )

    view = _sweep(sweep)

    assert view.settled == ()
