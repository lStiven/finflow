from __future__ import annotations

import re

from personal_finance.contexts.ingestion.domain.parsing.amounts import (
    AMOUNT_PATTERN,
    parse_amount,
)
from personal_finance.contexts.ingestion.domain.parsing.dates import (
    CLOCK_12H_PATTERN,
    SPANISH_DATE_PATTERN,
    parse_spanish_date_time,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)


BANK_NAME = "lulo bank"

# Lulo sends HTML only, so every template here is written against the single
# line `extract_text` produces, not against the markup.
_AMOUNT = rf"(?P<amount>{AMOUNT_PATTERN})"
# The alert lays the movement out as labelled legs, each `<label> <kind> •
# <digits>`, and the bullet is the only separator the bank uses.
_ORIGIN = r"Origen\s+[^\W\d_]+\s*•\s*(?P<origin>\d+)"
_DESTINATION = r"Destino\s+[^\W\d_]+\s*•\s*(?P<destination>\d+)"
# Whatever the template puts between the fields — a counterparty bank's name,
# a transaction id, a receipt number, the cost of the transfer. None of it is
# needed and all of it varies.
#
# Written as *whole words, counted, and tempered* rather than as `.*?`, for
# two reasons that are both bugs otherwise. A body is one long line and an
# unbounded lazy gap next to another one backtracks cubically: a digest email
# stalled the parse worker for 45 seconds. And a gap that may cross anything
# lets an alert missing a leg complete itself from the *next* alert in the
# same body — a real amount booked against somebody else's account digits and
# a third alert's date. Excluding the words that open an alert stops the match
# at the boundary, which is a miss, which is the LLM's turn.
_GAP = r"(?:(?!Recibiste|Realizaste|Fecha)\S+\s+){0,24}"
# Bounded for the same reason, and no counterparty name reaches 80 characters
# or contains the bullet that separates the legs below.
_PAYER = r"(?P<payer>[^•]{1,80}?)"
_RECIPIENT = r"(?P<recipient>[^•]{1,80}?)"
_WHEN = (
    rf"Fecha\s+(?P<date>{SPANISH_DATE_PATTERN})"
    rf"\s+Hora\s+(?P<clock>{CLOCK_12H_PATTERN})"
)

# "Recibiste $1,000 de STIVEN PINZON TRIANA": money arriving through Bre-B,
# Colombia's instant-payment scheme.
_BRE_B_INCOMING = re.compile(
    rf"Recibiste\s+{_AMOUNT}\s+de\s+{_PAYER}"
    rf"\s+{_ORIGIN}\s+{_GAP}{_DESTINATION}\s+{_GAP}{_WHEN}",
    re.IGNORECASE,
)

# "Realizaste una transferencia a STIVEN PINZON por $1,169". Named recipient
# plus the key it was sent to; only the origin leg is the holder's.
_BRE_B_OUTGOING = re.compile(
    rf"Realizaste\s+una\s+transferencia\s+a\s+{_RECIPIENT}\s+por\s+{_AMOUNT}"
    rf"\s+{_ORIGIN}\s+{_GAP}{_WHEN}",
    re.IGNORECASE,
)

# "Recibiste de OMNIPRO COLOMBIA $6.468.490." — an ordinary incoming transfer,
# which words the same fact in the opposite order and ends the sentence with a
# full stop the amount pattern deliberately does not eat.
_INCOMING_TRANSFER = re.compile(
    rf"Recibiste\s+de\s+{_PAYER}\s+{_AMOUNT}\s*\.?"
    rf"\s+{_ORIGIN}\s+{_GAP}{_DESTINATION}\s+{_GAP}{_WHEN}",
    re.IGNORECASE,
)


class LuloBankParser:
    """Deterministic parser for Lulo bank's `Notificaciones` alerts.

    Same contract as every other template parser: None when nothing matches,
    never a half-filled movement.

    Two things are specific to this bank and worth stating once.

    **The holder's own leg is chosen by direction, not by position.** Every
    alert names two accounts, and only one of them is the reader's: money
    arriving lands on `Destino`, money leaving departs from `Origen`. The
    other leg belongs to whoever was on the other side — routing a movement
    to it would place it on an account the reader does not hold.

    **The leg's own word is ignored.** Lulo calls the very same account
    `ahorro` in one alert and `cuenta` in the next — 7111 appears as both
    across four real emails — so the word is not a taxonomy, and honouring it
    would fingerprint one real account two different ways and make the owner
    declare it twice to catch all its movements. Every leg is
    `InstrumentKind.ACCOUNT`.
    """

    bank = BANK_NAME

    def parse(self, text: str) -> ExtractedTransaction | None:
        normalized = " ".join(text.split())

        for handler in (
            self._bre_b_incoming,
            self._bre_b_outgoing,
            self._incoming_transfer,
        ):
            transaction = handler(normalized)

            if transaction is not None:
                return transaction

        return None

    def _bre_b_incoming(self, text: str) -> ExtractedTransaction | None:
        match = _BRE_B_INCOMING.search(text)

        if match is None:
            return None

        return self._received(
            amount=match.group("amount"),
            payer=match.group("payer"),
            destination=match.group("destination"),
            date=match.group("date"),
            clock=match.group("clock"),
        )

    def _incoming_transfer(self, text: str) -> ExtractedTransaction | None:
        match = _INCOMING_TRANSFER.search(text)

        if match is None:
            return None

        return self._received(
            amount=match.group("amount"),
            payer=match.group("payer"),
            destination=match.group("destination"),
            date=match.group("date"),
            clock=match.group("clock"),
        )

    def _bre_b_outgoing(self, text: str) -> ExtractedTransaction | None:
        match = _BRE_B_OUTGOING.search(text)

        if match is None:
            return None

        return ExtractedTransaction(
            kind=TransactionKind.TRANSFER,
            direction=TransactionDirection.OUTGOING,
            amount=parse_amount(match.group("amount")),
            occurred_at=parse_spanish_date_time(
                match.group("date"),
                match.group("clock"),
            ),
            bank=self.bank,
            # The recipient's name, not the key it was sent to: the key is an
            # address of theirs, and this alert is one of the few that names
            # the person behind it.
            counterparty=match.group("recipient").strip(),
            instrument=Instrument(
                kind=InstrumentKind.ACCOUNT,
                last_four=_last_four(match.group("origin")),
            ),
        )

    def _received(
        self,
        *,
        amount: str,
        payer: str,
        destination: str,
        date: str,
        clock: str,
    ) -> ExtractedTransaction:
        """Both incoming templates state the same fact in different words."""
        return ExtractedTransaction(
            kind=TransactionKind.INCOMING_PAYMENT,
            direction=TransactionDirection.INCOMING,
            amount=parse_amount(amount),
            occurred_at=parse_spanish_date_time(date, clock),
            bank=self.bank,
            counterparty=payer.strip(),
            instrument=Instrument(
                kind=InstrumentKind.ACCOUNT,
                last_four=_last_four(destination),
            ),
        )


def _last_four(digits: str) -> str:
    return digits[-4:]
