"""Read side: what an accounts screen and a net-worth figure need.

Filtering happens here rather than in the index, for the same reason it does
in Merchant: a person has a handful of accounts and their movements live in
one partition, and keeping the answer in one place beats three queries that
can disagree. This is the seam to push down if anyone ever outgrows it.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from decimal import Decimal
import enum

from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountId,
    TransactionOrigin,
)
from personal_finance.shared.domain.value_objects import Currency, UserId


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class AccountScope(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NetWorth:
    """Assets minus liabilities, and the two halves it came from.

    Per currency, never summed across them: converting would need an exchange
    rate, which is a fact about a moment nobody recorded here.
    """

    currency: Currency
    assets: Decimal
    liabilities: Decimal

    @property
    def total(self) -> Decimal:
        return self.assets - self.liabilities


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountsView:
    accounts: Sequence[Account]
    # One entry per currency the user holds. Empty when they hold no accounts,
    # which is an ordinary state and not an error.
    net_worth: Sequence[NetWorth]


class ListAccountsUseCase:
    """Every account a user declared, with what each holds now."""

    def __init__(self, *, accounts: AccountRepository) -> None:
        self._accounts = accounts

    def execute(
        self,
        *,
        user_id: UserId,
        scope: AccountScope = AccountScope.OPEN,
    ) -> AccountsView:
        held = [
            account
            for account in self._accounts.list_by_user(user_id)
            if _in_scope(account, scope)
        ]
        held.sort(key=lambda account: account.name.casefold())

        return AccountsView(accounts=held, net_worth=_net_worth(held))


class GetAccountUseCase:
    def __init__(self, *, accounts: AccountRepository) -> None:
        self._accounts = accounts

    def execute(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return self._accounts.find(user_id=user_id, account_id=account_id)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionQuery:
    user_id: UserId
    account_id: AccountId | None = None
    # True lists only what no account claimed — the queue somebody works
    # through after declaring an account, or the whole ledger for somebody who
    # declared none.
    unassigned: bool | None = None
    origin: TransactionOrigin | None = None
    search: str | None = None
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionPage:
    transactions: Sequence[Transaction]
    total: int


class ListTransactionsUseCase:
    """Movements, newest first, filtered the way a screen asks for them."""

    def __init__(self, *, ledger: TransactionLedger) -> None:
        self._ledger = ledger

    def execute(self, query: TransactionQuery) -> TransactionPage:
        if query.account_id is not None:
            found = list(
                self._ledger.list_movements(
                    user_id=query.user_id,
                    account_id=query.account_id,
                ),
            )
        elif query.unassigned:
            found = list(self._ledger.list_unassigned(query.user_id))
        else:
            found = list(self._ledger.list_all(query.user_id))

        if query.unassigned is False:
            found = [movement for movement in found if movement.account_id is not None]

        if query.origin is not None:
            found = [movement for movement in found if movement.origin is query.origin]

        if query.search:
            needle = query.search.strip().casefold()
            found = [
                movement
                for movement in found
                if needle in movement.counterparty.casefold()
            ]

        # Newest first: what somebody looks at when they open the app.
        found.sort(
            key=lambda movement: movement.occurred_at.as_epoch_seconds(),
            reverse=True,
        )
        window = found[query.offset : query.offset + query.limit]

        return TransactionPage(transactions=window, total=len(found))


class GetTransactionUseCase:
    def __init__(self, *, ledger: TransactionLedger) -> None:
        self._ledger = ledger

    def execute(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        return self._ledger.find(user_id=user_id, transaction_id=transaction_id)


def _in_scope(account: Account, scope: AccountScope) -> bool:
    if scope is AccountScope.ALL:
        return True

    return account.is_closed == (scope is AccountScope.CLOSED)


def _net_worth(accounts: Sequence[Account]) -> Sequence[NetWorth]:
    """Assets minus liabilities, one figure per currency.

    A closed account still counts: a paid-off loan sitting at zero changes
    nothing, and one closed with a balance is money that is still somewhere.
    """
    assets: dict[Currency, Decimal] = {}
    liabilities: dict[Currency, Decimal] = {}

    for account in accounts:
        side = assets if account.category is AccountCategory.ASSET else liabilities
        side[account.currency] = (
            side.get(account.currency, Decimal(0)) + account.balance.signed_amount
        )

    return [
        NetWorth(
            currency=currency,
            assets=assets.get(currency, Decimal(0)),
            liabilities=liabilities.get(currency, Decimal(0)),
        )
        for currency in sorted(
            set(assets) | set(liabilities),
            key=lambda currency: currency.value,
        )
    ]
