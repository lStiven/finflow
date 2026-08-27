from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.exceptions import (
    CurrencyMismatchError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountKind,
    Balance,
    BalanceSign,
    InstrumentKind,
)
from personal_finance.shared.domain.value_objects import Currency, Money


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def test_what_you_owe_is_never_an_asset() -> None:
    assert AccountKind.SAVINGS.category is AccountCategory.ASSET
    assert AccountKind.CASH.category is AccountCategory.ASSET
    assert AccountKind.CREDIT_CARD.category is AccountCategory.LIABILITY
    assert AccountKind.MORTGAGE.category is AccountCategory.LIABILITY


def test_the_same_account_written_two_ways_fingerprints_the_same() -> None:
    from_template = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
    )
    from_llm = AccountFingerprint.from_parts(
        bank="  Bancolombia ",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653 ",
    )

    assert from_template == from_llm


def test_an_alert_spelling_an_instrument_its_own_way_still_reaches_the_key() -> None:
    """The deterministic parser and the model do not agree on casing, and the
    instrument is the one field that used to carry that difference into a key.
    """
    declared = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
    )

    for spelling in ("Credit_Card", "  credit_card ", "CREDIT_CARD"):
        assert (
            AccountFingerprint.from_alert(
                bank="bancolombia",
                instrument_kind=spelling,
                last_four="7653",
            )
            == declared
        )


def test_two_banks_reusing_four_digits_stay_two_accounts() -> None:
    bancolombia = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
    )
    nu = AccountFingerprint.from_parts(
        bank="nu",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
    )

    assert bancolombia != nu


def test_an_instrument_without_last_four_cannot_identify_an_account() -> None:
    with pytest.raises(ValueError):
        AccountFingerprint.from_parts(
            bank="bancolombia",
            instrument_kind=InstrumentKind.SAVINGS_ACCOUNT,
            last_four="",
        )


def test_a_balance_of_zero_has_no_direction() -> None:
    balance = Balance(amount=_cop("0"), sign=BalanceSign.NEGATIVE)

    assert balance.sign is BalanceSign.POSITIVE
    assert balance == Balance.zero(Currency.COP)


def test_a_balance_carries_its_sign_outside_the_amount() -> None:
    balance = Balance.zero(Currency.COP).minus(_cop("50000"))

    assert balance.is_negative
    assert balance.amount == _cop("50000")
    assert balance.signed_amount == Decimal("-50000")


def test_crossing_zero_flips_the_sign_without_losing_cents() -> None:
    balance = Balance.zero(Currency.COP).minus(_cop("50000.45")).plus(_cop("70000.05"))

    assert balance.signed_amount == Decimal("19999.60")
    assert not balance.is_negative


def test_a_balance_never_takes_an_amount_in_another_currency() -> None:
    balance = Balance.zero(Currency.COP)

    with pytest.raises(CurrencyMismatchError):
        balance.plus(Money(amount=Decimal("10"), currency=Currency.USD))


def test_one_card_printed_two_ways_still_fingerprints_once() -> None:
    # The template parser prints what the bank printed; the LLM fallback may
    # hand back more of the number than that.
    template = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
    )
    fallback = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="45127653",
    )

    assert template == fallback


def test_digits_that_are_not_ascii_are_refused() -> None:
    # `"\u0662".isdigit()` is True, and this text came from an email.
    with pytest.raises(ValueError):
        AccountFingerprint.from_parts(
            bank="bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="\u0667\u0666\u0665\u0663",
        )
