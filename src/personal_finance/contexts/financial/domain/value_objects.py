from __future__ import annotations

import dataclasses
from decimal import Decimal
import enum
from typing import Self
import uuid

from personal_finance.contexts.financial.domain.exceptions import (
    CurrencyMismatchError,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    JsonValue,
    Money,
    PosixTime,
    ValueObject,
)


class AccountCategory(enum.Enum):
    """Which side of net worth an account sits on.

    Net worth is the sum of assets minus the sum of liabilities, so this is
    what decides a balance's meaning: 1.2M on a savings account is money you
    have, the same 1.2M on a credit card is money you owe.
    """

    ASSET = "asset"
    LIABILITY = "liability"


class AccountKind(enum.Enum):
    """What kind of account this is.

    Explicit string values: the kind is persisted, so reordering the members
    must not rewrite anybody's data.
    """

    SAVINGS = "savings"
    CHECKING = "checking"
    CASH = "cash"
    CREDIT_CARD = "credit_card"
    LOAN = "loan"
    MORTGAGE = "mortgage"

    @property
    def category(self) -> AccountCategory:
        """Derived, never chosen: nobody gets to declare a mortgage an asset."""
        return (
            AccountCategory.LIABILITY
            if self in _LIABILITY_KINDS
            else AccountCategory.ASSET
        )


_LIABILITY_KINDS = frozenset(
    {AccountKind.CREDIT_CARD, AccountKind.LOAN, AccountKind.MORTGAGE},
)


class AccountStatus(enum.Enum):
    # Discovered from a bank alert; nobody has looked at it yet.
    AUTOMATIC = "automatic"
    # A user created it, renamed it, or accepted it as it stands.
    CONFIRMED = "confirmed"


@dataclasses.dataclass(frozen=True, slots=True)
class AccountId(ValueObject):
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid account id: {value!r}") from error

    def to_dict(self) -> JsonValue:
        return str(self.value)


def normalize_last_four(value: str) -> str:
    """The trailing digits of a card or account, as everything must spell them.

    Trailing, not verbatim: a template parser prints what its bank printed and
    the LLM fallback may hand back more of the number, so the same card would
    otherwise fingerprint two ways and split one balance into two. ASCII
    digits only — `str.isdigit` alone accepts `²` and Arabic-Indic numerals,
    and this text comes from an untrusted email.
    """
    digits = value.strip()

    if not digits or not digits.isascii() or not digits.isdigit():
        raise ValueError(f"Last four must be digits: {value!r}")

    return digits[-4:]


@dataclasses.dataclass(frozen=True, slots=True)
class AccountFingerprint(ValueObject):
    """How an incoming movement finds the account it belongs to.

    Built from what a bank alert exposes — the institution, the sort of
    instrument, and its last four digits. The last four are required: without
    them "a savings account at Bancolombia" would match every savings account
    the user holds there, and two real accounts would silently merge into one
    wrong balance. A movement whose instrument has no last four stays
    unassigned instead.

    Built through `from_parts` so every caller normalizes the same way; the
    constructor stays open for reading a stored value back.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Account fingerprint cannot be empty")

    @classmethod
    def from_parts(
        cls,
        *,
        bank: str,
        instrument_kind: str,
        last_four: str,
    ) -> Self:
        institution = bank.strip().lower()

        if not institution:
            raise ValueError("Account fingerprint requires a bank")

        instrument = instrument_kind.strip().lower()

        if not instrument:
            raise ValueError("Account fingerprint requires an instrument kind")

        return cls(
            value=f"{institution}:{instrument}:{normalize_last_four(last_four)}",
        )

    def to_dict(self) -> JsonValue:
        return self.value


class BalanceSign(enum.Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclasses.dataclass(frozen=True, slots=True)
class Balance(ValueObject):
    """What an account holds, as an unsigned amount plus its sign.

    `Money` stays unsigned everywhere in this codebase — direction belongs to
    the movement, not to the quantity — but a balance still needs a sign of
    its own: an account discovered from a bank alert starts at zero with its
    real opening balance unknown, so the first purchase legitimately takes the
    running total below zero. That is a known-incomplete history, not an
    error, and refusing to represent it would mean inventing a number.

    On a liability the amount is what is owed: `POSITIVE` 1.2M on a credit
    card means a 1.2M debt, and `AccountCategory` is what makes it subtract.
    """

    amount: Money
    sign: BalanceSign = BalanceSign.POSITIVE

    def __post_init__(self) -> None:
        # Zero has no direction; without this, two equal balances could differ.
        if self.amount.amount == 0:
            object.__setattr__(self, "sign", BalanceSign.POSITIVE)

    @classmethod
    def zero(cls, currency: Currency) -> Self:
        return cls(amount=Money(amount=Decimal("0"), currency=currency))

    @classmethod
    def from_signed(cls, amount: Decimal, currency: Currency) -> Self:
        return cls(
            amount=Money(amount=abs(amount), currency=currency),
            sign=BalanceSign.NEGATIVE if amount < 0 else BalanceSign.POSITIVE,
        )

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_negative(self) -> bool:
        return self.sign is BalanceSign.NEGATIVE

    @property
    def signed_amount(self) -> Decimal:
        return -self.amount.amount if self.is_negative else self.amount.amount

    def plus(self, amount: Money) -> Self:
        return self._moved_by(amount, Decimal(1))

    def minus(self, amount: Money) -> Self:
        return self._moved_by(amount, Decimal(-1))

    def _moved_by(self, amount: Money, factor: Decimal) -> Self:
        if amount.currency is not self.currency:
            raise CurrencyMismatchError(
                f"Cannot move {amount.currency.value} on a "
                f"{self.currency.value} balance",
            )

        return type(self).from_signed(
            self.signed_amount + (amount.amount * factor),
            self.currency,
        )

    def to_dict(self) -> JsonValue:
        return {
            "amount": str(self.signed_amount),
            "currency": self.currency.value,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class MovementId(ValueObject):
    """Identity of one movement of money.

    Deliberately not the identity of the email that announced it: the same
    notification can be delivered twice, and two different notifications can
    describe the same purchase. What makes a movement the same movement is
    Financial's own business.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Movement id cannot be empty")

    def to_dict(self) -> JsonValue:
        return self.value


class MovementDirection(enum.Enum):
    """Which way the money went, in Financial's own vocabulary.

    Ingestion has an enum that happens to look identical today. Mapping
    between them at the boundary is the point: neither context gets to change
    the other's meaning by editing its own.
    """

    OUTGOING = "outgoing"
    INCOMING = "incoming"


@dataclasses.dataclass(frozen=True, slots=True)
class LedgerMovement(ValueObject):
    """One accepted movement of money, ready to hit an account.

    Carries its own identity so the balance change can be tied back to the
    ledger row that caused it — the balance is a running total of these, and
    a total nobody can retrace is a total nobody can repair.
    """

    movement_id: MovementId
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
