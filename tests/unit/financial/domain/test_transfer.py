"""The two sides of money that never left the owner's own finances.

A card payment is the case: an account falls, a card's debt falls with it, and
net worth does not move. Every test here is about one of the three ways that
can silently go wrong — one side written and not the other, both sides written
in the same direction, or the pair written twice.

The last section covers the case where only one side is knowable at all: a
card paid from another bank, a wallet or cash. There the danger is the
opposite one — not a half-written pair, but a lone movement recorded as
ordinary spending or ordinary income when it is neither.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import TransferLegError
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    BalanceSign,
    InstrumentKind,
    MovementDirection,
    MovementId,
    TransactionOrigin,
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


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
OTHER_USER = UserId.from_string("11111111-2222-3333-4444-555555555555")
PAID_AT = PosixTime.from_datetime(datetime(2026, 5, 21, 21, 30, tzinfo=UTC))


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _pair(**overrides: object) -> tuple[Transaction, Transaction]:
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "amount": _cop("3540258"),
        "occurred_at": PAID_AT,
        "source_instrument_kind": InstrumentKind.ACCOUNT.value,
        "source_last_four": "5261",
        "destination_instrument_kind": InstrumentKind.CREDIT_CARD.value,
        "destination_last_four": "7653",
    }
    parts.update(overrides)

    return Transaction.as_transfer(**parts)  # pyright: ignore[reportArgumentType]


# ------------------------------------------------------------- the two sides


def test_the_sides_move_in_opposite_directions() -> None:
    source, destination = _pair()

    assert source.direction is MovementDirection.OUTGOING
    assert destination.direction is MovementDirection.INCOMING


def test_both_sides_carry_the_same_amount_and_instant() -> None:
    source, destination = _pair()

    assert source.amount == destination.amount
    assert source.occurred_at == destination.occurred_at


def test_both_sides_share_one_transfer_id_and_name_each_other() -> None:
    source, destination = _pair()

    assert source.transfer is not None
    assert destination.transfer is not None
    assert source.transfer.transfer_id == destination.transfer.transfer_id
    assert source.transfer.counterpart_id == destination.id
    assert destination.transfer.counterpart_id == source.id


def test_each_side_says_which_role_it_plays() -> None:
    source, destination = _pair()

    assert source.transfer is not None
    assert destination.transfer is not None
    assert source.transfer.role is TransferRole.SOURCE
    assert destination.transfer.role is TransferRole.DESTINATION


def test_each_side_names_the_other_instrument_so_a_client_need_not_parse_text() -> None:
    source, destination = _pair()

    assert source.transfer is not None
    assert destination.transfer is not None
    assert source.transfer.counterpart_last_four == "7653"
    assert destination.transfer.counterpart_last_four == "5261"


def test_the_sides_are_two_different_movements() -> None:
    """Same email, same amount, same minute — and two rows, because they land
    on two different balances."""
    source, destination = _pair()

    assert source.id != destination.id


def test_each_side_routes_to_its_own_instrument() -> None:
    source, destination = _pair()

    assert source.account_fingerprint is not None
    assert destination.account_fingerprint is not None
    assert source.account_fingerprint != destination.account_fingerprint


def test_both_sides_are_marked_as_transfer_legs() -> None:
    """The one question a spending total asks."""
    source, destination = _pair()

    assert source.is_transfer
    assert destination.is_transfer


def test_an_ordinary_alert_is_not_a_transfer_leg() -> None:
    movement = Transaction.from_alert(
        user_id=USER,
        bank="bancolombia",
        direction=MovementDirection.OUTGOING,
        amount=_cop("45000"),
        occurred_at=PAID_AT,
        counterparty="TIENDAS ARA",
        instrument_kind=InstrumentKind.CREDIT_CARD.value,
        last_four="7653",
    )

    assert not movement.is_transfer
    assert movement.transfer is None


# ------------------------------------------------------------- identity


def test_the_same_transfer_built_twice_is_the_same_two_rows() -> None:
    """What makes a redelivery safe: identity comes from content, so the
    second read writes over the same two keys rather than doubling them."""
    first_source, first_destination = _pair()
    second_source, second_destination = _pair()

    assert first_source.id == second_source.id
    assert first_destination.id == second_destination.id
    assert first_source.transfer is not None
    assert second_source.transfer is not None
    assert first_source.transfer.transfer_id == second_source.transfer.transfer_id


def test_two_payments_of_the_same_amount_from_different_accounts_stay_apart() -> None:
    source, _ = _pair()
    other_source, _ = _pair(source_last_four="9999")

    assert source.id != other_source.id


def test_two_people_paying_identical_cards_never_share_a_row() -> None:
    """The user is part of the key, not a filter applied after it."""
    source, _ = _pair()
    theirs, _ = _pair(user_id=OTHER_USER)

    assert source.id != theirs.id
    assert source.transfer is not None
    assert theirs.transfer is not None
    assert source.transfer.transfer_id != theirs.transfer.transfer_id


def test_an_instrument_paying_itself_is_refused() -> None:
    """Not a movement of money: a misread alert, and one that would put both
    sides on one balance."""
    with pytest.raises(ValueError, match="two different instruments"):
        _pair(
            destination_instrument_kind=InstrumentKind.ACCOUNT.value,
            destination_last_four="5261",
        )


def test_a_transfer_without_a_bank_is_refused() -> None:
    with pytest.raises(ValueError, match="requires a bank"):
        _pair(bank="   ")


def test_a_transfer_is_recorded_as_coming_from_a_bank_alert() -> None:
    source, destination = _pair()

    assert source.origin is TransactionOrigin.BANK_ALERT
    assert destination.origin is TransactionOrigin.BANK_ALERT


# ------------------------------------------------------------- balances


def _account(kind: AccountKind, last_four: str, holds: str) -> Account:
    """An account with a starting balance, declared the way its owner would."""
    account = Account.open(
        user_id=USER,
        name=kind.value,
        kind=kind,
        currency=Currency.COP,
        bank="bancolombia",
        instrument_kind=(
            InstrumentKind.CREDIT_CARD
            if kind is AccountKind.CREDIT_CARD
            else InstrumentKind.ACCOUNT
        ),
        last_four=last_four,
        opening_balance=_cop(holds),
        opened_at=PAID_AT,
    )
    account.pull_events()

    return account


def test_paying_a_card_lowers_the_account_and_the_debt_together() -> None:
    """The whole point. Applied to a liability, an *incoming* movement is the
    debt going down — the rule `Account.apply` already had."""
    savings = _account(AccountKind.SAVINGS, "5261", holds="5000000")
    card = _account(AccountKind.CREDIT_CARD, "7653", holds="3540258")

    source, destination = _pair()
    savings.apply(source.as_movement())
    card.apply(destination.as_movement())

    assert savings.balance.signed_amount == Decimal("1459742")
    assert card.balance.signed_amount == Decimal("0")
    assert card.balance.sign is BalanceSign.POSITIVE


def test_a_card_payment_leaves_net_worth_where_it_was() -> None:
    """Nothing was spent and nothing was earned: assets fell by exactly what
    liabilities fell by."""
    savings = _account(AccountKind.SAVINGS, "5261", holds="5000000")
    card = _account(AccountKind.CREDIT_CARD, "7653", holds="3540258")

    before = savings.balance.signed_amount - card.balance.signed_amount

    source, destination = _pair()
    savings.apply(source.as_movement())
    card.apply(destination.as_movement())

    after = savings.balance.signed_amount - card.balance.signed_amount
    assert after == before


# ------------------------------------------------------------- corrections


def test_one_side_of_a_transfer_cannot_be_corrected_on_its_own() -> None:
    """Refused rather than half-applied: correcting one side would leave the
    pair describing two different movements and two balances that no longer
    reconcile."""
    source, _ = _pair()

    with pytest.raises(TransferLegError):
        source.edit(amount=_cop("100"))


def test_moving_a_transfer_leg_in_time_is_refused_too() -> None:
    source, _ = _pair()

    with pytest.raises(TransferLegError):
        source.edit(occurred_at=PosixTime.from_epoch_seconds(1_700_000_000))


def test_a_note_can_still_be_written_on_a_transfer_leg() -> None:
    """An annotation is not a claim about the movement, so it stays allowed —
    it is how somebody records why they paid the card early."""
    source, _ = _pair()

    source.edit(note="pago anticipado")

    assert source.note == "pago anticipado"


# ------------------------------------------- a leg whose other side is outside


# Fixed, not fresh per call: the identity of a leg is derived from its
# content, and an account that changed every time would make two legs
# "entered alike" differ for a reason no test meant to state.
LEG_ACCOUNT = AccountId.new()
OTHER_ACCOUNT = AccountId.new()


def _leg(**overrides: object) -> Transaction:
    """A card paid from somewhere this app does not hold: Nequi, cash, PSE."""
    parts: dict[str, object] = {
        "user_id": USER,
        "role": TransferRole.DESTINATION,
        "amount": _cop("3540258"),
        "occurred_at": PAID_AT,
        "counterparty": "Nequi",
        "account_id": LEG_ACCOUNT,
        "bank": "bancolombia",
    }
    parts.update(overrides)

    return Transaction.enter_transfer_leg(**parts)  # pyright: ignore[reportArgumentType]


def test_the_destination_leg_faces_incoming() -> None:
    """Money paid *to* the card arrives on it, and on a liability arriving is
    the debt going down."""
    assert _leg(role=TransferRole.DESTINATION).direction is MovementDirection.INCOMING


def test_the_source_leg_faces_outgoing() -> None:
    assert _leg(role=TransferRole.SOURCE).direction is MovementDirection.OUTGOING


def test_the_role_alone_decides_the_direction() -> None:
    """There is no way to ask for a source that faces incoming: the pairing of
    the two is what would record a payment that raises what is owed."""
    source = _leg(role=TransferRole.SOURCE)
    destination = _leg(role=TransferRole.DESTINATION)

    assert source.direction is not destination.direction


def test_it_is_a_transfer_so_no_total_counts_it() -> None:
    """The whole reason this exists. `is_transfer` is what every total filters
    on, and a lone leg answers it exactly like a paired one."""
    assert _leg().is_transfer is True


def test_its_counterpart_is_external() -> None:
    leg = _leg()

    assert leg.transfer is not None
    assert leg.transfer.counterpart_is_external is True
    assert leg.transfer.counterpart_id is None


def test_it_has_no_counterpart_movement_to_point_a_reader_at() -> None:
    assert _leg().has_counterpart_movement is False


def test_a_paired_leg_does_have_one() -> None:
    """The property separates the two cases, which is what lets corrections be
    refused on one and allowed on the other."""
    source, _ = _pair()

    assert source.has_counterpart_movement is True


def test_two_legs_entered_alike_are_one_and_the_same_row() -> None:
    """The double submit. Two identical payments to one card in one minute
    are one payment typed twice, and counted as two the debt falls twice —
    a wrong balance nobody would see."""
    first = _leg()
    second = _leg()

    assert first.id == second.id
    assert first.transfer is not None
    assert second.transfer is not None
    assert first.transfer.transfer_id == second.transfer.transfer_id


def test_a_note_or_a_bank_does_not_make_it_a_different_payment() -> None:
    """Neither says which money moved, so neither may split one payment in
    two — a second attempt that added a note would otherwise write a row."""
    assert _leg().id == _leg(note="pago anticipado", bank="").id


def test_two_cards_paid_from_one_wallet_stay_apart() -> None:
    """The account stands where an alert's instrument would. Without it these
    two would collapse into one row and only one debt would fall."""
    assert _leg(account_id=LEG_ACCOUNT).id != _leg(account_id=OTHER_ACCOUNT).id


@pytest.mark.parametrize(
    "difference",
    [
        {"amount": _cop("100000")},
        {"occurred_at": PosixTime.from_epoch_seconds(1_700_000_000)},
        {"role": TransferRole.SOURCE},
        {"counterparty": "Daviplata"},
    ],
    ids=["amount", "instant", "role", "counterparty"],
)
def test_anything_that_says_which_money_moved_makes_it_another_payment(
    difference: dict[str, object],
) -> None:
    assert _leg().id != _leg(**difference).id


def test_two_people_entering_the_same_payment_never_share_a_row() -> None:
    assert _leg().id != _leg(user_id=OTHER_USER).id


def test_its_transfer_id_can_never_collide_with_an_alert_derived_one() -> None:
    """Both are sha256 now, so length no longer separates them — the tag in
    each canonical form does, and that is what this pins."""
    lone = _leg()
    source, _ = _pair()

    assert lone.transfer is not None
    assert source.transfer is not None
    assert lone.transfer.transfer_id != source.transfer.transfer_id


def test_a_lone_transfer_id_is_not_its_own_movement_id() -> None:
    """Hashed again rather than reused, so a transfer and a movement never
    answer to one id."""
    leg = _leg()

    assert leg.transfer is not None
    assert leg.transfer.transfer_id.value != leg.id.value


def test_it_is_recorded_as_the_users_own_claim() -> None:
    assert _leg().origin is TransactionOrigin.MANUAL


def test_it_names_no_instrument_so_nothing_adopts_it_by_matching() -> None:
    """It carries no account fingerprint: an account declared later must never
    silently claim a movement whose account its owner already chose."""
    assert _leg().is_routable is False


def test_it_keeps_the_account_its_owner_named() -> None:
    account = AccountId.new()

    assert _leg(account_id=account).account_id == account


def test_it_shows_what_the_owner_called_the_other_side() -> None:
    """The label lives in the counterparty, where every reader already looks —
    there is no instrument to name instead."""
    assert _leg(counterparty="Nequi").counterparty == "Nequi"


def test_a_leg_with_no_counterparty_text_is_refused() -> None:
    with pytest.raises(ValueError, match="counterparty"):
        _leg(counterparty="   ")


# ------------------------------------------------------- what it does to money


def test_paying_a_card_from_outside_lowers_only_the_debt() -> None:
    """The other balance is not here to move, and that is the correct answer:
    the money came from somewhere this app does not track."""
    card = _account(AccountKind.CREDIT_CARD, "7653", holds="3540258")

    card.apply(_leg(role=TransferRole.DESTINATION).as_movement())

    assert card.balance.signed_amount == Decimal("0")
    assert card.balance.sign is BalanceSign.POSITIVE


def test_paying_a_card_elsewhere_from_a_tracked_account_lowers_only_it() -> None:
    """The mirror case: the account is here and the card is at another bank."""
    savings = _account(AccountKind.SAVINGS, "5261", holds="5000000")

    savings.apply(_leg(role=TransferRole.SOURCE, amount=_cop("1000000")).as_movement())

    assert savings.balance.signed_amount == Decimal("4000000")


def test_the_leg_moves_the_balance_by_exactly_its_amount() -> None:
    """No rounding, no sign flip: the figure the owner typed is the figure the
    debt falls by."""
    card = _account(AccountKind.CREDIT_CARD, "7653", holds="3540258")

    card.apply(_leg(amount=_cop("540258.55")).as_movement())

    assert card.balance.signed_amount == Decimal("2999999.45")


# ----------------------------------------------------------- its corrections


def test_a_lone_leg_can_be_corrected() -> None:
    """Allowed where a paired leg is refused, and for a reason that is not a
    preference: there is no second row to fall out of step with."""
    leg = _leg()

    leg.edit(amount=_cop("100000"))

    assert leg.amount == _cop("100000")


def test_correcting_a_lone_leg_keeps_it_out_of_spending() -> None:
    """A correction must not quietly turn a payment back into an expense."""
    leg = _leg()

    leg.edit(amount=_cop("100000"), counterparty="Daviplata")

    assert leg.is_transfer is True
    assert leg.transfer is not None
    assert leg.transfer.counterpart_is_external is True


def test_correcting_a_lone_leg_keeps_no_stated_movement() -> None:
    """`stated` preserves what the *bank* said, so a correction can be told
    apart from a misparse. Nobody stated this one but its owner, and keeping a
    copy of their own earlier typo would claim a source that never existed."""
    leg = _leg()

    leg.edit(amount=_cop("100000"))

    assert leg.stated is None


# ------------------------------------------------- the shape of a leg itself


def test_a_leg_describing_half_its_counterpart_is_refused() -> None:
    """The state this class exists to make impossible: an id nothing can
    resolve, or digits belonging to no movement."""
    with pytest.raises(ValueError, match="fully or not at all"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.SOURCE,
            counterpart_id=MovementId.new(),
        )


def test_a_leg_naming_digits_but_no_movement_is_refused_too() -> None:
    with pytest.raises(ValueError, match="fully or not at all"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.SOURCE,
            counterpart_instrument_kind="credit_card",
            counterpart_last_four="7653",
        )


def test_a_fully_described_counterpart_is_accepted() -> None:
    leg = TransferLeg(
        transfer_id=TransferId(value="a-transfer"),
        role=TransferRole.SOURCE,
        counterpart_id=MovementId.new(),
        counterpart_instrument_kind="credit_card",
        counterpart_last_four="7653",
    )

    assert leg.counterpart_is_external is False


def test_a_counterpart_named_by_blank_instrument_is_refused() -> None:
    with pytest.raises(ValueError, match="instrument"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.SOURCE,
            counterpart_id=MovementId.new(),
            counterpart_instrument_kind="   ",
            counterpart_last_four="7653",
        )


def test_a_lone_leg_cannot_be_left_without_an_account() -> None:
    """It states that a balance moved, and it names no instrument, so nothing
    would ever adopt it — detached it would say a payment happened while no
    balance shows one."""
    leg = _leg()

    with pytest.raises(TransferLegError):
        leg.detach()

    assert leg.account_id is not None


def test_a_lone_leg_can_still_be_moved_to_another_account() -> None:
    """Which is what a leg entered against the wrong card actually needs."""
    leg = _leg()
    elsewhere = AccountId.new()

    leg.unassign()
    leg.assign_to(elsewhere)

    assert leg.account_id == elsewhere


def test_an_ordinary_movement_can_still_be_detached() -> None:
    """The refusal is about transfer legs, not about detaching."""
    movement = Transaction.enter_manually(
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=_cop("45000"),
        occurred_at=PAID_AT,
        counterparty="TIENDAS ARA",
        account_id=AccountId.new(),
    )

    movement.detach()

    assert movement.account_id is None


def test_a_paired_leg_can_still_be_detached() -> None:
    """It names an instrument, so declaring that account adopts it back — the
    retroactive path every unassigned alert already takes."""
    source, _ = _pair()
    source.assign_to(AccountId.new())

    source.detach()

    assert source.account_id is None
