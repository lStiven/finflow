from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    MovementDirection,
    MovementId,
    TransactionOrigin,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountEvent(Event):
    """Every account fact belongs to exactly one user.

    Accounts are per-user by design: nothing here is shared, and a household
    view — if it ever exists — will be a query over several users' accounts,
    never a second owner on one of them.
    """

    account_id: AccountId
    user_id: UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountOpened(AccountEvent):
    name: str
    kind: AccountKind
    category: AccountCategory
    currency: Currency
    opening_balance: Balance
    bank: str | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountFingerprintLinked(AccountEvent):
    """A bank/instrument pair now resolves to this account."""

    fingerprint: AccountFingerprint


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountFingerprintUnlinked(AccountEvent):
    """That pair no longer resolves here.

    The correction for a card declared on the wrong account. Recorded
    separately from the assignments it releases because those name movements
    and this names the key: without it, an account that silently stopped
    matching a card would be indistinguishable from a bank that stopped
    sending alerts.
    """

    fingerprint: AccountFingerprint


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountRenamed(AccountEvent):
    name: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountFinancingSet(AccountEvent):
    """This account now charges or earns on its own, on these terms.

    Carries the effective annual rate and nothing else about the arithmetic:
    the terms are the owner's own data — what their mortgage costs, what their
    property is insured for — and an event is the wrong place for any of it.
    What a reader needs is that the account started computing, from when, and
    at what headline rate.
    """

    kind: AccountKind
    rate: Decimal | None
    statement_day: int | None
    accrued_through: dt.date | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountFinancingCleared(AccountEvent):
    """This account stops computing. Every period already posted stays."""


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountBalanceChanged(AccountEvent):
    """One movement moved the balance, and by how much.

    Carries the movement that caused it so the running total can be retraced
    against the ledger rather than trusted blindly.
    """

    movement_id: MovementId
    direction: MovementDirection
    amount: Money
    balance: Balance


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountBalanceReversed(AccountEvent):
    """A movement was taken back off the balance, and where it landed.

    Its own fact rather than an `AccountBalanceChanged` with the direction
    flipped: the row is gone, and a reader following `direction` back to a
    movement that says the opposite would be chasing something that never
    happened. `movement_id` is what left, and `balance` is what the account
    holds now that it is gone.
    """

    movement_id: MovementId
    direction: MovementDirection
    amount: Money
    balance: Balance


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountBalanceRebuilt(AccountEvent):
    """The balance was recomputed from the ledger instead of nudged.

    A repair is a fact worth recording: if it ever changes the number, the
    running total had drifted and somebody needs to know why.
    """

    balance: Balance
    movements_applied: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountBalanceRestated(AccountEvent):
    """The owner said what the account holds, and the opening balance was
    solved backwards from the ledger to match.

    Not a repair, which is what makes it a different fact from
    `AccountBalanceRebuilt`: nothing drifted. The starting point was unknown
    or wrong, and somebody supplied the one number they can actually check.
    """

    opening_balance: Balance
    balance: Balance
    movements_applied: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountClosed(AccountEvent):
    pass


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountReopened(AccountEvent):
    """Taking movements again, after a closure that turned out to be wrong.

    Its own event rather than a closure with a null date: closing and
    reopening are two facts about the account's life, and a reader that only
    ever saw the current state could not tell an account that was never
    closed from one closed and reopened twice.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionEvent(Event):
    """Every movement belongs to exactly one user, like every account."""

    movement_id: MovementId
    user_id: UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionRecorded(TransactionEvent):
    """A movement, from the moment Financial first built it.

    Recorded when the aggregate is created, and — like every event here —
    published only once the write that accepted it succeeded. On a redelivery
    the ledger's conditional insert refuses the row, and the use case must
    drop these events rather than pull them: `movement_id` is stable across
    deliveries, but `event_id` is not, so a subscriber deduping on the latter
    would treat the replay as new work.

    `movement_occurred_at` is when the money moved, which is not `occurred_at`
    — that one says when this fact was recorded, and an alert can arrive days
    late.

    `account_fingerprint` is absent when the alert named no usable instrument.
    That is the unassigned case, and it is a fact worth publishing: nothing
    else explains why a movement is sitting outside every balance.
    """

    direction: MovementDirection
    amount: Money
    movement_occurred_at: PosixTime
    counterparty: str
    bank: str
    origin: TransactionOrigin
    account_fingerprint: AccountFingerprint | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionAssigned(TransactionEvent):
    """The movement now belongs to an account and has moved its balance."""

    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionEdited(TransactionEvent):
    """Somebody corrected what this movement says.

    Carries the corrected values, not the original: the original stays on the
    aggregate, where a reader can compare the two. The identity is absent
    from the list on purpose — an edit never moves it.
    """

    amount: Money
    movement_occurred_at: PosixTime
    counterparty: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionUnassigned(TransactionEvent):
    """The movement came off the account that was holding it.

    `account_id` is the account it left, so the balance it stopped counting
    towards can be traced back.
    """

    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionErased(TransactionEvent):
    """The movement was removed from the ledger at its owner's request.

    The one fact nothing else can reconstruct afterwards. Every other event
    here describes a row a reader can still go and look at; this one describes
    a row that is gone, so it carries what it was worth and where it was
    counting — otherwise a balance that dropped by two thousand has nothing
    behind it to explain the drop.

    `account_id` is absent when the movement was sitting on no account, which
    is also exactly when no balance moved.
    """

    direction: MovementDirection
    amount: Money
    account_id: AccountId | None
