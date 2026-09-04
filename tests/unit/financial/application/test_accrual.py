"""What a month actually does to a debt, end to end through the ledger.

The story every test here tells is the same one: 60 000 000 owed, 2 000 000
paid, and a balance that lands on 58 920 622.87 rather than 58 000 000. What
makes the difference is that the interest and the insurance are **rows** — an
ordinary movement each, written the same way an alert is — so the balance goes
on being the running total of things somebody can read, and a debt that grew
can be pointed at rather than merely noticed.

The second thing under test is that running the accrual is safe: a scheduled
job that fires twice, a retry after a crash, a screen that refreshes. Each
charge is identified by its account and its period, so the second attempt
writes a key the ledger already holds and is refused there.
"""

from collections.abc import Sequence
import copy
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from personal_finance.contexts.financial.application.commands import (
    AccrueFinancingCommand,
    ChargeDraft,
    EnterTransferLegCommand,
    RevalueAccountCommand,
    SetInvestmentTermsCommand,
    SetLoanTermsCommand,
)
from personal_finance.contexts.financial.application.financing import (
    AccrualResult,
    AccrueFinancingUseCase,
    ManageFinancingUseCase,
    NotFinancedError,
    ReadFinancingUseCase,
    RevalueAccountUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    FinancingTermsError,
)
from personal_finance.contexts.financial.domain.financing import (
    ChargeBasis,
    InterestRate,
    RateBasis,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    BalanceSign,
    TransactionOrigin,
    TransferRole,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
DISBURSED_ON = dt.date(2026, 1, 15)
BOGOTA = "America/Bogota"


class FakeAccounts:
    def __init__(self) -> None:
        self.by_id: dict[str, Account] = {}
        self.saves = 0

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(str(account_id.value))

        return account if account and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        return None

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self.saves += 1
        self.by_id[str(account.id.value)] = account

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        del fingerprint
        self.save(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class FakeLedger:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Transaction, Decimal | None]] = {}

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        if transaction.id.value in self.rows:
            return False

        self.rows[transaction.id.value] = (transaction, balance_delta)

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = (transaction, None)

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        del reversals

        for transaction in transactions:
            self.rows.pop(transaction.id.value, None)

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        return []

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row[0] if row and row[0].user_id == user_id else None

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id and movement.account_id == account_id
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id
        ]

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return []


class RacingAccounts(FakeAccounts):
    """Accounts that hand out a copy per read, the way a stored one does.

    Enough to play a race: what a request holds stops being what storage says
    the moment somebody else writes, which is the whole situation a losing
    write has to recognise.
    """

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = super().find(user_id=user_id, account_id=account_id)

        return None if account is None else copy.deepcopy(account)


class RacingLedger(FakeLedger):
    """A ledger that lets somebody else win the first write.

    The row lands and the stored balance moves — by another request identical
    to this one — and the caller is told its key was taken.
    """

    def __init__(self, accounts: FakeAccounts) -> None:
        super().__init__()
        self._accounts = accounts
        self._raced = False

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        if self._raced:
            return super().record(
                transaction=transaction,
                balance_delta=balance_delta,
            )

        self._raced = True
        self.rows[transaction.id.value] = (transaction, balance_delta)
        account_id = transaction.account_id

        if account_id is not None:
            stored = self._accounts.by_id[str(account_id.value)]
            stored.apply(transaction.as_movement())
            stored.pull_events()

        return False


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _mortgage(accounts: FakeAccounts, *, balance: str = "60000000") -> Account:
    account = Account.open(
        user_id=USER,
        name="Hipoteca",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_768_000_000),
        opening_balance=_cop(balance),
        bank="bancolombia",
    )
    account.pull_events()
    accounts.save(account)

    return account


def _life_insurance() -> ChargeDraft:
    return ChargeDraft(
        name="Seguro de vida deudores",
        basis=ChargeBasis.OUTSTANDING_BALANCE,
        rate=Decimal("0.000345"),
    )


def _declare_loan(
    accounts: FakeAccounts,
    account: Account,
    *,
    installment: str | None = "2000000",
    charges: Sequence[ChargeDraft] = (),
    accrue_from: dt.date | None = DISBURSED_ON,
) -> Account:
    return ManageFinancingUseCase(
        accounts=accounts,
        event_publisher=RecordingPublisher(),
    ).set_loan(
        SetLoanTermsCommand(
            user_id=USER,
            account_id=account.id,
            rate=InterestRate(
                value=Decimal("0.1956"),
                basis=RateBasis.EFFECTIVE_ANNUAL,
            ),
            disbursed_on=DISBURSED_ON,
            term_months=60,
            statement_day=15,
            payment_day=20,
            principal=Decimal("60000000"),
            installment=None if installment is None else Decimal(installment),
            installment_covers_charges=True,
            charges=charges,
            accrue_from=accrue_from,
        ),
    )


def _accrue(
    accounts: FakeAccounts,
    ledger: FakeLedger,
    *,
    through: dt.date,
    account_id: AccountId | None = None,
) -> Sequence[AccrualResult]:
    return AccrueFinancingUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).execute(
        AccrueFinancingCommand(
            user_id=USER,
            account_id=account_id,
            through=through,
            timezone=BOGOTA,
        ),
    )


# ------------------------------------------------------------------ posting


def test_a_closed_month_posts_its_interest_and_its_insurance_as_movements() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, charges=[_life_insurance()])

    result = _accrue(accounts, ledger, through=dt.date(2026, 2, 20))[0]

    assert [row.counterparty for row in result.posted] == [
        "Intereses",
        "Seguro de vida deudores",
    ]
    assert [str(row.amount.amount) for row in result.posted] == [
        "899922.87",
        "20700.00",
    ]
    # And every one of them is a row somebody can read, on this account.
    assert all(row.origin is TransactionOrigin.ACCRUAL for row in result.posted)
    assert all(row.account_id == account.id for row in result.posted)


def test_the_balance_grows_by_exactly_what_the_month_charged() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, charges=[_life_insurance()])

    result = _accrue(accounts, ledger, through=dt.date(2026, 2, 20))[0]

    # 60 000 000 + 899 922.87 + 20 700.
    assert result.account.balance.amount.amount == Decimal("60920622.87")
    assert result.account.balance.sign is BalanceSign.POSITIVE


def test_paying_two_million_leaves_the_debt_at_fifty_eight_nine_twenty() -> None:
    """The sentence this whole feature exists for.

    A month closes and charges 920 622.87. The instalment is then paid — as a
    transfer leg, because paying a loan is money moving between two of the
    owner's own balances and not an expense — and what is left is
    58 920 622.87. A ledger that only knew about the payment would say
    58 000 000, and would go on being wrong for twenty years.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, charges=[_life_insurance()])

    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))
    ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER,
            role=TransferRole.DESTINATION,
            amount=_cop("2000000"),
            occurred_at=PosixTime.from_datetime(
                dt.datetime(2026, 2, 20, 10, 0, tzinfo=dt.UTC),
            ),
            counterparty="Cuenta de ahorros",
            account_id=account.id,
        ),
    )

    assert accounts.by_id[str(account.id.value)].balance.amount.amount == Decimal(
        "58920622.87",
    )


def test_interest_compounds_on_the_month_before_it() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)

    result = _accrue(accounts, ledger, through=dt.date(2026, 3, 20))[0]
    charged = [str(row.amount.amount) for row in result.posted]

    # The second month is charged on 60 899 922.87, not on 60 000 000 again.
    assert charged == ["899922.87", "913420.55"]


def test_a_payment_inside_a_period_lowers_the_interest_of_the_next_one() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)
    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER,
            role=TransferRole.DESTINATION,
            amount=_cop("2000000"),
            occurred_at=PosixTime.from_datetime(
                dt.datetime(2026, 2, 20, 10, 0, tzinfo=dt.UTC),
            ),
            counterparty="Cuenta de ahorros",
            account_id=account.id,
        ),
    )
    result = _accrue(accounts, ledger, through=dt.date(2026, 3, 20))[0]

    # Charged on 58 899 922.87 — the balance the cut closed with, after the
    # instalment landed.
    assert [str(row.amount.amount) for row in result.posted] == ["883423.12"]


# -------------------------------------------------------------- idempotency


def test_running_the_accrual_twice_charges_the_month_once() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, charges=[_life_insurance()])

    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))
    before = accounts.by_id[str(account.id.value)].balance.signed_amount
    again = _accrue(accounts, ledger, through=dt.date(2026, 2, 20))[0]

    assert list(again.posted) == []
    assert accounts.by_id[str(account.id.value)].balance.signed_amount == before


def test_a_run_that_wrote_but_never_advanced_the_cursor_repeats_nothing() -> None:
    """The crash this ordering exists to survive.

    The rows go out first and the mark moves last, so a run that died between
    them leaves periods already written and a cursor that has not moved. The
    next run walks them again and every row is refused by its own key — which
    is why the cursor is an optimization and never the authority.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, charges=[_life_insurance()])
    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    stored = accounts.by_id[str(account.id.value)]
    balance = stored.balance.signed_amount
    stored.accrued_through = DISBURSED_ON

    repeated = _accrue(accounts, ledger, through=dt.date(2026, 2, 20))[0]

    assert list(repeated.posted) == []
    assert repeated.skipped == 2
    assert accounts.by_id[str(account.id.value)].balance.signed_amount == balance


def test_the_cursor_moves_to_the_last_period_it_closed() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)

    result = _accrue(accounts, ledger, through=dt.date(2026, 3, 20))[0]

    assert result.accrued_through == dt.date(2026, 3, 15)


def test_a_period_still_running_charges_nothing_yet() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)

    result = _accrue(accounts, ledger, through=dt.date(2026, 2, 14))[0]

    assert list(result.posted) == []
    assert result.reason is not None


# ------------------------------------------------------------ what is skipped


def test_an_account_with_no_terms_is_left_alone_with_a_reason() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)

    result = _accrue(
        accounts,
        ledger,
        through=dt.date(2026, 3, 20),
        account_id=account.id,
    )[0]

    assert list(result.posted) == []
    assert result.reason == "the account has no terms"


def test_a_settled_loan_charges_nothing_at_all() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts, balance="0")
    _declare_loan(accounts, account)

    result = _accrue(accounts, ledger, through=dt.date(2026, 3, 20))[0]

    assert list(result.posted) == []


def test_a_closed_account_stops_charging() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)
    account.close(PosixTime.from_epoch_seconds(1_768_100_000))
    accounts.save(account)

    result = _accrue(
        accounts,
        ledger,
        through=dt.date(2026, 3, 20),
        account_id=account.id,
    )[0]

    assert list(result.posted) == []
    assert result.reason == "the account is closed"


def test_a_savings_account_cannot_be_given_loan_terms() -> None:
    accounts = FakeAccounts()
    account = Account.open(
        user_id=USER,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_768_000_000),
    )
    accounts.save(account)

    with pytest.raises(FinancingTermsError):
        _declare_loan(accounts, account)


def test_a_sweep_touches_every_financed_account_and_nothing_else() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    financed = _mortgage(accounts)
    _declare_loan(accounts, financed)
    plain = Account.open(
        user_id=USER,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_768_000_000),
        opening_balance=_cop("5000000"),
    )
    accounts.save(plain)

    results = _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    assert len(results) == 1
    assert results[0].account.id == financed.id


# ------------------------------------------------------ declaring the terms


def test_terms_declared_without_a_start_charge_nothing_for_the_past() -> None:
    """The trap the anchor exists to avoid.

    Somebody declaring a mortgage they have paid for three years states the
    balance their bank shows, and that figure already contains those three
    years of interest. Charging them again would double the debt, so the
    arithmetic starts today unless the owner says otherwise.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    updated = _declare_loan(accounts, account, accrue_from=None)

    # Today where the owner lives, not today in UTC: for five hours every
    # night those are two different days, and a cursor a day ahead would skip
    # a period nobody had been charged for.
    assert updated.accrued_through == dt.datetime.now(tz=ZoneInfo(BOGOTA)).date()

    result = _accrue(accounts, ledger, through=dt.date(2026, 3, 20))[0]

    assert list(result.posted) == []


# ------------------------------------------------------------- what is read


def test_the_payoff_is_the_balance_plus_the_days_nobody_has_been_charged_for() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)
    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER,
        account_id=account.id,
        as_of=dt.date(2026, 2, 25),
        periods=3,
    )

    # Ten days at 1.4999 % a month on 60 899 922.87, prorated 30/360.
    assert view.pending_interest.amount == Decimal("304473.52")
    assert view.payoff is not None
    assert view.payoff.amount == Decimal("61204396.39")
    assert view.next_statement_on == dt.date(2026, 3, 15)
    assert view.next_due_on == dt.date(2026, 3, 20)


def test_the_schedule_starts_from_the_ledger_and_not_from_what_was_borrowed() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts, balance="20000000")
    _declare_loan(accounts, account)

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER,
        account_id=account.id,
        as_of=dt.date(2026, 1, 15),
        periods=2,
    )

    assert view.schedule is not None
    assert view.schedule.payments[0].opening_balance.amount == Decimal("20000000")


def test_an_account_stating_no_terms_refuses_to_be_projected() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)

    with pytest.raises(NotFinancedError):
        ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
            user_id=USER,
            account_id=account.id,
        )


# ------------------------------------------------------------- investments


def _cdt(accounts: FakeAccounts, *, balance: str = "20000000") -> Account:
    account = Account.open(
        user_id=USER,
        name="CDT",
        kind=AccountKind.INVESTMENT,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_768_000_000),
        opening_balance=_cop(balance),
    )
    account.pull_events()
    accounts.save(account)

    return account


def test_a_cdt_earns_and_the_withholding_comes_straight_back_out() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts)
    ManageFinancingUseCase(
        accounts=accounts,
        event_publisher=RecordingPublisher(),
    ).set_investment(
        SetInvestmentTermsCommand(
            user_id=USER,
            account_id=account.id,
            opened_on=dt.date(2026, 1, 10),
            statement_day=10,
            rate=InterestRate(
                value=Decimal("0.105"),
                basis=RateBasis.EFFECTIVE_ANNUAL,
            ),
            charges=[
                ChargeDraft(
                    name="Retención en la fuente",
                    basis=ChargeBasis.EARNINGS,
                    rate=Decimal("0.04"),
                ),
            ],
            accrue_from=dt.date(2026, 1, 10),
        ),
    )

    result = _accrue(accounts, ledger, through=dt.date(2026, 2, 15))[0]

    assert [str(row.amount.amount) for row in result.posted] == [
        "167103.11",
        "6684.12",
    ]
    # Earned raises what is held; what is withheld lowers it again.
    assert result.account.balance.amount.amount == Decimal("20160418.99")


def test_stating_what_a_fund_is_worth_records_the_difference_as_a_movement() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts, balance="10000000")

    updated, movement = RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).execute(
        RevalueAccountCommand(
            user_id=USER,
            account_id=account.id,
            market_value=Decimal("10450000"),
        ),
    )

    assert movement is not None
    assert movement.counterparty == "Valoración"
    assert movement.amount.amount == Decimal("450000")
    assert updated.balance.signed_amount == Decimal("10450000")


def test_stating_the_value_it_already_has_records_nothing() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts, balance="10000000")
    use_case = RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    )
    use_case.execute(
        RevalueAccountCommand(
            user_id=USER,
            account_id=account.id,
            market_value=Decimal("10450000"),
        ),
    )

    _, second = use_case.execute(
        RevalueAccountCommand(
            user_id=USER,
            account_id=account.id,
            market_value=Decimal("10450000"),
        ),
    )

    assert second is None
    assert accounts.by_id[str(account.id.value)].balance.signed_amount == Decimal(
        "10450000",
    )


def test_a_debt_cannot_be_revalued() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)

    with pytest.raises(FinancingTermsError):
        RevalueAccountUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=RecordingPublisher(),
        ).execute(
            RevalueAccountCommand(
                user_id=USER,
                account_id=account.id,
                market_value=Decimal("1000"),
            ),
        )


def test_a_contribution_and_a_return_are_told_apart() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts, balance="10000000")
    ManageFinancingUseCase(
        accounts=accounts,
        event_publisher=RecordingPublisher(),
    ).set_investment(
        SetInvestmentTermsCommand(
            user_id=USER,
            account_id=account.id,
            opened_on=dt.date(2026, 1, 10),
            statement_day=10,
            accrue_from=dt.date(2026, 1, 10),
        ),
    )
    ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER,
            role=TransferRole.DESTINATION,
            amount=_cop("1000000"),
            occurred_at=PosixTime.from_datetime(
                dt.datetime(2026, 2, 1, 10, 0, tzinfo=dt.UTC),
            ),
            counterparty="Cuenta de ahorros",
            account_id=account.id,
        ),
    )
    RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).execute(
        RevalueAccountCommand(
            user_id=USER,
            account_id=account.id,
            market_value=Decimal("11800000"),
        ),
    )

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER,
        account_id=account.id,
        as_of=dt.date(2026, 2, 20),
    )

    assert view.performance is not None
    assert view.performance.contributed.amount == Decimal("1000000")
    assert view.performance.withdrawn.amount == Decimal("0")
    # 11 800 000 held against 10 000 000 opened and 1 000 000 put in.
    assert view.performance.earned.signed_amount == Decimal("800000")


def test_months_nobody_posted_are_counted_rather_than_prorated() -> None:
    """The figure that was wrong on screen, and why it is two figures now.

    A cursor six months behind does not mean six months of interest "running
    since the last cut". It means six charges the ledger never got, which
    compound — so rolling them into the part-month estimate would put a wrong
    number under a label that reads as a few days, and understate it besides.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)
    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER,
        account_id=account.id,
        as_of=dt.date(2026, 8, 20),
        periods=3,
    )

    # Five days past the August cut, not six months past the February one.
    assert view.pending_interest.amount == Decimal("152236.76")
    assert view.periods_due == 6


def test_an_account_that_is_up_to_date_owes_no_periods() -> None:
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account)
    _accrue(accounts, ledger, through=dt.date(2026, 2, 20))

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER,
        account_id=account.id,
        as_of=dt.date(2026, 2, 20),
        periods=3,
    )

    assert view.periods_due == 0


def test_stating_a_value_back_to_one_already_used_today_still_lands() -> None:
    """The sequence that used to leave the value stuck at the middle figure.

    11M, then 15M, then 11M again, all on one day. The third is a real
    correction and has to be recorded; keyed by the target alone it wrote the
    first one's key, was refused, and the answer reported success over a
    balance that had never moved back.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts, balance="11000000")
    use_case = RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    )

    for value in ("15000000", "11000000"):
        use_case.execute(
            RevalueAccountCommand(
                user_id=USER,
                account_id=account.id,
                market_value=Decimal(value),
            ),
        )

    assert accounts.by_id[str(account.id.value)].balance.signed_amount == Decimal(
        "11000000",
    )


def test_a_value_held_earlier_today_can_be_stated_again() -> None:
    """The step the round trip left stuck, and the reason turns exist.

    11M, 15M, 11M, and back up to 15M, all on one day. The fourth repeats the
    second exactly — same balance, same target, same day — so keyed by the
    move alone it wrote a key the ledger already held, was refused, and left
    the fund at 11 million under an answer that reported success.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _cdt(accounts, balance="11000000")
    use_case = RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    )

    for value in ("15000000", "11000000", "15000000"):
        use_case.execute(
            RevalueAccountCommand(
                user_id=USER,
                account_id=account.id,
                market_value=Decimal(value),
            ),
        )

    assert accounts.by_id[str(account.id.value)].balance.signed_amount == Decimal(
        "15000000",
    )
    # Three moves, three rows: the gain, the correction, and the gain again.
    assert [str(row.amount.amount) for row in ledger.list_all(USER)] == [
        "4000000",
        "4000000",
        "4000000",
    ]


def test_a_double_submit_that_loses_a_race_records_nothing_twice() -> None:
    """The protection the turns must not undo.

    Two identical requests read 11 million before either wrote. One wins, and
    the other finds both the key taken and the balance already where it was
    going to put it — which is what tells it apart from the correction above,
    where the balance had moved on. Taking another turn here would record a
    gain of four million that happened once and was paid for twice.
    """
    accounts = RacingAccounts()
    account = _cdt(accounts, balance="11000000")
    ledger = RacingLedger(accounts)

    updated, movement = RevalueAccountUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).execute(
        RevalueAccountCommand(
            user_id=USER,
            account_id=account.id,
            market_value=Decimal("15000000"),
        ),
    )

    assert updated.balance.signed_amount == Decimal("15000000")
    # One row, and nothing claimed as posted by the request that lost — the
    # same answer the second submit gets when it arrives late enough to read
    # the value the first one set.
    assert movement is None
    assert [str(row.amount.amount) for row in ledger.list_all(USER)] == ["4000000"]


def test_a_period_that_has_not_closed_cannot_be_charged_by_asking_nicely() -> None:
    """`through` narrows the window; it cannot widen it.

    A date in the future would post interest for months that have not
    happened — and the cursor only moves forward, so nothing would ever charge
    them again once they did.
    """
    accounts, ledger = FakeAccounts(), FakeLedger()
    account = _mortgage(accounts)
    _declare_loan(accounts, account, accrue_from=dt.date(2099, 1, 15))

    result = _accrue(accounts, ledger, through=dt.date(2099, 6, 20))[0]

    assert list(result.posted) == []
    assert accounts.by_id[str(account.id.value)].balance.signed_amount == Decimal(
        "60000000",
    )
