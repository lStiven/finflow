from __future__ import annotations

import dataclasses
import enum

from personal_finance.shared.domain.value_objects import (
    Money,
    PosixTime,
    ValueObject,
)


class TransactionDirection(enum.Enum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class TransactionKind(enum.Enum):
    CARD_PURCHASE = "card_purchase"
    QR_PAYMENT = "qr_payment"
    TRANSFER = "transfer"
    INCOMING_PAYMENT = "incoming_payment"


class InstrumentKind(enum.Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    SAVINGS_ACCOUNT = "savings_account"
    CHECKING_ACCOUNT = "checking_account"
    ACCOUNT = "account"


@dataclasses.dataclass(frozen=True, slots=True)
class Instrument(ValueObject):
    """The card or account the money moved through, as the bank named it.

    `last_four` is what the alert exposes; the real number never appears.
    """

    kind: InstrumentKind
    last_four: str | None = None

    def __post_init__(self) -> None:
        if self.last_four is not None and not self.last_four.isdigit():
            raise ValueError(f"Instrument last_four must be digits: {self.last_four!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class ExtractedTransaction(ValueObject):
    """What a parser recovered from one bank notification.

    This is ingestion's output, not a financial record: it states what the
    email said, with no balances applied and no merchant normalized yet. The
    Financial and Merchant contexts turn it into their own concepts.
    """

    kind: TransactionKind
    direction: TransactionDirection
    amount: Money
    occurred_at: PosixTime
    # The other side of the movement as the bank wrote it: a merchant name, a
    # payer, a transfer key, or a masked destination account.
    counterparty: str
    # Which institution this moved money at — a template parser already knows
    # its own bank; the LLM fallback has to read it. Financial needs this to
    # tell "Bancolombia *7653" apart from "Nu *7653": the account an alert
    # belongs to is never just the instrument.
    bank: str
    instrument: Instrument | None = None

    def __post_init__(self) -> None:
        if not self.counterparty.strip():
            raise ValueError("Extracted transaction requires a counterparty")

        if not self.bank.strip():
            raise ValueError("Extracted transaction requires a bank")
