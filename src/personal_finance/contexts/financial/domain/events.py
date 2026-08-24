from __future__ import annotations

import dataclasses

from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    MovementDirection,
    MovementId,
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountFingerprintLinked(AccountEvent):
    """A bank/instrument pair now resolves to this account."""

    fingerprint: AccountFingerprint


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountRenamed(AccountEvent):
    name: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountConfirmed(AccountEvent):
    pass


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
class AccountBalanceRebuilt(AccountEvent):
    """The balance was recomputed from the ledger instead of nudged.

    A repair is a fact worth recording: if it ever changes the number, the
    running total had drifted and somebody needs to know why.
    """

    balance: Balance
    movements_applied: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountClosed(AccountEvent):
    pass


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
    account_fingerprint: AccountFingerprint | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionAssigned(TransactionEvent):
    """The movement now belongs to an account and has moved its balance."""

    account_id: AccountId
