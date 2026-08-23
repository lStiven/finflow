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
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


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
