from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from personal_finance.shared.domain.value_objects import Currency, Money


class AmountParseError(ValueError):
    """Raised when a monetary string cannot be read unambiguously."""


# The currency marker and the digits, e.g. "COP29.259,00" or "$13,600.00".
AMOUNT_PATTERN = r"(?:COP|USD|\$)\s*[\d.,]+"

_MARKER = re.compile(r"^\s*(?P<marker>COP|USD|\$)\s*(?P<digits>[\d.,]+)\s*$")
# A trailing group of exactly two digits after the last separator: cents.
_WITH_CENTS = re.compile(r"^(?P<units>\d{1,3}(?:[.,]\d{3})*|\d+)[.,](?P<cents>\d{2})$")
_GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})*$|^\d+$")

_MARKER_CURRENCIES = {"COP": Currency.COP, "$": Currency.COP, "USD": Currency.USD}


def parse_amount(raw: str) -> Money:
    """Read a monetary amount out of a bank alert.

    Bancolombia writes both `COP29.259,00` (dot thousands, comma decimals) and
    `$13,600.00` (the reverse) in the same alert stream, so the separators
    cannot be assumed. The rule used here is positional rather than locale
    based: the last separator is the decimal point only when exactly two digits
    follow it. Every other separator is digit grouping.

    `$` means Colombian pesos: these are alerts from a Colombian bank, and no
    sample distinguishes it from dollars. A `USD` marker is honoured when the
    bank is explicit.
    """
    match = _MARKER.match(raw)

    if match is None:
        raise AmountParseError(f"Not a monetary amount: {raw!r}")

    marker = match.group("marker")
    digits = match.group("digits")

    with_cents = _WITH_CENTS.match(digits)

    if with_cents is not None:
        units = re.sub(r"[.,]", "", with_cents.group("units"))
        value = f"{units}.{with_cents.group('cents')}"
    elif _GROUPED.match(digits):
        # No cents at all: "$19,850" is nineteen thousand, not nineteen.
        value = re.sub(r"[.,]", "", digits)
    else:
        raise AmountParseError(f"Ambiguous digit grouping: {raw!r}")

    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise AmountParseError(f"Not a decimal amount: {raw!r}") from error

    return Money(amount=amount, currency=_MARKER_CURRENCIES[marker])
