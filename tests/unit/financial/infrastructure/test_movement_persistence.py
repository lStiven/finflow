"""What a movement survives being written down and read back as.

Storage is where a transfer quietly stops being one. The marker is a single
attribute on the row, and a movement that loses it does not fail — it comes
back as an ordinary expense whose balance is still right while every total
around it is wrong. So the round trip is asserted in both directions, and the
half-written shapes are asserted to be *refused* rather than repaired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    InstrumentKind,
    MovementDirection,
    TransactionOrigin,
    TransferRole,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    CorruptFinancialItemError,
    movement_to_entity,
    movement_to_item,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
PAID_AT = PosixTime.from_datetime(datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
ACCOUNT = AccountId.new()


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _lone_leg(role: TransferRole = TransferRole.DESTINATION) -> Transaction:
    return Transaction.enter_transfer_leg(
        user_id=USER,
        role=role,
        amount=_cop("3540258"),
        occurred_at=PAID_AT,
        counterparty="Nequi",
        account_id=ACCOUNT,
        bank="bancolombia",
    )


def _pair() -> tuple[Transaction, Transaction]:
    return Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("3540258"),
        occurred_at=PAID_AT,
        source_instrument_kind=InstrumentKind.ACCOUNT.value,
        source_last_four="5261",
        destination_instrument_kind=InstrumentKind.CREDIT_CARD.value,
        destination_last_four="7653",
    )


def _spending() -> Transaction:
    return Transaction.enter_manually(
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=_cop("45000"),
        occurred_at=PAID_AT,
        counterparty="TIENDAS ARA 123",
        account_id=ACCOUNT,
    )


def _round_trip(transaction: Transaction) -> Transaction:
    return movement_to_entity(movement_to_item(transaction))


# ------------------------------------------------------------- the round trip


def test_ordinary_spending_comes_back_with_no_transfer() -> None:
    assert _round_trip(_spending()).transfer is None


def test_a_lone_leg_comes_back_a_transfer() -> None:
    """The one that matters. Read back without this, the payment is an
    expense the size of the card's whole balance."""
    assert _round_trip(_lone_leg()).is_transfer is True


def test_a_lone_leg_comes_back_external() -> None:
    stored = _round_trip(_lone_leg())

    assert stored.transfer is not None
    assert stored.transfer.counterpart_is_external is True
    assert stored.transfer.counterpart_id is None


def test_a_lone_leg_keeps_its_transfer_id() -> None:
    leg = _lone_leg()
    stored = _round_trip(leg)

    assert leg.transfer is not None
    assert stored.transfer is not None
    assert stored.transfer.transfer_id == leg.transfer.transfer_id


def test_a_lone_leg_keeps_its_role_and_direction() -> None:
    """A source read back as a destination would move the balance the other
    way on the next rebuild."""
    stored = _round_trip(_lone_leg(TransferRole.SOURCE))

    assert stored.transfer is not None
    assert stored.transfer.role is TransferRole.SOURCE
    assert stored.direction is MovementDirection.OUTGOING


def test_a_lone_leg_keeps_the_money_to_the_cent() -> None:
    leg = _lone_leg()
    leg.edit(amount=_cop("540258.55"))

    assert _round_trip(leg).amount == _cop("540258.55")


def test_a_lone_leg_keeps_its_account_and_origin() -> None:
    stored = _round_trip(_lone_leg())

    assert stored.account_id == ACCOUNT
    assert stored.origin is TransactionOrigin.MANUAL


def test_a_lone_leg_keeps_what_the_owner_called_the_other_side() -> None:
    assert _round_trip(_lone_leg()).counterparty == "Nequi"


def test_a_paired_leg_still_names_its_counterpart() -> None:
    """The change that made the counterpart optional must not have made it
    absent: both sides of an alert-derived transfer still point at each
    other."""
    source, destination = _pair()
    stored = _round_trip(source)

    assert stored.transfer is not None
    assert stored.transfer.counterpart_is_external is False
    assert stored.transfer.counterpart_id == destination.id
    assert stored.transfer.counterpart_instrument_kind == "credit_card"
    assert stored.transfer.counterpart_last_four == "7653"


def test_both_sides_of_a_pair_still_share_one_transfer_id_through_storage() -> None:
    source, destination = _pair()

    stored_source = _round_trip(source)
    stored_destination = _round_trip(destination)

    assert stored_source.transfer is not None
    assert stored_destination.transfer is not None
    assert stored_source.transfer.transfer_id == stored_destination.transfer.transfer_id


# ------------------------------------------------------------ what is written


def _transfer_attributes(transaction: Transaction) -> dict[str, object]:
    stored = movement_to_item(transaction)["transfer"]

    return dict(stored["M"])  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_a_lone_leg_writes_no_counterpart_attributes_at_all() -> None:
    """Not an empty string. A stored `counterpart_id` of "" would read back as
    a link to a movement that cannot exist, and `TransferLeg` refuses it —
    turning a payment into an unreadable row on the next query."""
    written = _transfer_attributes(_lone_leg())

    assert set(written) == {"transfer_id", "role"}


def test_a_paired_leg_writes_the_whole_counterpart() -> None:
    source, _ = _pair()

    assert set(_transfer_attributes(source)) == {
        "transfer_id",
        "role",
        "counterpart_id",
        "counterpart_instrument_kind",
        "counterpart_last_four",
    }


def test_ordinary_spending_writes_no_transfer_attribute() -> None:
    assert "transfer" not in movement_to_item(_spending())


# ------------------------------------------------------ what is refused on read


def _stored_with(**transfer: object) -> dict[str, object]:
    """A movement item whose transfer map is whatever a test hands it."""
    item = movement_to_item(_lone_leg())
    item["transfer"] = {"M": {key: {"S": value} for key, value in transfer.items()}}

    return dict(item)


def test_a_leg_stored_without_its_transfer_id_is_refused() -> None:
    with pytest.raises(CorruptFinancialItemError, match="missing fields"):
        movement_to_entity(_stored_with(role="destination"))  # pyright: ignore[reportArgumentType]


def test_a_leg_stored_without_its_role_is_refused() -> None:
    with pytest.raises(CorruptFinancialItemError, match="missing fields"):
        movement_to_entity(_stored_with(transfer_id="abc"))  # pyright: ignore[reportArgumentType]


def test_a_leg_stored_with_half_a_counterpart_is_refused() -> None:
    """An id with nothing describing it. Refused rather than read as external:
    read that way it would claim there is no other side while a row pointing
    at one sits in the same table."""
    with pytest.raises(CorruptFinancialItemError, match="half a counterpart"):
        movement_to_entity(
            _stored_with(  # pyright: ignore[reportArgumentType]
                transfer_id="abc",
                role="destination",
                counterpart_id="def",
            ),
        )


def test_a_leg_stored_with_digits_but_no_counterpart_is_refused_too() -> None:
    with pytest.raises(CorruptFinancialItemError, match="half a counterpart"):
        movement_to_entity(
            _stored_with(  # pyright: ignore[reportArgumentType]
                transfer_id="abc",
                role="destination",
                counterpart_instrument_kind="credit_card",
                counterpart_last_four="7653",
            ),
        )


def test_a_leg_stored_with_an_unknown_role_is_refused() -> None:
    with pytest.raises(CorruptFinancialItemError):
        movement_to_entity(_stored_with(transfer_id="abc", role="sideways"))  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------- older rows


def test_a_row_written_before_this_change_still_reads_as_a_pair() -> None:
    """Every transfer stored until now named its counterpart in full. Nothing
    migrated them, so the reader has to keep understanding that shape."""
    stored = movement_to_entity(
        _stored_with(  # pyright: ignore[reportArgumentType]
            transfer_id="abc",
            role="source",
            counterpart_id="def",
            counterpart_instrument_kind="credit_card",
            counterpart_last_four="7653",
        ),
    )

    assert stored.transfer is not None
    assert stored.transfer.counterpart_is_external is False
    assert stored.has_counterpart_movement is True


def test_a_row_with_no_transfer_attribute_is_ordinary_spending() -> None:
    item = movement_to_item(_lone_leg())
    del item["transfer"]

    assert movement_to_entity(item).is_transfer is False
