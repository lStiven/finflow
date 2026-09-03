"""A loan against a real table, from declaring its terms to what it owes.

Two things need DynamoDB rather than a fake and are the reason this exists.

The **terms round-trip**: a rate, a cut day and a list of insurances go into a
nested map and have to come back as the same numbers. A repository that stored
one object in memory cannot fail that, and a mortgage that read back at a rate
of zero would quietly stop growing with nothing to notice it.

The **balance**: an accrual writes a row and moves the balance by an atomic
`ADD`, one `TransactWriteItems` each, exactly as an alert does. What is under
test is that after a month of interest, an insurance and an instalment paid,
the number stored is 58 920 622.87 — and that running the accrual again writes
nothing at all, because the second attempt lands on a key the table already
holds.
"""

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    AccrueFinancingCommand,
    ChargeDraft,
    ClearFinancingCommand,
    EnterTransferLegCommand,
    OpenAccountCommand,
    SetLoanTermsCommand,
)
from personal_finance.contexts.financial.application.financing import (
    AccrueFinancingUseCase,
    ManageFinancingUseCase,
    ReadFinancingUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.financing import (
    AmortizationStyle,
    ChargeBasis,
    InterestRate,
    RateBasis,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    TransferRole,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "financial"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
DISBURSED_ON = dt.date(2026, 1, 15)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> str:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )

    return TABLE_NAME


@pytest.fixture
def accounts(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def ledger(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(client=dynamodb_client, table_name=table)


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _declare_mortgage(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> Account:
    return ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Hipoteca",
            kind=AccountKind.MORTGAGE,
            currency=Currency.COP,
            opening_balance=_cop("60000000"),
            bank="Bancolombia",
        ),
    )


def _set_terms(
    accounts: DynamoDBAccountRepository,
    account: Account,
) -> Account:
    return ManageFinancingUseCase(
        accounts=accounts,
        event_publisher=NullEventPublisher(),
    ).set_loan(
        SetLoanTermsCommand(
            user_id=USER_ID,
            account_id=account.id,
            rate=InterestRate(
                value=Decimal("0.1956"),
                basis=RateBasis.EFFECTIVE_ANNUAL,
            ),
            disbursed_on=DISBURSED_ON,
            term_months=60,
            statement_day=15,
            payment_day=20,
            style=AmortizationStyle.FRENCH,
            principal=Decimal("60000000"),
            installment=Decimal("2000000"),
            installment_covers_charges=True,
            charges=[
                ChargeDraft(
                    name="Seguro de vida deudores",
                    basis=ChargeBasis.OUTSTANDING_BALANCE,
                    rate=Decimal("0.000345"),
                ),
                ChargeDraft(
                    name="Seguro de incendio y terremoto",
                    basis=ChargeBasis.INSURED_VALUE,
                    rate=Decimal("0.00029"),
                    base=Decimal("350000000"),
                    charged_to_balance=False,
                ),
            ],
            accrue_from=DISBURSED_ON,
        ),
    )


def test_the_terms_come_back_as_the_numbers_that_went_in(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    assert stored.loan is not None
    assert stored.loan.rate.value == Decimal("0.1956")
    assert stored.loan.rate.basis is RateBasis.EFFECTIVE_ANNUAL
    assert stored.loan.disbursed_on == DISBURSED_ON
    assert stored.loan.term_months == 60
    assert stored.loan.statement_day == 15
    assert stored.loan.payment_day == 20
    assert stored.loan.installment_covers_charges is True
    assert stored.loan.principal == _cop("60000000")
    assert stored.loan.installment == _cop("2000000")
    assert [charge.name for charge in stored.loan.charges] == [
        "Seguro de vida deudores",
        "Seguro de incendio y terremoto",
    ]
    assert stored.loan.charges[0].rate == Decimal("0.000345")
    assert stored.loan.charges[1].base == _cop("350000000")
    assert stored.loan.charges[1].charged_to_balance is False
    assert stored.accrued_through == DISBURSED_ON


def test_clearing_the_terms_removes_them_from_the_stored_account(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    # An update that only assigns leaves the old value in place, so this is
    # the half of `save` that has to actually remove an attribute.
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)
    manage = ManageFinancingUseCase(
        accounts=accounts,
        event_publisher=NullEventPublisher(),
    )

    manage.clear(ClearFinancingCommand(user_id=USER_ID, account_id=account.id))
    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    assert stored.loan is None
    assert stored.accrued_through is None


def test_a_month_of_interest_and_an_instalment_leave_the_stored_debt_right(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)

    AccrueFinancingUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).execute(
        AccrueFinancingCommand(
            user_id=USER_ID,
            account_id=account.id,
            through=dt.date(2026, 2, 20),
        ),
    )
    ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER_ID,
            role=TransferRole.DESTINATION,
            amount=_cop("2000000"),
            occurred_at=PosixTime.from_datetime(
                dt.datetime(2026, 2, 20, 15, 0, tzinfo=dt.UTC),
            ),
            counterparty="Cuenta de ahorros Bancolombia",
            account_id=account.id,
        ),
    )

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    # 60 000 000 + 899 922.87 of interest + 20 700 of *seguro de vida*, less
    # the 2 000 000 paid. The *seguro de incendio* is collected elsewhere and
    # never touches this balance.
    assert stored.balance.signed_amount == Decimal("58920622.87")
    assert stored.movements_applied == 3


def test_replaying_the_rows_reproduces_the_balance(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The invariant the whole design rests on.

    An accrual is a movement, so the balance stays the running total of the
    ledger — which means `rebuild` has to land on the same number the atomic
    adds did. If it did not, the interest would be a figure nothing could
    repair.
    """
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)
    AccrueFinancingUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).execute(
        AccrueFinancingCommand(
            user_id=USER_ID,
            account_id=account.id,
            through=dt.date(2026, 4, 20),
        ),
    )

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    incremental = stored.balance.signed_amount
    stored.rebuild(
        movement.as_movement()
        for movement in ledger.list_movements(
            user_id=USER_ID,
            account_id=account.id,
        )
    )

    assert stored.balance.signed_amount == incremental


def test_running_the_accrual_again_writes_nothing_to_the_table(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)
    accrue = AccrueFinancingUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )
    accrue.execute(
        AccrueFinancingCommand(
            user_id=USER_ID,
            account_id=account.id,
            through=dt.date(2026, 3, 20),
        ),
    )
    rows = len(ledger.list_movements(user_id=USER_ID, account_id=account.id))
    balance = accounts.find(user_id=USER_ID, account_id=account.id)

    assert balance is not None

    # The cursor put back by hand, which is what a crash between writing the
    # rows and advancing it leaves behind.
    balance.accrued_through = DISBURSED_ON
    accounts.save(balance)
    again = accrue.execute(
        AccrueFinancingCommand(
            user_id=USER_ID,
            account_id=account.id,
            through=dt.date(2026, 3, 20),
        ),
    )
    after = accounts.find(user_id=USER_ID, account_id=account.id)

    assert after is not None
    assert list(again[0].posted) == []
    assert again[0].skipped == rows
    assert len(ledger.list_movements(user_id=USER_ID, account_id=account.id)) == rows
    assert after.balance.signed_amount == balance.balance.signed_amount


def test_the_payoff_reads_back_from_what_the_table_holds(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    account = _declare_mortgage(accounts, ledger)
    _set_terms(accounts, account)
    AccrueFinancingUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).execute(
        AccrueFinancingCommand(
            user_id=USER_ID,
            account_id=account.id,
            through=dt.date(2026, 2, 20),
        ),
    )

    view = ReadFinancingUseCase(accounts=accounts, ledger=ledger).execute(
        user_id=USER_ID,
        account_id=account.id,
        as_of=dt.date(2026, 2, 15),
        periods=6,
    )

    assert view.payoff is not None
    # Nothing has accrued since the cut, so settling today is the balance.
    assert view.payoff.amount == Decimal("60920622.87")
    assert view.schedule is not None
    assert view.schedule.payments[0].opening_balance.amount == Decimal("60920622.87")
    assert view.next_due_on == dt.date(2026, 3, 20)
