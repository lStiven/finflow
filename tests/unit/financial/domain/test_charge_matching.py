"""Which movements can answer for a declared charge, and which never may.

The refusals carry most of the weight here. A candidate missed costs a
proposal nobody sees; a candidate wrongly taken as certain marks a charge paid
with somebody else's money, and the real charge then goes unnoticed — or, in
the other direction, the automatic charge writes a second row for money that
already left.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.domain.bills import (
    MATCH_WINDOW_DAYS,
    BillCadence,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.reconciliation import (
    MAX_CANDIDATES,
    ChargeCandidate,
    MatchQuality,
    bill_keys,
    candidates_for,
    only_certain,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.new()
PERIOD = dt.date(2026, 9, 4)
BOGOTA = dt.timezone(dt.timedelta(hours=-5))


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def bill(
    *,
    name: str = "Gimnasio",
    amount: str = "120000",
    direction: MovementDirection = MovementDirection.OUTGOING,
    account_id: AccountId | None = None,
) -> ScheduledBill:
    return ScheduledBill.declare(
        user_id=USER,
        name=name,
        amount=money(amount),
        cadence=BillCadence.MONTHLY,
        starts_on=PERIOD,
        direction=direction,
        account_id=account_id,
    )


def movement(
    *,
    counterparty: str = "Gimnasio",
    amount: str = "120000",
    currency: Currency = Currency.COP,
    day: dt.date = PERIOD,
    direction: MovementDirection = MovementDirection.OUTGOING,
    account_id: AccountId | None = None,
) -> Transaction:
    return Transaction.enter_manually(
        user_id=USER,
        direction=direction,
        amount=money(amount, currency),
        occurred_at=PosixTime.from_epoch_seconds(
            int(dt.datetime.combine(day, dt.time(hour=12), tzinfo=BOGOTA).timestamp()),
        ),
        counterparty=counterparty,
        account_id=account_id,
    )


def candidates(
    declared: ScheduledBill,
    *movements: Transaction,
    merchant_ids: dict[str, str] | None = None,
    taken: frozenset[str] = frozenset(),
    period: dt.date = PERIOD,
) -> tuple[ChargeCandidate, ...]:
    attributed = merchant_ids or {}

    return candidates_for(
        declared,
        period=period,
        movements=movements,
        keys=bill_keys(declared, attributed.get(declared.name)),
        merchant_ids=attributed,
        zone=BOGOTA,
        taken=taken,
    )


# ----------------------------------------------------------------------
# What counts as certain
# ----------------------------------------------------------------------


def test_the_same_name_for_the_same_money_on_the_day_is_certain() -> None:
    found = candidates(bill(), movement())

    assert [each.quality for each in found] == [MatchQuality.CERTAIN]


def test_the_merchant_is_recognised_through_its_spellings() -> None:
    """What the folded text cannot do: the gym arrives as `GYMSA*BOG` one
    month and `PAGO GYM SA` the next, and both are the same merchant."""
    found = candidates(
        bill(),
        movement(counterparty="GYMSA*BOG"),
        merchant_ids={"Gimnasio": "merchant-gym", "GYMSA*BOG": "merchant-gym"},
    )

    assert [each.quality for each in found] == [MatchQuality.CERTAIN]


def test_a_price_that_moved_a_little_is_still_certain() -> None:
    """The yearly rise, and the rounding a bank does on a foreign charge."""
    found = candidates(bill(amount="120000"), movement(amount="126000"))

    assert [each.quality for each in found] == [MatchQuality.CERTAIN]


def test_the_right_merchant_for_the_wrong_money_is_only_likely() -> None:
    """The electricity bill. Worth showing, never worth writing."""
    found = candidates(bill(amount="120000"), movement(amount="240000"))

    assert [each.quality for each in found] == [MatchQuality.LIKELY]


def test_the_right_money_under_a_name_nothing_attributes_is_only_likely() -> None:
    """The ordinary case for a bill somebody typed: «Gimnasio» against
    `PAGO PSE GYMSA`, with no merchant owning either spelling yet."""
    found = candidates(bill(), movement(counterparty="PAGO PSE GYMSA"))

    assert [each.quality for each in found] == [MatchQuality.LIKELY]


def test_a_charge_a_few_days_off_its_day_still_matches() -> None:
    found = candidates(
        bill(),
        movement(day=PERIOD + dt.timedelta(days=MATCH_WINDOW_DAYS)),
    )

    assert len(found) == 1


def test_a_charge_outside_the_window_is_not_a_candidate_at_all() -> None:
    found = candidates(
        bill(),
        movement(day=PERIOD + dt.timedelta(days=MATCH_WINDOW_DAYS + 1)),
    )

    assert found == ()


# ----------------------------------------------------------------------
# What can never be the charge
# ----------------------------------------------------------------------


def test_a_row_this_app_wrote_itself_is_never_evidence() -> None:
    """Otherwise the feature confirms itself: a charge it posted last month
    would be read as proof that the charge happened."""
    own = Transaction.confirm_scheduled(
        user_id=USER,
        bill_id=bill().id.value,
        period=PERIOD,
        direction=MovementDirection.OUTGOING,
        amount=money("120000"),
        occurred_at=PosixTime.from_epoch_seconds(
            int(dt.datetime.combine(PERIOD, dt.time(12), tzinfo=BOGOTA).timestamp()),
        ),
        counterparty="Gimnasio",
    )

    assert candidates(bill(), own) == ()


def test_a_transfer_between_your_own_accounts_is_not_a_paid_bill() -> None:
    leg = Transaction.enter_transfer_leg(
        user_id=USER,
        role=TransferRole.SOURCE,
        amount=money("120000"),
        occurred_at=PosixTime.from_epoch_seconds(
            int(dt.datetime.combine(PERIOD, dt.time(12), tzinfo=BOGOTA).timestamp()),
        ),
        counterparty="Gimnasio",
        account_id=AccountId.new(),
    )

    assert candidates(bill(), leg) == ()


def test_money_going_the_other_way_is_a_different_fact() -> None:
    """A salary arriving does not pay the gym."""
    found = candidates(bill(), movement(direction=MovementDirection.INCOMING))

    assert found == ()


def test_another_currency_is_never_the_same_charge() -> None:
    """40 dollars is not 40 000 pesos, whatever today's rate is."""
    found = candidates(bill(), movement(amount="120000", currency=Currency.USD))

    assert found == ()


def test_a_movement_on_another_account_is_refused_when_both_are_known() -> None:
    found = candidates(
        bill(account_id=AccountId.new()),
        movement(account_id=AccountId.new()),
    )

    assert found == ()


def test_a_movement_nobody_assigned_is_still_a_candidate() -> None:
    """Half this ledger is unassigned, because declaring accounts is
    optional. Refusing it would reconcile only for people who declared."""
    found = candidates(bill(account_id=AccountId.new()), movement())

    assert len(found) == 1


def test_a_movement_already_answering_for_something_is_not_offered_again() -> None:
    """One payment settles one thing: without this, one Netflix charge could
    pay January, February and March."""
    spoken_for = movement()

    assert candidates(bill(), spoken_for, taken=frozenset({spoken_for.id.value})) == ()


def test_a_period_this_bill_is_not_charged_on_has_no_candidates() -> None:
    found = candidates(bill(), movement(), period=dt.date(2026, 9, 5))

    assert found == ()


def test_a_paused_bill_matches_nothing() -> None:
    paused = bill()
    paused.pause()

    assert candidates(paused, movement()) == ()


# ----------------------------------------------------------------------
# What may be acted on
# ----------------------------------------------------------------------


def test_one_certain_candidate_is_the_answer() -> None:
    found = candidates(bill(), movement())

    assert only_certain(found) is not None


def test_two_certain_candidates_are_a_question_for_a_person() -> None:
    """Picking one would be guessing which of somebody's movements paid for
    what, and being wrong leaves a real charge filed as another."""
    found = candidates(
        bill(),
        movement(amount="120000", day=PERIOD),
        movement(amount="121000", day=PERIOD - dt.timedelta(days=1)),
    )

    assert len(found) == 2
    assert only_certain(found) is None


def test_a_likely_candidate_is_never_acted_on() -> None:
    found = candidates(bill(amount="120000"), movement(amount="240000"))

    assert only_certain(found) is None


def test_the_best_candidate_comes_first() -> None:
    far = movement(counterparty="Gimnasio", amount="240000")
    near = movement(counterparty="Gimnasio", amount="119000")

    found = candidates(bill(), far, near)

    assert [each.movement_id for each in found] == [near.id.value, far.id.value]


def test_a_charge_offers_a_bounded_number_of_answers() -> None:
    """A list longer than this is not a choice, it is a merchant somebody
    buys from all the time."""
    found = candidates(bill(), *(movement() for _ in range(MAX_CANDIDATES + 3)))

    assert len(found) == MAX_CANDIDATES


def test_a_movement_two_charges_both_reach_is_never_certain() -> None:
    """A weekly bill's charges are seven days apart and this window reaches
    five either side, so one movement sits inside two of them. Picking the
    nearer would leave the other to be charged automatically for money that
    has already left."""
    weekly = ScheduledBill.declare(
        user_id=USER,
        name="Gimnasio",
        amount=money("120000"),
        cadence=BillCadence.WEEKLY,
        starts_on=dt.date(2026, 9, 7),
    )
    between = movement(day=dt.date(2026, 9, 11))

    for period in (dt.date(2026, 9, 7), dt.date(2026, 9, 14)):
        found = candidates_for(
            weekly,
            period=period,
            movements=[between],
            keys=bill_keys(weekly, None),
            merchant_ids={},
            zone=BOGOTA,
            taken=frozenset(),
        )

        assert [each.quality for each in found] == [MatchQuality.LIKELY]
        assert only_certain(found) is None
