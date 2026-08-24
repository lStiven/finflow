"""What Financial accepts off the bus, and what it refuses."""

from decimal import Decimal

from pydantic import ValidationError
import pytest

from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.contexts.financial.infrastructure.messaging.inbound import (
    TransactionExtractedDetail,
    UnsupportedPayloadVersionError,
)
from personal_finance.shared.domain.value_objects import Currency


USER_ID = "6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10"
EVENT_ID = "3f1b7c2e-1111-4d63-9c2e-9d3b1f7c5a10"


def _payload(**overrides: object) -> dict[str, object]:
    transaction: dict[str, object] = {
        "kind": "card_purchase",
        "direction": "outgoing",
        "amount": "50000.50",
        "currency": "COP",
        "occurred_at": 1787846700,
        "counterparty": "TIENDAS ARA 123",
        "bank": "bancolombia",
        "instrument": {"kind": "credit_card", "last_four": "7653"},
    }
    transaction.update(overrides)

    return {
        "version": 1,
        "event_id": EVENT_ID,
        "user_id": USER_ID,
        "transaction": transaction,
    }


def test_a_published_movement_reads_into_financials_own_words() -> None:
    command = TransactionExtractedDetail.model_validate(_payload()).to_command()

    assert command.direction is MovementDirection.OUTGOING
    assert command.amount.currency is Currency.COP
    assert command.bank == "bancolombia"
    assert command.counterparty == "TIENDAS ARA 123"
    assert command.instrument_kind == "credit_card"
    assert command.last_four == "7653"


def test_the_cents_survive_the_bus() -> None:
    # The payload carries the amount as a string precisely so this holds; a
    # JSON float would have rounded it before Financial ever saw it.
    command = TransactionExtractedDetail.model_validate(
        _payload(amount="50000.50"),
    ).to_command()

    assert command.amount.amount == Decimal("50000.50")


def test_a_direction_this_cannot_read_refuses_the_movement() -> None:
    with pytest.raises(ValueError):
        TransactionExtractedDetail.model_validate(
            _payload(direction="sideways"),
        ).to_command()


def test_a_currency_this_cannot_read_refuses_the_movement() -> None:
    # Guessing would mix two currencies into one number.
    with pytest.raises(ValueError):
        TransactionExtractedDetail.model_validate(
            _payload(currency="XYZ"),
        ).to_command()


@pytest.mark.parametrize(
    "amount",
    ["-50000", "NaN", "Infinity", "cincuenta mil", "1E+1000000", "1_000"],
)
def test_an_amount_that_is_not_a_usable_quantity_is_refused(amount: str) -> None:
    # `Money` is unsigned and finite, and a NaN would compare false against
    # every guard downstream instead of raising. `1E+1000000` is the one worth
    # naming: ten harmless characters that expand into a million-digit amount
    # and raise `Overflow` — an `ArithmeticError` no caller guards for.
    with pytest.raises(ValidationError):
        TransactionExtractedDetail.model_validate(_payload(amount=amount))


@pytest.mark.parametrize("occurred_at", [-1, 10**18, 253_402_300_800])
def test_a_timestamp_outside_what_a_clock_can_hold_is_refused(
    occurred_at: int,
) -> None:
    # Unbounded, these raise `OSError` or an out-of-range `ValueError` from
    # inside `datetime` — past the point where a worker can call the payload
    # malformed, so the message would redeliver until the dead-letter queue.
    with pytest.raises(ValidationError):
        TransactionExtractedDetail.model_validate(_payload(occurred_at=occurred_at))


def test_a_version_this_cannot_read_is_refused_rather_than_assumed() -> None:
    # Not discarded either: a newer deploy wrote it, and a newer worker may
    # still take it. Reading it as v1 would book whatever v2 changed.
    payload = _payload()
    payload["version"] = 2

    with pytest.raises(UnsupportedPayloadVersionError):
        TransactionExtractedDetail.model_validate(payload).to_command()


def test_a_field_of_spaces_is_refused_at_the_boundary_not_in_the_domain() -> None:
    # `min_length=1` accepts these; only stripping catches them, and catching
    # them here is what lets a worker call the payload malformed and drop it.
    for blank in ("bank", "counterparty"):
        with pytest.raises(ValidationError):
            TransactionExtractedDetail.model_validate(_payload(**{blank: "   "}))


def test_an_alert_with_no_instrument_still_reads() -> None:
    # It will be unassigned, which is a decision the domain makes — not a
    # reason to refuse the payload.
    command = TransactionExtractedDetail.model_validate(
        _payload(instrument=None),
    ).to_command()

    assert command.instrument_kind is None
    assert command.last_four is None


def test_an_instrument_without_digits_still_reads() -> None:
    command = TransactionExtractedDetail.model_validate(
        _payload(instrument={"kind": "credit_card", "last_four": None}),
    ).to_command()

    assert command.instrument_kind == "credit_card"
    assert command.last_four is None


def test_a_payload_missing_what_identifies_the_movement_is_refused() -> None:
    for missing in ("bank", "counterparty"):
        with pytest.raises(ValidationError):
            TransactionExtractedDetail.model_validate(_payload(**{missing: ""}))


def test_an_unreadable_envelope_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(ValidationError):
        TransactionExtractedDetail.model_validate({"version": 1})
