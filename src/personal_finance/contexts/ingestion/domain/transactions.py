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


class TransferKind(enum.Enum):
    """Money that moved between two instruments of the same owner.

    Its own vocabulary, apart from `TransactionKind`, because these are not
    one movement: `TransactionKind.TRANSFER` is money leaving for somebody
    else's account, which is a single expense. This is money that never left —
    it changed sides inside one person's own finances, and describing it as
    one movement is what makes a card payment either vanish or double.

    `CARD_PAYMENT` is the only member because it is the only one a bank
    states unambiguously: the alert names both the card being paid and the
    account paying it, and both are the holder's. A transfer to an account
    number the bank does not claim is somebody else's until the owner says
    otherwise.
    """

    CARD_PAYMENT = "card_payment"


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

        bank = self.bank.strip().lower()

        if not bank:
            raise ValueError("Extracted transaction requires a bank")

        # Normalized so the same bank always compares equal regardless of
        # which path named it: a template parser's own constant, or whatever
        # casing the LLM fallback happened to answer with. Financial keys
        # account-matching on this value, and a casing mismatch would split
        # one real account into two.
        object.__setattr__(self, "bank", bank)


@dataclasses.dataclass(frozen=True, slots=True)
class ExtractedTransfer(ValueObject):
    """One email that moved money between two of the owner's own instruments.

    Deliberately not an `ExtractedTransaction` with a second field bolted on.
    A transaction states *one* direction against *one* instrument, and every
    consumer downstream reads it that way; a payment to your own credit card
    is money leaving an account and debt falling on a card, and reporting
    either half alone is wrong in a way nothing later can detect. Reporting
    the wrong half is worse still: on a liability an outgoing movement raises
    what is owed, so a card payment read as one expense on the card *adds* the
    amount to the debt it just paid off.

    Both instruments are required to carry their last four digits: the whole
    reason this type exists is that each side can be routed to an account of
    its own, and a side that cannot be routed is a movement nobody can place.
    They must also differ — one instrument paying itself is not a movement of
    money, it is a misread alert.
    """

    kind: TransferKind
    amount: Money
    occurred_at: PosixTime
    bank: str
    # Where the money came from, and what it went to. For a card payment:
    # the account that paid, and the card whose debt fell.
    source: Instrument
    destination: Instrument

    def __post_init__(self) -> None:
        bank = self.bank.strip().lower()

        if not bank:
            raise ValueError("Extracted transfer requires a bank")

        object.__setattr__(self, "bank", bank)

        if self.source.last_four is None or self.destination.last_four is None:
            raise ValueError(
                "Extracted transfer requires last four digits on both sides: "
                "a side without them can never be routed to an account",
            )

        if self.source == self.destination:
            raise ValueError(
                "Extracted transfer requires two different instruments",
            )


# What a deterministic parser may return: one movement, or both sides of one
# that never left the owner's own finances.
ExtractedMovement = ExtractedTransaction | ExtractedTransfer
