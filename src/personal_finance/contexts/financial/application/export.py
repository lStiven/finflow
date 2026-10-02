"""Movements as rows for a file somebody takes out of the app.

The rows are the list the Transacciones screen shows, read by the same use
case and the same filter, so an export can never disagree with the screen it
was asked from. What this adds is the two names a file cannot look up later —
which account a movement sat on and what its category is called — because a
spreadsheet opened next month has no API behind it.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from typing import Protocol

from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    MerchantDirectory,
    TransactionLedger,
)
from personal_finance.contexts.financial.application.queries import (
    AttributedTransaction,
    ListTransactionsUseCase,
    MovementFilter,
    TransactionQuery,
)
from personal_finance.shared.domain.value_objects import UserId


# A year of a busy personal ledger is a few thousand rows. The ceiling is about
# the response, not the ledger: a Lambda answer stops at 6 MB, and a file cut
# short without saying so is worse than a refusal that asks for a shorter range.
MAX_EXPORT_ROWS = 10_000
# What a client reads to tell this refusal from any other 422.
EXPORT_TOO_LARGE = "export_too_large"


class ExportTooLargeError(Exception):
    """More movements matched than one file may carry."""

    def __init__(self, matched: int, *, limit: int) -> None:
        super().__init__(
            f"{matched} movements match; an export carries at most "
            f"{limit}. Narrow the dates.",
        )
        self.matched = matched
        self.limit = limit


class CategoryNamer(Protocol):
    """What each category value this user can see is called."""

    def category_labels(self, *, user_id: UserId) -> Mapping[str, str]:
        """Keyed by value. A user's own category carries the name they gave
        it; a shipped one carries the API's label, which a file in another
        language restates from the value."""
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionExport:
    rows: list[AttributedTransaction]
    # Keyed by account id, for the accounts this user declared — closed ones
    # included, because a movement from last year may sit on one.
    account_names: Mapping[str, str]
    category_labels: Mapping[str, str]


class ExportTransactionsUseCase:
    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        accounts: AccountRepository,
        merchants: MerchantDirectory | None = None,
        categories: CategoryNamer | None = None,
    ) -> None:
        self._list = ListTransactionsUseCase(ledger=ledger, merchants=merchants)
        self._accounts = accounts
        self._categories = categories

    def execute(self, movement_filter: MovementFilter) -> TransactionExport:
        page = self._list.execute(
            # `total` says how many matched regardless of the window, so the
            # ceiling is enough to both fill the file and know it overflowed.
            TransactionQuery(filter=movement_filter, limit=MAX_EXPORT_ROWS),
        )

        if page.total > MAX_EXPORT_ROWS:
            raise ExportTooLargeError(page.total, limit=MAX_EXPORT_ROWS)

        user_id = movement_filter.user_id

        return TransactionExport(
            rows=list(page.transactions),
            account_names={
                str(account.id.value): account.name
                for account in self._accounts.list_by_user(user_id)
            },
            category_labels=(
                {}
                if self._categories is None
                else dict(self._categories.category_labels(user_id=user_id))
            ),
        )
