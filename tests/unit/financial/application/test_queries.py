"""The two answers a client could not build for itself: which merchant a
movement was with, and what a period adds up to.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.ports import MerchantAttribution
from personal_finance.contexts.financial.application.queries import (
    FinancialHistory,
    HistoryQuery,
    ListTransactionsUseCase,
    MovementFilter,
    ReadFinancialHistoryUseCase,
    ReadSpendingTrendUseCase,
    SpendingSummary,
    SpendingTrend,
    SummarizeSpendingUseCase,
    SummaryGrouping,
    SummaryOrder,
    SummaryQuery,
    TransactionQuery,
    TransactionSort,
    TransferView,
    TrendDimension,
    TrendInterval,
    TrendQuery,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
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


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")

# Bogotá is UTC-5, so these two are the same local day and the same month
# while the second one is already the next day in UTC.
AUGUST_MIDDAY = 1_787_500_000  # 2026-08-23 15:46 UTC / 10:46 Bogotá, a Sunday
AUGUST_LAST_NIGHT = 1_788_224_400  # 2026-09-01 01:00 UTC / 2026-08-31 20:00 Bogotá
JULY_MIDDAY = 1_784_900_000  # 2026-07-22 UTC

SEPTEMBER_START = 1_788_238_800  # 2026-09-01 00:00 Bogotá


class InMemoryLedger:
    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        del balance_delta
        self.rows[transaction.id.value] = transaction

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = transaction

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row if row is not None and row.user_id == user_id else None

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in self.list_unassigned(user_id)
            if row.account_fingerprint == fingerprint
        ]

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id == account_id
        ]

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id is None
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]


class InMemoryAccounts:
    def __init__(self) -> None:
        self.by_id: dict[AccountId, Account] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(account_id)

        return account if account is not None and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        return next(
            (
                account
                for account in self.by_id.values()
                if account.user_id == user_id and account.matches(fingerprint)
            ),
            None,
        )

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self.by_id[account.id] = account

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class FakeDirectory:
    """Answers what merchant would, by exact counterparty text."""

    def __init__(self, known: Mapping[str, MerchantAttribution]) -> None:
        self._known = known
        self.calls = 0

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id
        self.calls += 1

        return {
            counterparty: self._known[counterparty]
            for counterparty in counterparties
            if counterparty in self._known
        }

    def categories(self) -> frozenset[str]:
        return frozenset({"groceries", "transport", "subscriptions", "uncategorized"})


ARA = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Ara",
    category="groceries",
    needs_review=False,
)
UBER = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000002",
    display_name="Uber",
    category="transport",
    needs_review=True,
)


@pytest.fixture
def ledger() -> InMemoryLedger:
    return InMemoryLedger()


@pytest.fixture
def accounts() -> InMemoryAccounts:
    return InMemoryAccounts()


@pytest.fixture
def directory() -> FakeDirectory:
    return FakeDirectory({"TIENDAS ARA 123": ARA, "UBER TRIP": UBER})


def _spend(
    ledger: InMemoryLedger,
    *,
    counterparty: str,
    amount: str = "50000",
    when: int = AUGUST_MIDDAY,
    direction: MovementDirection = MovementDirection.OUTGOING,
    currency: Currency = Currency.COP,
    account_id: AccountId | None = None,
) -> Transaction:
    movement = Transaction.enter_manually(
        user_id=USER_ID,
        direction=direction,
        amount=Money(amount=Decimal(amount), currency=currency),
        occurred_at=PosixTime.from_epoch_seconds(when),
        counterparty=counterparty,
        account_id=account_id,
    )
    ledger.save(movement)

    return movement


def _declare(accounts: InMemoryAccounts, name: str) -> Account:
    account = Account.open(
        user_id=USER_ID,
        name=name,
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(JULY_MIDDAY),
    )
    accounts.save(account)

    return account


def _filter(**overrides: object) -> MovementFilter:
    return MovementFilter(user_id=USER_ID, **overrides)  # type: ignore[arg-type]


# ------------------------------------------------------- movement ↔ merchant


def test_a_movement_carries_the_merchant_behind_its_counterparty_text(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter()),
    )

    assert page.transactions[0].merchant == ARA


def test_a_counterparty_no_merchant_owns_yet_simply_reads_without_one(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    # Its sighting may still be on merchant's queue. The movement is complete
    # either way — an attribution is an enrichment, not a precondition.
    _spend(ledger, counterparty="PAGO NOMINA")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter()),
    )

    assert page.transactions[0].merchant is None
    assert page.total == 1


def test_movements_read_the_same_when_no_directory_is_wired(
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(filter=_filter()),
    )

    assert page.total == 1
    assert page.transactions[0].merchant is None


def test_filtering_by_merchant_returns_only_that_merchants_movements(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")
    _spend(ledger, counterparty="UBER TRIP")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter(merchant_id=ARA.merchant_id)),
    )

    assert page.total == 1
    assert page.transactions[0].transaction.counterparty == "TIENDAS ARA 123"


def test_filtering_by_category_groups_every_spelling_under_it(
    ledger: InMemoryLedger,
) -> None:
    directory = FakeDirectory({"ARA 12": ARA, "TIENDAS ARA 99": ARA, "UBER": UBER})
    _spend(ledger, counterparty="ARA 12")
    _spend(ledger, counterparty="TIENDAS ARA 99")
    _spend(ledger, counterparty="UBER")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter(category="groceries")),
    )

    assert page.total == 2


def test_a_movement_with_no_merchant_matches_neither_merchant_nor_category(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    # Unknown is not "uncategorized": reporting it under a category would put
    # spending in a bucket Merchant never placed it in.
    _spend(ledger, counterparty="PAGO NOMINA")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter(category="uncategorized")),
    )

    assert page.total == 0


def test_the_total_counts_what_the_merchant_filter_kept_not_the_whole_ledger(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    # The filter has to run before the page is cut, or paging walks off the end.
    for index in range(5):
        _spend(ledger, counterparty="UBER TRIP", when=AUGUST_MIDDAY + index)

    _spend(ledger, counterparty="TIENDAS ARA 123")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter(merchant_id=UBER.merchant_id), limit=2),
    )

    assert page.total == 5
    assert len(page.transactions) == 2


def test_a_page_of_movements_asks_the_directory_once(
    ledger: InMemoryLedger,
    directory: FakeDirectory,
) -> None:
    for index in range(20):
        _spend(ledger, counterparty="TIENDAS ARA 123", when=AUGUST_MIDDAY + index)

    ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(filter=_filter()),
    )

    assert directory.calls == 1


# ------------------------------------------------------------- the period


def test_movements_can_be_narrowed_to_a_period(
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="JULY", when=JULY_MIDDAY)
    _spend(ledger, counterparty="AUGUST", when=AUGUST_MIDDAY)

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(
            filter=_filter(
                since=PosixTime.from_epoch_seconds(AUGUST_MIDDAY - 1),
            ),
        ),
    )

    assert page.total == 1
    assert page.transactions[0].transaction.counterparty == "AUGUST"


def test_the_period_is_half_open_so_two_months_never_share_a_movement(
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="ON THE BOUNDARY", when=SEPTEMBER_START)

    august = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(
            filter=_filter(until=PosixTime.from_epoch_seconds(SEPTEMBER_START)),
        ),
    )
    september = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(
            filter=_filter(since=PosixTime.from_epoch_seconds(SEPTEMBER_START)),
        ),
    )

    assert august.total == 0
    assert september.total == 1


# ------------------------------------------------------------- aggregates


def test_a_summary_totals_what_came_in_and_what_went_out(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="50000")
    _spend(ledger, counterparty="UBER TRIP", amount="20000")
    _spend(
        ledger,
        counterparty="NOMINA",
        amount="3000000",
        direction=MovementDirection.INCOMING,
    )

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter()),
    )
    figure = summary.totals[0]

    assert figure.currency is Currency.COP
    assert figure.outgoing == Decimal("70000")
    assert figure.incoming == Decimal("3000000")
    assert figure.net == Decimal("2930000")
    assert figure.movements == 3


def test_two_currencies_are_never_added_together(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="LOCAL", amount="50000")
    _spend(ledger, counterparty="ABROAD", amount="30", currency=Currency.USD)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter()),
    )

    assert [(figure.currency, figure.outgoing) for figure in summary.totals] == [
        (Currency.COP, Decimal("50000")),
        (Currency.USD, Decimal("30")),
    ]


def test_a_late_evening_purchase_falls_in_the_month_it_was_made_locally(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    # 8pm on the 31st in Bogotá is already the 1st in UTC. Grouping in UTC
    # would move somebody's spending into the following month.
    _spend(ledger, counterparty="LATE", when=AUGUST_LAST_NIGHT)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.MONTH),
    )

    assert [group.key for group in summary.groups] == ["2026-08"]


def test_months_come_back_newest_first(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="JULY", when=JULY_MIDDAY)
    _spend(ledger, counterparty="AUGUST", when=AUGUST_MIDDAY)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter()),
    )

    assert [group.key for group in summary.groups] == ["2026-08", "2026-07"]


def test_spending_can_be_broken_down_by_category(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="50000")
    _spend(ledger, counterparty="UBER TRIP", amount="20000")

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.CATEGORY),
    )

    assert {group.key for group in summary.groups} == {"groceries", "transport"}


def test_a_merchant_breakdown_names_the_merchant_rather_than_the_bank_text(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    directory = FakeDirectory({"ARA 12": ARA, "TIENDAS ARA 99": ARA})
    _spend(ledger, counterparty="ARA 12", amount="10000")
    _spend(ledger, counterparty="TIENDAS ARA 99", amount="15000")

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.MERCHANT),
    )

    assert len(summary.groups) == 1
    assert summary.groups[0].label == "Ara"
    assert summary.groups[0].totals[0].outgoing == Decimal("25000")


def test_movements_no_merchant_owns_get_their_own_bucket_rather_than_vanishing(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    # Without it the groups would stop adding up to the total, which is worse
    # than admitting a bucket is unknown.
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="50000")
    _spend(ledger, counterparty="PAGO NOMINA", amount="10000")

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.CATEGORY),
    )
    unknown = next(group for group in summary.groups if group.key is None)

    assert unknown.totals[0].outgoing == Decimal("10000")
    assert sum(group.totals[0].outgoing for group in summary.groups) == (
        summary.totals[0].outgoing
    )


def test_an_account_breakdown_calls_each_account_by_the_name_its_owner_gave(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    account = _declare(accounts, "Ahorros Bancolombia")
    _spend(ledger, counterparty="TIENDAS ARA 123", account_id=account.id)
    _spend(ledger, counterparty="PAGO NOMINA")

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.ACCOUNT),
    )
    labels = {group.key: group.label for group in summary.groups}

    assert labels[str(account.id.value)] == "Ahorros Bancolombia"
    # Somebody who declared no accounts sees only this bucket, which is the
    # ordinary state and a complete answer.
    assert labels[None] == "Unassigned"


def test_a_summary_by_month_never_reads_the_merchant_list(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")

    SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(SummaryQuery(filter=_filter(), group_by=SummaryGrouping.MONTH))

    assert directory.calls == 0


def test_a_summary_takes_the_same_filters_as_the_list(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    # What makes a bucket openable: repeat the query against /transactions
    # with the bucket's key and the movements behind it come back.
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="50000")
    _spend(ledger, counterparty="UBER TRIP", amount="20000")

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(
        SummaryQuery(
            filter=_filter(merchant_id=ARA.merchant_id),
            group_by=SummaryGrouping.MONTH,
        ),
    )

    assert summary.totals[0].outgoing == Decimal("50000")


def test_an_unknown_timezone_is_refused_rather_than_quietly_read_as_utc(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")

    with pytest.raises(ValueError, match="timezone"):
        SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
            SummaryQuery(filter=_filter(), timezone="Mars/Olympus"),
        )


def test_somebody_with_no_movements_gets_an_empty_but_valid_summary(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=_filter()),
    )

    assert summary.totals == []
    assert summary.groups == []


# ------------------------------------------------------------------- history

# 2026-08-15 12:00 Bogotá. Halfway through August, which is what makes the
# comparison window a real one rather than a whole month.
AUGUST_FIFTEENTH = 1_786_813_200
JULY_FIFTH = 1_783_270_800
JULY_TWENTY_FIFTH = 1_784_998_800
AUGUST_FIFTH = 1_785_949_200
# 2026-03-31 12:00 Bogotá — the day of the month February cannot answer.
MARCH_THIRTY_FIRST = 1_774_976_400


def _history(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    *,
    at: int = AUGUST_FIFTEENTH,
    months: int = 3,
) -> FinancialHistory:
    return ReadFinancialHistoryUseCase(ledger=ledger, accounts=accounts).execute(
        HistoryQuery(
            user_id=USER_ID,
            months=months,
            now=PosixTime.from_epoch_seconds(at),
        ),
    )


def test_history_returns_a_bucket_per_month_oldest_first(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    history = _history(ledger, accounts, months=3)

    assert [point.key for point in history.months] == ["2026-06", "2026-07", "2026-08"]
    assert [point.partial for point in history.months] == [False, False, True]


def test_the_month_being_lived_is_the_only_partial_one(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    history = _history(ledger, accounts, months=2)

    assert history.months[-1].partial is True
    assert history.months[-1].key == "2026-08"


def test_a_month_totals_only_its_own_movements(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="JULIO", amount="30000", when=JULY_FIFTH)
    _spend(ledger, counterparty="AGOSTO", amount="50000", when=AUGUST_FIFTH)

    history = _history(ledger, accounts, months=2)
    july, august = history.months

    assert july.totals[0].outgoing == Decimal("30000")
    assert august.totals[0].outgoing == Decimal("50000")


def test_net_worth_is_replayed_to_each_month_end(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """The point of the whole endpoint: a balance for a month that has passed,
    reconstructed rather than stored.
    """
    account = _declare(accounts, "Ahorros")
    _spend(
        ledger,
        counterparty="NOMINA",
        amount="100000",
        when=JULY_MIDDAY,
        direction=MovementDirection.INCOMING,
        account_id=account.id,
    )
    _spend(
        ledger,
        counterparty="MERCADO",
        amount="40000",
        when=AUGUST_FIFTH,
        account_id=account.id,
    )

    history = _history(ledger, accounts, months=2)
    july, august = history.months

    # July closed up 100.000; August has spent 40.000 of it back.
    assert july.net_worth[0].total == Decimal("100000")
    assert august.net_worth[0].total == Decimal("60000")


def test_a_movement_no_account_claimed_moves_no_balance(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _declare(accounts, "Ahorros")
    _spend(ledger, counterparty="SIN CUENTA", amount="70000", when=JULY_MIDDAY)

    history = _history(ledger, accounts, months=2)

    assert history.months[0].totals[0].outgoing == Decimal("70000")
    assert history.months[0].net_worth[0].total == Decimal(0)


def test_the_comparison_covers_the_same_days_of_the_previous_month(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Standing on the 15th, July's window has to stop on the 15th too.

    Against the whole of July the answer would be "spending is down", every
    month, until the very last day of it.
    """
    _spend(ledger, counterparty="JULIO 5", amount="10000", when=JULY_FIFTH)
    _spend(ledger, counterparty="JULIO 25", amount="90000", when=JULY_TWENTY_FIFTH)
    _spend(ledger, counterparty="AGOSTO 5", amount="20000", when=AUGUST_FIFTH)

    comparison = _history(ledger, accounts).comparison

    assert comparison.key == "2026-08"
    assert comparison.previous_key == "2026-07"
    assert comparison.clamped is False
    assert comparison.totals[0].outgoing == Decimal("20000")
    # Only the 5th; the 25th is past the 15th and stays out.
    assert comparison.previous_totals[0].outgoing == Decimal("10000")


def test_a_month_the_previous_one_is_too_short_for_is_flagged(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    comparison = _history(ledger, accounts, at=MARCH_THIRTY_FIRST).comparison

    assert comparison.key == "2026-03"
    assert comparison.previous_key == "2026-02"
    assert comparison.clamped is True
    # Clamped to the whole of February rather than running into March.
    assert comparison.previous_through == comparison.starts_at


def test_history_is_capped_rather_than_refused(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    assert len(_history(ledger, accounts, months=999).months) == 36
    assert len(_history(ledger, accounts, months=0).months) == 1


AUGUST_START = 1_785_560_400  # 2026-08-01 00:00 Bogotá
AUGUST_TWENTIETH = 1_787_245_200  # 2026-08-20 12:00 Bogotá


def test_a_movement_on_a_boundary_opens_the_month_it_starts(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """A date-only entry lands on local midnight, which is a boundary.

    Counting it in the closing net worth of the month before would put money
    in July that a client can see was spent in August — the totals and the
    balance would disagree about the same row.
    """
    account = _declare(accounts, "Ahorros")
    _spend(
        ledger,
        counterparty="MEDIANOCHE",
        amount="25000",
        when=AUGUST_START,
        direction=MovementDirection.INCOMING,
        account_id=account.id,
    )

    history = _history(ledger, accounts, months=2)
    july, august = history.months

    assert july.totals == []
    assert july.net_worth[0].total == Decimal(0)
    assert august.totals[0].incoming == Decimal("25000")
    assert august.net_worth[0].total == Decimal("25000")


def test_the_lived_month_stops_at_now_like_the_comparison_does(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Nothing validates `occurred_at`, so a date later this month is ordinary.

    The partial bucket and the comparison carry the same key, so a client puts
    them side by side. Totalling the whole calendar month in one and stopping
    at now in the other makes the same month report two different figures.
    """
    account = _declare(accounts, "Ahorros")
    _spend(
        ledger,
        counterparty="HOY",
        amount="10000",
        when=AUGUST_FIFTH,
        account_id=account.id,
    )
    _spend(
        ledger,
        counterparty="MAS TARDE",
        amount="70000",
        when=AUGUST_TWENTIETH,
        account_id=account.id,
    )

    history = _history(ledger, accounts)
    lived = history.months[-1]

    assert lived.key == history.comparison.key
    assert lived.totals[0].outgoing == Decimal("10000")
    assert lived.totals == list(history.comparison.totals)
    assert lived.net_worth == list(history.comparison.net_worth)


# ------------------------------------------------------------- traslados


def _pay_a_card(
    ledger: InMemoryLedger,
    *,
    amount: str = "3540258",
    when: int = AUGUST_MIDDAY,
) -> tuple[Transaction, Transaction]:
    """Both sides of a card payment, in the ledger the way the worker leaves
    them."""
    source, destination = Transaction.as_transfer(
        user_id=USER_ID,
        bank="bancolombia",
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(when),
        source_instrument_kind=InstrumentKind.ACCOUNT.value,
        source_last_four="5261",
        destination_instrument_kind=InstrumentKind.CREDIT_CARD.value,
        destination_last_four="7653",
    )
    ledger.save(source)
    ledger.save(destination)

    return source, destination


def test_a_summary_leaves_transfers_out_of_what_was_spent(
    ledger: InMemoryLedger,
) -> None:
    """The bug this whole path exists to prevent: a card payment reporting as
    the month's largest expense, and again as income on the card."""
    _spend(ledger, counterparty="TIENDAS ARA", amount="50000")
    _pay_a_card(ledger)

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=InMemoryAccounts(),
    ).execute(
        SummaryQuery(
            filter=_filter(transfers=TransferView.EXCLUDE),
            group_by=SummaryGrouping.MONTH,
        ),
    )

    assert summary.totals[0].outgoing == Decimal("50000")
    assert summary.totals[0].incoming == Decimal("0")
    assert summary.totals[0].movements == 1


def test_a_summary_can_be_asked_for_the_transfers_alone(
    ledger: InMemoryLedger,
) -> None:
    """The opposite question: what did I move between my own accounts."""
    _spend(ledger, counterparty="TIENDAS ARA", amount="50000")
    _pay_a_card(ledger)

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=InMemoryAccounts(),
    ).execute(
        SummaryQuery(
            filter=_filter(transfers=TransferView.ONLY),
            group_by=SummaryGrouping.MONTH,
        ),
    )

    assert summary.totals[0].outgoing == Decimal("3540258")
    assert summary.totals[0].incoming == Decimal("3540258")
    assert summary.totals[0].movements == 2


def test_the_list_shows_both_sides_of_a_transfer_by_default(
    ledger: InMemoryLedger,
) -> None:
    """They explain why an account fell, so hiding them would leave somebody
    looking for money that plainly left."""
    _spend(ledger, counterparty="TIENDAS ARA", amount="50000")
    _pay_a_card(ledger)

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(filter=_filter()),
    )

    assert page.total == 3
    assert sum(1 for entry in page.transactions if entry.transaction.is_transfer) == 2


def test_the_list_can_hide_transfers_so_it_matches_a_total_beside_it(
    ledger: InMemoryLedger,
) -> None:
    _pay_a_card(ledger)
    _spend(ledger, counterparty="TIENDAS ARA", amount="50000")

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(filter=_filter(transfers=TransferView.EXCLUDE)),
    )

    assert page.total == 1
    assert page.transactions[0].transaction.counterparty == "TIENDAS ARA"


def test_history_keeps_transfers_out_of_the_monthly_totals(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="AGOSTO", amount="50000", when=AUGUST_FIFTH)
    _pay_a_card(ledger, when=AUGUST_FIFTH)

    history = _history(ledger, accounts, months=1)

    assert history.months[-1].totals[0].outgoing == Decimal("50000")
    assert history.months[-1].totals[0].incoming == Decimal("0")


def test_history_still_replays_transfers_into_the_balances(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Totals and balances read the same ledger for different questions: a
    card payment is not spending, and it really did move both balances."""
    account = Account.open(
        user_id=USER_ID,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(JULY_FIFTH),
        bank="bancolombia",
        instrument_kind=InstrumentKind.ACCOUNT,
        last_four="5261",
        opening_balance=Money(amount=Decimal("5000000"), currency=Currency.COP),
    )
    accounts.save(account)
    source, _ = _pay_a_card(ledger, when=AUGUST_FIFTH)
    source.assign_to(account.id)
    ledger.save(source)

    history = _history(ledger, accounts, months=1)

    assert history.months[-1].net_worth[0].total == Decimal("1459742")


# --------------------------------------------------------------- reporting


# Bogotá never moves, so these are exact.
JULY_START = 1_782_882_000  # 2026-07-01 00:00 Bogotá
AUGUST_FIRST_MONDAY = 1_785_769_200  # 2026-08-03 Mon 10:00 Bogotá, week 2026-W32
AUGUST_SECOND_MONDAY = 1_786_374_000  # 2026-08-10 Mon 10:00 Bogotá, week 2026-W33
AUGUST_SATURDAY = 1_787_410_800  # 2026-08-22 Sat 10:00 Bogotá
JULY_MONDAY = 1_783_350_000  # 2026-07-06 Mon 10:00 Bogotá, the window before August
SEPTEMBER_MIDDAY = 1_788_368_400  # 2026-09-02 12:00 Bogotá
NEW_YEARS_DAY = 1_798_815_600  # 2027-01-01 Bogotá, which is ISO week 2026-W53


def _summary(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory | None = None,
    **overrides: object,
) -> SpendingSummary:
    return SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(SummaryQuery(**overrides))  # type: ignore[arg-type]


def test_spending_can_be_broken_down_by_day(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="UNO", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="DOS", when=AUGUST_SECOND_MONDAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(),
        group_by=SummaryGrouping.DAY,
    )

    # Newest first, like every other stretch of time here.
    assert [group.key for group in summary.groups] == ["2026-08-10", "2026-08-03"]


def test_a_late_evening_purchase_falls_in_the_day_it_was_made_locally(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    # 2026-09-01 01:00 UTC, which is still the 31st of August in Bogotá.
    _spend(ledger, counterparty="TIENDA", when=AUGUST_LAST_NIGHT)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(),
        group_by=SummaryGrouping.DAY,
    )

    assert [group.key for group in summary.groups] == ["2026-08-31"]


def test_spending_can_be_broken_down_by_iso_week(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="UNO", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="DOS", when=AUGUST_SECOND_MONDAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(),
        group_by=SummaryGrouping.WEEK,
    )

    assert [group.key for group in summary.groups] == ["2026-W33", "2026-W32"]


def test_a_week_is_named_by_its_iso_year_not_its_calendar_one(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """The 1st of January 2027 is a Friday, so its week began in December."""
    _spend(ledger, counterparty="TIENDA", when=NEW_YEARS_DAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(),
        group_by=SummaryGrouping.WEEK,
    )

    assert [group.key for group in summary.groups] == ["2026-W53"]


def test_a_weekday_breakdown_gathers_every_monday_into_one_bucket(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="UNO", when=AUGUST_FIRST_MONDAY, amount="1000")
    _spend(ledger, counterparty="DOS", when=AUGUST_SECOND_MONDAY, amount="2000")
    _spend(ledger, counterparty="TRES", when=AUGUST_SATURDAY, amount="4000")

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(),
        group_by=SummaryGrouping.WEEKDAY,
    )

    # Monday to Sunday, not busiest first: a week is read in order.
    assert [(group.key, group.label) for group in summary.groups] == [
        ("1", "Monday"),
        ("6", "Saturday"),
    ]
    assert summary.groups[0].movements == 2
    assert summary.groups[0].totals[0].outgoing == Decimal("3000")


def test_a_summary_can_be_pinned_to_one_currency(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="LOCAL", amount="50000", currency=Currency.COP)
    _spend(ledger, counterparty="ABROAD", amount="20", currency=Currency.USD)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(currency=Currency.COP),
    )

    assert [figure.currency for figure in summary.totals] == [Currency.COP]
    assert summary.totals[0].movements == 1


def test_buckets_can_be_ranked_by_money_rather_than_by_frequency(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    # Three small rides against one large grocery run: the ranking these two
    # orders produce is the opposite of each other, which is the point.
    for _ in range(3):
        _spend(ledger, counterparty="UBER TRIP", amount="10000")
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="500000")

    by_count = _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(),
        group_by=SummaryGrouping.CATEGORY,
    )
    by_money = _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(currency=Currency.COP),
        group_by=SummaryGrouping.CATEGORY,
        order=SummaryOrder.AMOUNT,
    )

    assert [group.key for group in by_count.groups] == ["transport", "groceries"]
    assert [group.key for group in by_money.groups] == ["groceries", "transport"]


def test_ranking_by_money_without_a_currency_is_refused(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Two currencies have no rate here, so the ranking would be deciding that
    whichever unit has larger numbers is the larger amount.
    """
    with pytest.raises(ValueError, match="needs a currency"):
        SummaryQuery(filter=_filter(), order=SummaryOrder.AMOUNT)


def test_the_tail_of_a_breakdown_can_be_folded_into_a_remainder(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="100000")
    _spend(ledger, counterparty="UBER TRIP", amount="30000")
    _spend(ledger, counterparty="NOBODY KNOWS", amount="7000")

    summary = _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(currency=Currency.COP),
        group_by=SummaryGrouping.CATEGORY,
        order=SummaryOrder.AMOUNT,
        top=1,
    )

    assert [group.key for group in summary.groups] == ["groceries"]
    assert summary.folded == 2
    assert summary.others is not None
    # No key: unlike a real bucket, the remainder cannot be reopened as the
    # list of movements behind it, and must not look as though it can.
    assert summary.others.key is None
    assert summary.others.movements == 2
    assert summary.others.totals[0].outgoing == Decimal("37000")
    # The parts still add up to the whole, which is what folding must not break.
    kept = sum(group.totals[0].outgoing for group in summary.groups)
    assert kept + summary.others.totals[0].outgoing == summary.totals[0].outgoing


def test_nothing_is_folded_when_the_breakdown_already_fits(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123")

    summary = _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(),
        group_by=SummaryGrouping.CATEGORY,
        top=5,
    )

    assert summary.others is None
    assert summary.folded == 0


def test_folding_a_stretch_of_time_is_refused(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """A range is narrowed with `since`/`until`. Folding the oldest days into
    a remainder answers no question anybody has.
    """
    with pytest.raises(ValueError, match="does not apply"):
        SummaryQuery(filter=_filter(), group_by=SummaryGrouping.MONTH, top=3)


def test_a_summary_can_report_the_window_before_it(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    _spend(ledger, counterparty="TIENDA", amount="80000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="TIENDA", amount="50000", when=JULY_MIDDAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
        group_by=SummaryGrouping.CATEGORY,
        compare=True,
    )

    assert summary.totals[0].outgoing == Decimal("80000")
    assert summary.previous_totals is not None
    assert summary.previous_totals[0].outgoing == Decimal("50000")
    # The window of equal length butted right up against this one.
    assert summary.previous_since is not None
    assert summary.previous_since.as_epoch_seconds() == JULY_START
    assert summary.previous_until is not None
    assert summary.previous_until.as_epoch_seconds() == AUGUST_START


def test_a_category_that_stopped_appears_at_zero_rather_than_vanishing(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    """Money that used to go somewhere and no longer does is exactly what a
    report exists to surface. Dropping the bucket hides the fall.
    """
    _spend(ledger, counterparty="UBER TRIP", amount="30000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="90000", when=JULY_MIDDAY)

    summary = _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
        group_by=SummaryGrouping.CATEGORY,
        compare=True,
    )
    groceries = next(group for group in summary.groups if group.key == "groceries")

    assert groceries.movements == 0
    assert groceries.totals == []
    assert groceries.previous_totals is not None
    assert groceries.previous_totals[0].outgoing == Decimal("90000")


def test_comparing_two_stretches_of_time_reports_the_period_and_not_each_bucket(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """`2026-08` against `2026-07` is two different months, not one month
    twice, so there is no previous self to put beside each bucket.
    """
    _spend(ledger, counterparty="TIENDA", amount="80000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="TIENDA", amount="50000", when=JULY_MIDDAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
        group_by=SummaryGrouping.MONTH,
        compare=True,
    )

    assert [group.key for group in summary.groups] == ["2026-08"]
    assert summary.groups[0].previous_totals is None
    assert summary.previous_totals is not None
    assert summary.previous_totals[0].outgoing == Decimal("50000")


def test_comparing_without_a_window_is_refused(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    with pytest.raises(ValueError, match="needs `since` and `until`"):
        SummaryQuery(filter=_filter(), compare=True)


def test_comparing_still_asks_the_merchant_list_only_once(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    """Two windows are one question. Reading the same partition twice to
    answer it would double the cost of every report that shows a delta.
    """
    _spend(ledger, counterparty="UBER TRIP", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="TIENDAS ARA 123", when=JULY_MIDDAY)

    _summary(
        ledger,
        accounts,
        directory,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
        group_by=SummaryGrouping.CATEGORY,
        compare=True,
    )

    assert directory.calls == 1


# ------------------------------------------------------------------ trends


def _trend(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory | None = None,
    **overrides: object,
) -> SpendingTrend:
    overrides.setdefault("now", PosixTime.from_epoch_seconds(SEPTEMBER_MIDDAY))

    return ReadSpendingTrendUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(TrendQuery(**overrides))  # type: ignore[arg-type]


def test_a_trend_walks_back_whole_periods_from_now(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    trend = _trend(ledger, accounts, filter=_filter(), periods=3)

    assert [bucket.key for bucket in trend.buckets] == ["2026-07", "2026-08", "2026-09"]
    assert trend.starts_at.as_epoch_seconds() == JULY_START


def test_a_period_nothing_happened_in_is_a_zero_and_not_a_gap(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    """The whole reason this endpoint exists: a client zips points against
    buckets by index and never has to notice a missing month.
    """
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="10000", when=JULY_MIDDAY)
    _spend(
        ledger,
        counterparty="TIENDAS ARA 123",
        amount="20000",
        when=SEPTEMBER_MIDDAY - 3600,
    )

    trend = _trend(ledger, accounts, directory, filter=_filter(), periods=3)
    groceries = trend.series[0]

    assert [bucket.key for bucket in trend.buckets] == ["2026-07", "2026-08", "2026-09"]
    assert [point.bucket for point in groceries.points] == [
        bucket.key for bucket in trend.buckets
    ]
    # August is present and empty, which is not the same as absent.
    assert groceries.points[1].totals == []
    assert groceries.points[0].totals[0].outgoing == Decimal("10000")
    assert groceries.points[2].totals[0].outgoing == Decimal("20000")


def test_only_the_period_being_lived_is_partial_and_it_ends_now(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    trend = _trend(ledger, accounts, filter=_filter(), periods=2)

    assert [bucket.partial for bucket in trend.buckets] == [False, True]
    assert trend.buckets[-1].ends_at.as_epoch_seconds() == SEPTEMBER_MIDDAY
    assert trend.buckets[0].ends_at.as_epoch_seconds() == SEPTEMBER_START


def test_a_trend_is_split_into_one_band_per_category(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="UBER TRIP", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="UBER TRIP", when=AUGUST_SECOND_MONDAY)

    trend = _trend(ledger, accounts, directory, filter=_filter(), periods=2)

    assert [series.key for series in trend.series] == ["transport", "groceries"]
    assert trend.series[0].movements == 2


def test_an_undivided_trend_is_one_band_carrying_both_directions(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """`none` is not redundant with the rest: every point already holds what
    came in and what went out, so one band is the cashflow chart.
    """
    _spend(
        ledger,
        counterparty="NOMINA",
        amount="3000000",
        when=AUGUST_FIRST_MONDAY,
        direction=MovementDirection.INCOMING,
    )
    _spend(ledger, counterparty="TIENDA", amount="80000", when=AUGUST_FIRST_MONDAY)

    trend = _trend(
        ledger,
        accounts,
        filter=_filter(),
        dimension=TrendDimension.NONE,
        periods=2,
    )
    august = trend.series[0].points[0]

    assert len(trend.series) == 1
    assert trend.series[0].label == "Total"
    assert august.totals[0].incoming == Decimal("3000000")
    assert august.totals[0].outgoing == Decimal("80000")


def test_the_bands_a_chart_cannot_stack_are_folded_into_a_remainder(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(
        ledger,
        counterparty="TIENDAS ARA 123",
        amount="100000",
        when=AUGUST_FIRST_MONDAY,
    )
    _spend(ledger, counterparty="UBER TRIP", amount="30000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="NOBODY KNOWS", amount="7000", when=AUGUST_FIRST_MONDAY)

    trend = _trend(
        ledger,
        accounts,
        directory,
        filter=_filter(currency=Currency.COP),
        order=SummaryOrder.AMOUNT,
        series=1,
        periods=2,
    )

    assert [series.key for series in trend.series] == ["groceries"]
    assert trend.folded == 2
    assert trend.others is not None
    assert trend.others.key is None
    # Folded band and all, the remainder is still dense.
    assert len(trend.others.points) == len(trend.buckets)
    assert trend.others.points[0].totals[0].outgoing == Decimal("37000")


def test_an_explicit_range_is_widened_to_the_periods_it_touches(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Half of August charted beside the whole of September reports a fall
    that did not happen.
    """
    trend = _trend(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_SECOND_MONDAY),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
    )

    assert [bucket.key for bucket in trend.buckets] == ["2026-08"]
    assert trend.starts_at.as_epoch_seconds() == AUGUST_START


def test_a_range_too_wide_to_chart_is_refused_rather_than_answered(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    with pytest.raises(ValueError, match="shorter one"):
        _trend(
            ledger,
            accounts,
            filter=_filter(
                since=PosixTime.from_epoch_seconds(JULY_START - 3 * 365 * 86400),
                until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
            ),
            interval=TrendInterval.DAY,
        )


def test_a_trend_by_month_never_reads_the_merchant_list(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
    directory: FakeDirectory,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", when=AUGUST_FIRST_MONDAY)

    _trend(
        ledger,
        accounts,
        directory,
        filter=_filter(),
        dimension=TrendDimension.NONE,
        periods=2,
    )

    assert directory.calls == 0


# ------------------------------------------------------ biggest movements


def test_movements_can_be_ordered_by_size_rather_than_by_date(
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="SMALL", amount="1000", when=AUGUST_SECOND_MONDAY)
    _spend(ledger, counterparty="LARGE", amount="900000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="MIDDLING", amount="40000", when=AUGUST_SATURDAY)

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(
            filter=_filter(currency=Currency.COP),
            sort=TransactionSort.AMOUNT,
        ),
    )

    assert [entry.transaction.counterparty for entry in page.transactions] == [
        "LARGE",
        "MIDDLING",
        "SMALL",
    ]


def test_ordering_movements_by_size_without_a_currency_is_refused(
    ledger: InMemoryLedger,
) -> None:
    with pytest.raises(ValueError, match="needs a currency"):
        TransactionQuery(filter=_filter(), sort=TransactionSort.AMOUNT)


def test_a_period_the_window_stops_inside_of_says_it_is_partial(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """Cut short by an explicit `to`, not by now. Charting half of a period
    beside whole ones reports a fall that did not happen, so the bucket has to
    admit where it actually stops.
    """
    trend = _trend(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(AUGUST_SATURDAY),
        ),
    )

    assert [bucket.key for bucket in trend.buckets] == ["2026-08"]
    assert trend.buckets[0].partial is True
    assert trend.buckets[0].ends_at.as_epoch_seconds() == AUGUST_SATURDAY


def test_a_period_the_window_covers_whole_is_not_partial(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    trend = _trend(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
    )

    assert [bucket.key for bucket in trend.buckets] == ["2026-08"]
    assert trend.buckets[0].partial is False
    assert trend.buckets[0].ends_at.as_epoch_seconds() == SEPTEMBER_START


def test_periods_are_counted_back_from_the_last_one_the_window_covers(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """`to` is exclusive here as everywhere else, so a `to` landing exactly on
    a boundary opens a month the window does not cover — and counting back
    from that one returns a period fewer than asked for.
    """
    trend = _trend(
        ledger,
        accounts,
        filter=_filter(until=PosixTime.from_epoch_seconds(SEPTEMBER_START)),
        periods=3,
    )

    assert [bucket.key for bucket in trend.buckets] == ["2026-06", "2026-07", "2026-08"]


def test_a_weekday_is_compared_against_itself_because_mondays_come_round_again(
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    """The one temporal grouping whose buckets recur. `2026-08` against
    `2026-07` is two different months; Monday against Monday is not.
    """
    # AUGUST_FIRST_MONDAY is in the current window, JULY_MONDAY in the one
    # before it, and both are Mondays.
    _spend(ledger, counterparty="TIENDA", amount="8000", when=AUGUST_FIRST_MONDAY)
    _spend(ledger, counterparty="TIENDA", amount="5000", when=JULY_MONDAY)

    summary = _summary(
        ledger,
        accounts,
        filter=_filter(
            since=PosixTime.from_epoch_seconds(AUGUST_START),
            until=PosixTime.from_epoch_seconds(SEPTEMBER_START),
        ),
        group_by=SummaryGrouping.WEEKDAY,
        compare=True,
    )
    monday = next(group for group in summary.groups if group.key == "1")

    assert monday.totals[0].outgoing == Decimal("8000")
    assert monday.previous_totals is not None
    assert monday.previous_totals[0].outgoing == Decimal("5000")
