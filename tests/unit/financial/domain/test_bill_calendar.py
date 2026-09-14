"""The calendar a declared bill follows, and what it refuses to say.

Most of this file is about dates, because dates are where this goes wrong: a
bill anchored at the end of a month, a leap day, a charge that lands on a
Saturday. The rest is about the one rule the whole feature rests on — nothing
here writes anything, and a paused bill predicts nothing.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.bills import (
    GRACE_DAYS,
    MAX_NAME_LENGTH,
    MAX_WINDOW_DAYS,
    BillCadence,
    BillStatus,
    OccurrenceState,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


USER = UserId.new()


def _money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _bill(
    *,
    cadence: BillCadence = BillCadence.MONTHLY,
    starts_on: dt.date = dt.date(2026, 9, 4),
    amount: str = "120000",
    name: str = "Gimnasio",
    **rest: object,
) -> ScheduledBill:
    return ScheduledBill.declare(
        user_id=USER,
        name=name,
        amount=_money(amount),
        cadence=cadence,
        starts_on=starts_on,
        **rest,  # type: ignore[arg-type]
    )


# ----------------------------------------------------------------------
# Declaring
# ----------------------------------------------------------------------


def test_a_declared_bill_publishes_nothing() -> None:
    """What somebody plans to be charged for is a statement about their life,
    not about money that moved. The fact worth publishing is the charge."""
    assert _bill().pull_events() == []


def test_a_bill_starts_active_and_without_an_account() -> None:
    bill = _bill()

    assert bill.status is BillStatus.ACTIVE
    assert bill.account_id is None
    assert bill.direction is MovementDirection.OUTGOING


def test_a_bill_for_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be for nothing"):
        _bill(amount="0")


def test_a_bill_without_a_name_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a name"):
        _bill(name="   ")


def test_a_name_is_collapsed_rather_than_kept_as_typed() -> None:
    assert _bill(name="  Gimnasio   Bogotá  ").name == "Gimnasio Bogotá"


# ----------------------------------------------------------------------
# The calendar
# ----------------------------------------------------------------------


def test_a_monthly_bill_lands_on_the_same_day_every_month() -> None:
    occurrences = _bill().occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 12, 31),
        today=dt.date(2026, 9, 14),
    )

    assert [o.due_on for o in occurrences] == [
        dt.date(2026, 9, 4),
        dt.date(2026, 10, 4),
        dt.date(2026, 11, 4),
        dt.date(2026, 12, 4),
    ]


def test_a_bill_on_the_31st_borrows_february_and_gets_march_back() -> None:
    """The bug this rule exists for: stepping from where the charge *landed*
    would leave a 31st bill stuck on the 28th from February onwards."""
    occurrences = _bill(starts_on=dt.date(2026, 1, 31)).occurrences(
        since=dt.date(2026, 1, 1),
        until=dt.date(2026, 4, 30),
        today=dt.date(2026, 1, 1),
    )

    assert [o.due_on for o in occurrences] == [
        dt.date(2026, 1, 31),
        dt.date(2026, 2, 28),
        dt.date(2026, 3, 31),
        dt.date(2026, 4, 30),
    ]


def test_an_annual_bill_on_a_leap_day_clamps_and_comes_back() -> None:
    occurrences = _bill(
        cadence=BillCadence.ANNUAL,
        starts_on=dt.date(2028, 2, 29),
    ).occurrences(
        since=dt.date(2029, 1, 1),
        until=dt.date(2029, 12, 31),
        today=dt.date(2029, 1, 1),
    )

    assert [o.due_on for o in occurrences] == [dt.date(2029, 2, 28)]


@pytest.mark.parametrize(
    ("cadence", "second"),
    [
        (BillCadence.WEEKLY, dt.date(2026, 9, 11)),
        (BillCadence.BIWEEKLY, dt.date(2026, 9, 18)),
        (BillCadence.MONTHLY, dt.date(2026, 10, 4)),
        (BillCadence.BIMONTHLY, dt.date(2026, 11, 4)),
        (BillCadence.QUARTERLY, dt.date(2026, 12, 4)),
    ],
)
def test_every_cadence_knows_what_comes_after_the_first_charge(
    cadence: BillCadence,
    second: dt.date,
) -> None:
    occurrences = _bill(cadence=cadence).occurrences(
        since=dt.date(2026, 9, 4),
        until=second,
        today=dt.date(2026, 9, 4),
    )

    assert occurrences[1].due_on == second


def test_charges_before_the_window_are_walked_over_but_not_returned() -> None:
    """The walk starts at the anchor, not at `since` — that is what keeps the
    day of the month right — so everything earlier has to be dropped."""
    occurrences = _bill(starts_on=dt.date(2026, 1, 4)).occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 14),
    )

    assert [o.due_on for o in occurrences] == [dt.date(2026, 9, 4)]


def test_a_bill_that_has_not_started_yet_shows_nothing_of_this_month() -> None:
    assert (
        _bill(starts_on=dt.date(2027, 3, 4)).occurrences(
            since=dt.date(2026, 9, 1),
            until=dt.date(2026, 9, 30),
            today=dt.date(2026, 9, 14),
        )
        == ()
    )


def test_an_inverted_window_is_empty_rather_than_an_error() -> None:
    assert (
        _bill().occurrences(
            since=dt.date(2026, 9, 30),
            until=dt.date(2026, 9, 1),
            today=dt.date(2026, 9, 14),
        )
        == ()
    )


def test_a_window_nobody_should_ask_for_is_refused() -> None:
    since = dt.date(2026, 1, 1)

    with pytest.raises(ValueError, match="wider than"):
        _bill(cadence=BillCadence.WEEKLY).occurrences(
            since=since,
            until=since + dt.timedelta(days=MAX_WINDOW_DAYS + 1),
            today=since,
        )


# ----------------------------------------------------------------------
# What can be said about a charge
# ----------------------------------------------------------------------


def test_a_charge_still_inside_its_grace_is_not_late() -> None:
    """The case this grace exists for: the 4th fell on a Saturday and the bank
    took it on Monday."""
    [occurrence] = _bill().occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 4) + dt.timedelta(days=GRACE_DAYS),
    )

    assert occurrence.state is OccurrenceState.EXPECTED


def test_a_charge_past_its_grace_is_overdue() -> None:
    [occurrence] = _bill().occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 4) + dt.timedelta(days=GRACE_DAYS + 1),
    )

    assert occurrence.state is OccurrenceState.OVERDUE


def test_a_charge_still_to_come_is_expected() -> None:
    [occurrence] = _bill().occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 1),
    )

    assert occurrence.state is OccurrenceState.EXPECTED


# ----------------------------------------------------------------------
# Pausing and amending
# ----------------------------------------------------------------------


def test_a_paused_bill_predicts_nothing() -> None:
    """A pause that still filled the month's total with charges nobody expects
    would make that total the one number in the app nobody can trust."""
    bill = _bill()
    bill.pause()

    assert (
        bill.occurrences(
            since=dt.date(2026, 9, 1),
            until=dt.date(2026, 12, 31),
            today=dt.date(2026, 9, 14),
        )
        == ()
    )


def test_resuming_brings_the_calendar_back() -> None:
    bill = _bill()
    bill.pause()
    bill.resume()

    assert bill.occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 14),
    )


def test_amending_the_amount_changes_what_the_next_charges_say() -> None:
    bill = _bill()
    bill.amend(amount=_money("135000"))

    [occurrence] = bill.occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 1),
    )

    assert occurrence.amount == _money("135000")


def test_amending_leaves_alone_what_it_was_not_given() -> None:
    bill = _bill()
    bill.amend(name="Gym")

    assert bill.name == "Gym"
    assert bill.amount == _money("120000")
    assert bill.cadence is BillCadence.MONTHLY


def test_an_account_can_be_moved_and_taken_away() -> None:
    """`None` already means "leave it alone", so saying "this comes out of no
    account of mine" needs a flag of its own."""
    account = AccountId.new()
    bill = _bill()

    bill.amend(account_id=account)
    assert bill.account_id == account

    bill.amend(clear_account=True)
    assert bill.account_id is None


def test_amending_still_refuses_what_declaring_refuses() -> None:
    bill = _bill()

    with pytest.raises(ValueError, match="cannot be for nothing"):
        bill.amend(amount=_money("0"))


def test_a_name_too_long_is_refused_rather_than_cut() -> None:
    """It becomes the counterparty of a real movement later: half a sentence
    with nothing saying why would be worse than a refusal now."""
    with pytest.raises(ValueError, match="cannot exceed"):
        _bill(name="G" * (MAX_NAME_LENGTH + 1))


def test_the_direction_can_be_corrected() -> None:
    """A salary declared as an expense inflates the month for as long as it
    stands, and re-declaring it would lose what it has paid."""
    bill = _bill()
    bill.amend(direction=MovementDirection.INCOMING)

    [occurrence] = bill.occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 1),
    )

    assert occurrence.direction is MovementDirection.INCOMING


def test_a_window_costs_the_window_and_not_the_history_behind_it() -> None:
    """A weekly bill anchored in the year 202 — a plausible typo — used to
    cost ninety-odd thousand steps on a request asking about one month."""
    bill = _bill(cadence=BillCadence.WEEKLY, starts_on=dt.date(202, 1, 4))

    occurrences = bill.occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2026, 9, 30),
        today=dt.date(2026, 9, 14),
    )

    assert 4 <= len(occurrences) <= 5
    assert all(
        dt.date(2026, 9, 1) <= o.due_on <= dt.date(2026, 9, 30) for o in occurrences
    )


@pytest.mark.parametrize(
    "cadence",
    list(BillCadence),
)
def test_jumping_into_a_window_lands_where_stepping_would_have(
    cadence: BillCadence,
) -> None:
    """The arithmetic shortcut and the calendar have to agree, or a bill's day
    would depend on how far away the window was asked for."""
    anchor = dt.date(2020, 1, 31)
    bill = _bill(cadence=cadence, starts_on=anchor)

    stepped = anchor
    while stepped < dt.date(2026, 9, 1):
        stepped = cadence.next_after(stepped, anchor=anchor)

    jumped = bill.occurrences(
        since=dt.date(2026, 9, 1),
        until=dt.date(2027, 9, 1),
        today=dt.date(2026, 9, 1),
    )

    assert jumped[0].due_on == stepped
