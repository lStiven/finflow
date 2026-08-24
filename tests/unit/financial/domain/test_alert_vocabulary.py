"""Reading ingestion's strings into Financial's own words.

The payload on the bus is JSON, so every one of these arrives as text.
Ingestion's enums are never imported: that is the whole point of the mapping,
since either context must be free to rename its own members.
"""

import pytest

from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountKind,
    MovementDirection,
)


def test_a_direction_is_read_however_the_alert_spelled_it() -> None:
    assert MovementDirection.from_alert("outgoing") is MovementDirection.OUTGOING
    assert MovementDirection.from_alert("  INCOMING ") is MovementDirection.INCOMING


def test_a_direction_that_cannot_be_read_refuses_the_alert() -> None:
    # The one field with no safe default: a guessed direction moves a real
    # balance the wrong way and looks correct doing it.
    with pytest.raises(ValueError):
        MovementDirection.from_alert("sideways")


def test_a_card_is_read_as_the_account_it_draws_on() -> None:
    assert AccountKind.from_instrument("credit_card") is AccountKind.CREDIT_CARD
    assert AccountKind.from_instrument("savings_account") is AccountKind.SAVINGS
    assert AccountKind.from_instrument("checking_account") is AccountKind.CHECKING
    assert AccountKind.from_instrument("  Debit_Card ") is AccountKind.SAVINGS


def test_an_instrument_reading_never_crosses_asset_and_liability() -> None:
    # The rule that makes the imprecision safe. A debit card may really sit on
    # a checking account rather than a savings one, and being wrong there
    # costs a rename. Reading a debit card as a credit one would turn money
    # held into money owed and invert net worth.
    liabilities = {
        instrument
        for instrument in (
            "credit_card",
            "debit_card",
            "savings_account",
            "checking_account",
            "account",
        )
        if AccountKind.from_instrument(instrument) is not None
        and AccountKind.from_instrument(instrument).category  # type: ignore[union-attr]
        is AccountCategory.LIABILITY
    }

    assert liabilities == {"credit_card"}


def test_an_instrument_nobody_recognises_concludes_nothing() -> None:
    # Not a default: opening an account for it would mean guessing whether it
    # holds money or owes it.
    assert AccountKind.from_instrument("prepaid_wallet") is None
    assert AccountKind.from_instrument("") is None
