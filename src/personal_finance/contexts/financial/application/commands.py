from __future__ import annotations

import dataclasses
from decimal import Decimal

from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordMovementCommand:
    """One movement of money a bank announced, in Financial's own words.

    Everything here has already been read out of ingestion's vocabulary at the
    boundary, which is why nothing on it is an ingestion type. The instrument
    stays a pair of raw strings on purpose: what it means for routing —
    whether it can find an account, and what kind of account it would open —
    is Financial's decision, and `Transaction.from_alert` is where it is made.

    Deliberately absent: the notification id and anything else naming the
    email. What makes a movement the same movement is its content.
    """

    user_id: UserId
    bank: str
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    instrument_kind: str | None = None
    last_four: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class OpenAccountCommand:
    """An account its owner declared.

    The instrument is what makes the account start collecting: given a bank, a
    kind of instrument and its last four digits, every alert arriving under
    them lands here — including the ones that arrived before this moment. An
    account without one never matches an alert, which is right for cash and
    for a mortgage that emails nothing.
    """

    user_id: UserId
    name: str
    kind: AccountKind
    currency: Currency
    opening_balance: Money | None = None
    bank: str | None = None
    instrument_kind: InstrumentKind | None = None
    last_four: str | None = None
    # Only a liability has one, and it is not the opening balance: the balance
    # is what has been spent, this is what may be.
    credit_limit: Money | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LinkInstrumentCommand:
    """Teach an existing account another of the names its alerts arrive under.

    One real account emails as a debit card for purchases and as an account
    number for transfers. Linking is always the owner's decision: deciding it
    automatically would be guessing about somebody's money.
    """

    user_id: UserId
    account_id: AccountId
    bank: str
    instrument_kind: InstrumentKind
    last_four: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RenameAccountCommand:
    user_id: UserId
    account_id: AccountId
    name: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SetCreditLimitCommand:
    """State or restate what a card may owe.

    Its own command rather than a field on the rename above: a limit is a fact
    about the account that changes on the bank's schedule, not a correction to
    what the owner called it. `None` clears it.
    """

    user_id: UserId
    account_id: AccountId
    # Bare amount: the account carries the currency, and asking a caller to
    # restate it only creates a way to get it wrong.
    credit_limit: Decimal | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CloseAccountCommand:
    user_id: UserId
    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnterTransactionCommand:
    """Money the user says moved, that no alert announced.

    The path for an automatic payment the bank never emails, for cash, for
    anything a parser could not be expected to see. The account is optional:
    somebody watching only what comes in and goes out has none.
    """

    user_id: UserId
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    account_id: AccountId | None = None
    bank: str = ""
    note: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EditTransactionCommand:
    """A correction. Every field left None is left alone.

    `account_id` is three-valued on purpose: absent leaves the movement where
    it is, a value moves it, and `detach` takes it off the account holding it.
    """

    user_id: UserId
    transaction_id: str
    amount: Money | None = None
    occurred_at: PosixTime | None = None
    counterparty: str | None = None
    note: str | None = None
    account_id: AccountId | None = None
    detach: bool = False
