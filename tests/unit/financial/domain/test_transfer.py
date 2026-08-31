"""The two sides of money that never left the owner's own finances.

A card payment is the case: an account falls, a card's debt falls with it, and
net worth does not move. Every test here is about one of the three ways that
can silently go wrong — one side written and not the other, both sides written
in the same direction, or the pair written twice.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import TransferLegError
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    BalanceSign,
    InstrumentKind,
    MovementDirection,
    TransactionOrigin,
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
