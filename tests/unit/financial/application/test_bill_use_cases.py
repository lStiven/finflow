"""Declaring bills, and the two numbers the month adds up to.

The domain already pins the calendar. What is pinned here is everything the
calendar cannot answer on its own: that a bill pointing at a closed account
reads as frozen without anything having written that down, that income is
declared but never netted against spending, and that the totals stay apart by
currency.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.bills import (
    AmendBillCommand,
    BillsView,
    DeclareBillCommand,
    ListBillsQuery,
    ListBillsUseCase,
    ManageBillsUseCase,
    NoSuchBillError,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    BillStatus,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
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
) -> tuple[ManageBillsUseCase, FakeBills, FakeAccounts]:
    bills = bills or FakeBills()
    accounts = accounts or FakeAccounts()

    return (
        ManageBillsUseCase(bills=bills, accounts=accounts),
        bills,
        accounts,
    )


def _view(
    bills: FakeBills,
    accounts: FakeAccounts | None = None,
    **query: object,
) -> BillsView:
    use_case = ListBillsUseCase(
        bills=bills,
        accounts=accounts or FakeAccounts(),
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
    # One charge already past, one still to come, inside the same window.
    use_case.declare(
        _declare(name="Arriendo", amount="900000", starts_on=dt.date(2026, 9, 1))
    )
    use_case.declare(
        _declare(name="Gimnasio", amount="120000", starts_on=dt.date(2026, 9, 28))
    )

    view = _view(bills, since=dt.date(2026, 9, 1), until=dt.date(2026, 9, 30))
    [total] = view.totals

    assert total.expected == Decimal("1020000")
    # Which of the two is upcoming depends on the day this runs, so what is
    # pinned is the relationship: upcoming is a subset, never more.
    assert total.upcoming <= total.expected


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
