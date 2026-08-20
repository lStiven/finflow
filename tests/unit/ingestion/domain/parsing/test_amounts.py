from decimal import Decimal

import pytest

from personal_finance.contexts.ingestion.domain.parsing.amounts import (
    AmountParseError,
    parse_amount,
)
from personal_finance.shared.domain.value_objects import Currency


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Both formats below come from the same Bancolombia alert stream. A
        # single locale assumption gets one of the two wrong by a factor of a
        # thousand, which is the whole reason this parser is positional.
        ("COP29.259,00", Decimal("29259.00")),
        ("COP37.680,00", Decimal("37680.00")),
        ("$13,600.00", Decimal("13600.00")),
        ("$112,700.00", Decimal("112700.00")),
        ("$19,850,806.00", Decimal("19850806.00")),
        # Defensive, not observed: every real Bancolombia alert writes cents.
        # If one ever omits them, a grouped number must read as thousands —
        # nineteen thousand, never nineteen.
        ("$19,850", Decimal("19850")),
        ("COP1.234.567,89", Decimal("1234567.89")),
        ("$1.234.567,89", Decimal("1234567.89")),
        ("COP 500", Decimal("500")),
        ("$0.00", Decimal("0.00")),
    ],
    ids=[
        "european-with-cents",
        "european-with-cents-2",
        "us-with-cents",
        "us-with-cents-2",
        "us-two-groups",
        "us-grouped-no-cents",
        "european-millions",
        "dollar-marker-european-digits",
        "spaced-marker",
        "zero",
    ],
)
def test_amounts_from_real_alerts(raw: str, expected: Decimal) -> None:
    assert parse_amount(raw).amount == expected


def test_peso_marker_and_dollar_sign_both_mean_pesos() -> None:
    # A Colombian bank writing "$" means pesos; no sample distinguishes it.
    assert parse_amount("$13,600.00").currency is Currency.COP
    assert parse_amount("COP29.259,00").currency is Currency.COP


def test_explicit_usd_marker_is_honoured() -> None:
    assert parse_amount("USD 25.00").currency is Currency.USD


@pytest.mark.parametrize(
    "raw",
    ["", "29.259,00", "COP", "COP abc", "COP12.34.56", "$1,2345"],
    ids=["empty", "no-marker", "no-digits", "letters", "bad-groups", "bad-group-size"],
)
def test_unreadable_amounts_are_rejected(raw: str) -> None:
    # Refusing is the correct outcome: a wrong amount is worse than a miss,
    # because a miss falls through to the LLM while a wrong number is silent.
    with pytest.raises(AmountParseError):
        parse_amount(raw)
