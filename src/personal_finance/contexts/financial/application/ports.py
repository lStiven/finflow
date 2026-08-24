from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
)
from personal_finance.shared.domain.value_objects import UserId


class AccountRepository(Protocol):
    """Persistence port for `Account`.

    Every method takes the owner. `AccountFingerprint` carries no user of its
    own — unlike `MovementFingerprint`, which does — so two people at one bank
    whose cards end in the same four digits produce the same fingerprint. The
    scoping is what keeps their money apart, and an implementation that could
    answer without knowing whose data it is asked for would be one query away
    from putting one person's spending on another's balance.
    """

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        """Load one account, or None when this user has no such account."""
        ...

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        """Resolve a bank/instrument pair to the account that answers to it.

        The hot path: every parsed alert asks this, so an implementation must
        not scan. One account answers to several fingerprints — a checking
        account emails as a debit card for purchases and as an account number
        for transfers.
        """
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        """Every account this user owns, for their own list and net worth."""
        ...

    def save(self, account: Account) -> None:
        """Replace an account that already exists, and its fingerprints.

        Everything `Account` can be told to do after it is opened needs this:
        rename, confirm, close, rebuild a drifted total, and link the second
        instrument one real account emails under. Never used to move a
        balance — that is the ledger's atomic write, and a read-then-write
        here would drop one of two movements landing at once.
        """
        ...

    def add(self, account: Account) -> bool:
        """Create an account and the fingerprints it answers to, or lose.

        Returns False when something already holds that identity — two
        workers can be opening the same account from the same redelivered
        alert at the same moment, and only one of them may win. The loser
        re-reads and uses the account that won rather than creating a second
        one holding half the movements.
        """
        ...


class TransactionLedger(Protocol):
    """Persistence port for `Transaction`, and the balance it moves.

    The ledger is the authority; a balance is a running total of what is
    written here. That is why this is one method and not two: the row and the
    balance change it causes have to land together, or a balance moves with
    nothing behind it to explain or repair it.
    """

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        """Write the ledger row and its balance change in one atomic write.

        Returns False when the row already existed, which is how a redelivery
        is recognised: the movement's identity is derived from its content, so
        the same alert read twice writes the same key. Nothing is applied on
        that path — not the row, not the balance.

        `balance_delta` is how far the account's running total moves, signed:
        what an outgoing movement does to a credit card is not what it does to
        a savings account, and that rule belongs to `Account`, which has
        already applied it by the time this is called. It must be None exactly
        when the transaction is unassigned, and set otherwise.
        """
        ...

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        """Load one movement, for reading a balance back to its rows."""
        ...

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        """Every movement on one account, oldest first.

        What `Account.rebuild` replays when a running total is suspected of
        having drifted from the rows behind it.
        """
        ...

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        """Movements no account answered for, for the user to place by hand."""
        ...
