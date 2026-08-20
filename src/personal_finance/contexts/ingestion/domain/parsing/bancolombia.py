from __future__ import annotations

import re

from personal_finance.contexts.ingestion.domain.parsing.amounts import (
    AMOUNT_PATTERN,
    parse_amount,
)
from personal_finance.contexts.ingestion.domain.parsing.dates import (
    DATE_TIME_PATTERN,
    parse_date_time,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)


BANK_NAME = "bancolombia"

# Every alert opens with this prefix, whatever the movement.
_PREFIX = r"Bancolombia:\s*"
_AMOUNT = rf"(?P<amount>{AMOUNT_PATTERN})"
_WHEN = rf"el\s+(?P<when>{DATE_TIME_PATTERN})"

# The masked marker is not consistent: "cuenta *5261" in one alert, "cuenta
# 5261" in another, so the asterisk is optional everywhere.
_CARD_PURCHASE = re.compile(
    _PREFIX + rf"Compraste\s+{_AMOUNT}\s+en\s+(?P<merchant>.+?)"
    r"\s+con\s+tu\s+(?P<instrument>T\.Cred|T\.Deb|T\.Debito)"
    rf"\s*\*?(?P<last_four>\d{{4}})\s*,?\s+{_WHEN}",
    re.IGNORECASE,
)

_QR_PAYMENT = re.compile(
    _PREFIX + rf"(?P<payer>.+?)\s+pagaste\s+{_AMOUNT}"
    r"\s+por\s+codigo\s+QR\s+desde\s+tu\s+cuenta\s*\*?(?P<last_four>\d+)"
    rf"\s+a\s+la\s+llave\s+(?P<key>\S+)\s+{_WHEN}",
    re.IGNORECASE,
)

_TRANSFER = re.compile(
    _PREFIX + rf"Transferiste\s+{_AMOUNT}"
    r"\s+desde\s+tu\s+cuenta\s*\*?(?P<last_four>\d+)"
    rf"\s+a\s+la\s+cuenta\s*\*?(?P<destination>\d+)\s+{_WHEN}",
    re.IGNORECASE,
)

_INCOMING_PAYMENT = re.compile(
    _PREFIX + r"Recibiste\s+un\s+pago\s+de\s+(?P<concept>.+?)"
    rf"\s+de\s+(?P<payer>.+?)\s+por\s+{_AMOUNT}"
    rf"\s+en\s+tu\s+cuenta\s+de\s+(?P<account_type>\w+)\s+{_WHEN}",
    re.IGNORECASE,
)

_CARD_KINDS = {
    "t.cred": InstrumentKind.CREDIT_CARD,
    "t.deb": InstrumentKind.DEBIT_CARD,
    "t.debito": InstrumentKind.DEBIT_CARD,
}
_ACCOUNT_KINDS = {
    "ahorros": InstrumentKind.SAVINGS_ACCOUNT,
    "corriente": InstrumentKind.CHECKING_ACCOUNT,
}


class BancolombiaParser:
    """Deterministic parser for Bancolombia's `Alertas y Notificaciones`.

    Returns None when no template matches, which is the signal for the caller
    to fall back to the LLM. It never guesses: a partially matched alert is a
    miss, not a half-filled transaction.
    """

    bank = BANK_NAME

    def parse(self, text: str) -> ExtractedTransaction | None:
        normalized = " ".join(text.split())

        for handler in (
            self._card_purchase,
            self._qr_payment,
            self._transfer,
            self._incoming_payment,
        ):
            transaction = handler(normalized)

            if transaction is not None:
                return transaction

        return None

    def _card_purchase(self, text: str) -> ExtractedTransaction | None:
        match = _CARD_PURCHASE.search(text)

        if match is None:
            return None

        return ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=TransactionDirection.OUTGOING,
            amount=parse_amount(match.group("amount")),
            occurred_at=parse_date_time(match.group("when")),
            counterparty=match.group("merchant").strip(),
            instrument=Instrument(
                kind=_CARD_KINDS[match.group("instrument").lower()],
                last_four=match.group("last_four"),
            ),
        )

    def _qr_payment(self, text: str) -> ExtractedTransaction | None:
        match = _QR_PAYMENT.search(text)

        if match is None:
            return None

        return ExtractedTransaction(
            kind=TransactionKind.QR_PAYMENT,
            direction=TransactionDirection.OUTGOING,
            amount=parse_amount(match.group("amount")),
            occurred_at=parse_date_time(match.group("when")),
            # The transfer key is the only identifier of who was paid: the
            # alert never names the recipient.
            counterparty=match.group("key").strip(),
            instrument=Instrument(
                kind=InstrumentKind.ACCOUNT,
                last_four=_last_four(match.group("last_four")),
            ),
        )

    def _transfer(self, text: str) -> ExtractedTransaction | None:
        match = _TRANSFER.search(text)

        if match is None:
            return None

        return ExtractedTransaction(
            kind=TransactionKind.TRANSFER,
            direction=TransactionDirection.OUTGOING,
            amount=parse_amount(match.group("amount")),
            occurred_at=parse_date_time(match.group("when")),
            counterparty=match.group("destination").strip(),
            instrument=Instrument(
                kind=InstrumentKind.ACCOUNT,
                last_four=_last_four(match.group("last_four")),
            ),
        )

    def _incoming_payment(self, text: str) -> ExtractedTransaction | None:
        match = _INCOMING_PAYMENT.search(text)

        if match is None:
            return None

        account_type = match.group("account_type").lower()

        return ExtractedTransaction(
            kind=TransactionKind.INCOMING_PAYMENT,
            direction=TransactionDirection.INCOMING,
            amount=parse_amount(match.group("amount")),
            occurred_at=parse_date_time(match.group("when")),
            counterparty=match.group("payer").strip(),
            instrument=Instrument(
                kind=_ACCOUNT_KINDS.get(account_type, InstrumentKind.ACCOUNT),
            ),
        )


def _last_four(digits: str) -> str:
    return digits[-4:]
