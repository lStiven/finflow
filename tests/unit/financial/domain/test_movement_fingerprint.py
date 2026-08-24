from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
    MovementFingerprint,
    MovementId,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
OTHER_USER = UserId.from_string("11111111-2222-3333-4444-555555555555")
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, 9, tzinfo=UTC))


def _fingerprint(**overrides: object) -> MovementFingerprint:
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "direction": MovementDirection.OUTGOING,
        "amount": Money(amount=Decimal("50000"), currency=Currency.COP),
        "occurred_at": PURCHASE_TIME,
        "counterparty": "TIENDAS ARA 123",
        "instrument_kind": "credit_card",
        "last_four": "7653",
    }
    parts.update(overrides)

    return MovementFingerprint.from_movement(**parts)  # type: ignore[arg-type]


def test_the_same_alert_delivered_twice_is_one_movement() -> None:
    assert _fingerprint() == _fingerprint()


def test_the_same_money_written_two_ways_is_one_movement() -> None:
    # A template parser prints what its bank printed; the LLM fallback may
    # hand back trailing zeros. Same money either way.
    assert _fingerprint(
        amount=Money(amount=Decimal("50000.00"), currency=Currency.COP),
    ) == _fingerprint(amount=Money(amount=Decimal("50000"), currency=Currency.COP))


def test_the_same_bank_written_two_ways_is_one_movement() -> None:
    assert _fingerprint(bank="  Bancolombia ") == _fingerprint(bank="bancolombia")


def test_the_same_counterparty_written_two_ways_is_one_movement() -> None:
    assert _fingerprint(counterparty="  Tiendas  Ara-123 ") == _fingerprint(
        counterparty="TIENDAS ARA 123",
    )


def test_accents_are_noise_a_bank_adds_inconsistently() -> None:
    assert _fingerprint(counterparty="ALMACEN ÉXITO") == _fingerprint(
        counterparty="almacen exito",
    )


def test_a_longer_card_number_still_names_the_same_card() -> None:
    # The LLM fallback can return more of the number than a template does.
    assert _fingerprint(last_four="4539871234567653") == _fingerprint(
        last_four="7653",
    )


def test_two_purchases_at_different_times_stay_two_movements() -> None:
    # A minute apart, not a second: neither extraction path resolves past
    # `HH:MM`, so a minute is the finest difference this key can actually
    # see. Two identical charges inside one minute reach Financial as the
    # same nine fields as one charge announced twice, and collapse into one
    # movement — a knowingly accepted loss, since idempotency under
    # at-least-once delivery is the requirement that has to hold.
    later = PosixTime.from_epoch_seconds(PURCHASE_TIME.as_epoch_seconds() + 60)

    assert _fingerprint(occurred_at=later) != _fingerprint()


def test_a_refund_is_not_the_purchase_it_reverses() -> None:
    assert _fingerprint(direction=MovementDirection.INCOMING) != _fingerprint(
        direction=MovementDirection.OUTGOING,
    )


def test_two_users_sharing_a_card_keep_separate_movements() -> None:
    assert _fingerprint(user_id=OTHER_USER) != _fingerprint(user_id=USER)


def test_two_banks_reusing_four_digits_stay_two_movements() -> None:
    assert _fingerprint(bank="nu") != _fingerprint(bank="bancolombia")


def test_two_cards_at_one_bank_stay_two_movements() -> None:
    assert _fingerprint(last_four="1234") != _fingerprint(last_four="7653")


def test_the_same_amount_in_another_currency_is_another_movement() -> None:
    assert _fingerprint(
        amount=Money(amount=Decimal("50000"), currency=Currency.USD),
    ) != _fingerprint(amount=Money(amount=Decimal("50000"), currency=Currency.COP))


def test_an_alert_with_no_instrument_still_identifies_its_movement() -> None:
    # It will not match an account, but it is kept unassigned rather than
    # dropped, and being kept means having an identity of its own.
    instrument_less = _fingerprint(instrument_kind=None, last_four=None)

    assert instrument_less == _fingerprint(instrument_kind=None, last_four=None)
    assert instrument_less != _fingerprint()
    assert instrument_less != _fingerprint(
        counterparty="OTRO COMERCIO",
        instrument_kind=None,
        last_four=None,
    )


def test_a_card_with_no_last_four_still_identifies_its_movement() -> None:
    no_digits = _fingerprint(last_four=None)

    assert no_digits == _fingerprint(last_four=None)
    assert no_digits != _fingerprint(last_four="7653")


def test_one_card_paying_two_ways_stays_two_movements() -> None:
    # A debit and a credit alert can share a bank and four digits; they are
    # different instruments and must not merge into one ledger row.
    assert _fingerprint(instrument_kind="debit_card") != _fingerprint(
        instrument_kind="credit_card",
    )


def test_missing_digits_are_absent_not_malformed() -> None:
    # The instrument arrives as JSON, where "no digits" and `""` say the same.
    assert _fingerprint(last_four="") == _fingerprint(last_four=None)
    assert _fingerprint(instrument_kind="  ") == _fingerprint(instrument_kind=None)


def test_an_instrument_cannot_spell_its_own_absence() -> None:
    # The sentinel must be something no real alert can write, or a crafted
    # instrument would share a fingerprint with an instrument-less movement.
    assert _fingerprint(instrument_kind="-") != _fingerprint(instrument_kind=None)


def test_a_crafted_separator_cannot_forge_another_movement() -> None:
    # `bank` is untrusted text and sits next to the instrument in the key.
    # Under a plain delimiter both of these canonicalize to `nu|ba|x`, and one
    # of the two movements would never reach the ledger.
    assert _fingerprint(bank="nu|ba", instrument_kind="x") != _fingerprint(
        bank="nu",
        instrument_kind="ba|x",
    )


def test_an_alert_that_names_no_bank_is_refused() -> None:
    with pytest.raises(ValueError):
        _fingerprint(bank="   ")


def test_an_alert_that_names_no_counterparty_is_refused() -> None:
    with pytest.raises(ValueError):
        _fingerprint(counterparty="   ")


def test_a_merchant_named_outside_the_latin_alphabet_still_normalizes() -> None:
    assert _fingerprint(counterparty="  Кофе-Хауз ") == _fingerprint(
        counterparty="Кофе Хауз",
    )


def test_a_fold_that_expands_into_letters_is_not_dropped_as_punctuation() -> None:
    assert _fingerprint(counterparty="STRAßE") == _fingerprint(counterparty="STRASSE")


def test_a_compatibility_character_folds_to_the_letters_it_decomposes_into() -> None:
    # NFKD turns these into ordinary cased letters (`№` -> `No`), so the case
    # fold has to come after it or the capital survives into the key.
    assert _fingerprint(counterparty="SUCURSAL № 12") == _fingerprint(
        counterparty="SUCURSAL No 12",
    )


def test_a_counterparty_of_pure_punctuation_keeps_its_own_identity() -> None:
    # It folds away to nothing, so the raw text stands in — otherwise every
    # such alert in one second would share a single fingerprint.
    assert _fingerprint(counterparty="***") != _fingerprint(counterparty="###")


def test_unusable_digits_cost_the_instrument_not_the_movement() -> None:
    # Ingestion gates `last_four` on bare `str.isdigit`, which lets Arabic-Indic
    # numerals through. Refusing the alert here would lose the money; the
    # movement is kept and simply arrives without an instrument.
    assert _fingerprint(last_four="٤٥٦٧") == _fingerprint(last_four=None)
    assert _fingerprint(last_four="76a3") == _fingerprint(last_four=None)


def test_a_fingerprint_carries_no_spending_history() -> None:
    # It reaches ledger keys and `AccountBalanceChanged`; a readable key would
    # carry an amount, a counterparty and card digits wherever ids are logged.
    value = _fingerprint().value

    assert "7653" not in value
    assert "50000" not in value
    assert "ara" not in value
    assert "bancolombia" not in value


def test_a_movement_is_identified_by_what_it_is() -> None:
    fingerprint = _fingerprint()

    assert MovementId.from_fingerprint(fingerprint) == MovementId.from_fingerprint(
        _fingerprint(),
    )
    assert MovementId.from_fingerprint(fingerprint).value == fingerprint.value


def test_a_stored_fingerprint_reads_back_but_an_empty_one_does_not() -> None:
    assert MovementFingerprint(value="abc").to_dict() == "abc"

    with pytest.raises(ValueError):
        MovementFingerprint(value="  ")


def test_padding_picked_up_in_storage_is_not_a_second_identity() -> None:
    # These are the ledger's keys: a value that gained a space on some
    # round-trip must still match its own row, or the movement re-applies.
    assert MovementFingerprint(value=" abc ") == MovementFingerprint(value="abc")
    assert MovementId(value=" abc ") == MovementId(value="abc")
