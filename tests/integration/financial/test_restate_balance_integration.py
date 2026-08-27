"""Restating a balance, against a real table.

The reason this needs DynamoDB rather than a fake: a restatement changes two
numbers that two different writers own. `save` carries the derived opening
balance and deliberately refuses to touch the balance, while
`overwrite_balance` writes only the balance and the count. An in-memory
repository stores one object and cannot tell whether both landed — so a
restatement that persisted half of itself would pass every unit test and
still read back wrong the next morning.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransactionCommand,
    OpenAccountCommand,
    RestateBalanceCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    InstrumentKind,
    MovementDirection,
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
WHEN = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))


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


@pytest.fixture
def manage_accounts(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> ManageAccountsUseCase:
    return ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def manage_transactions(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> ManageTransactionsUseCase:
    return ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _declare_savings(manage_accounts: ManageAccountsUseCase) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Ahorros Bancolombia",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.ACCOUNT,
            last_four="5261",
        ),
    )


def _spend(
    manage_transactions: ManageTransactionsUseCase,
    account: Account,
    amount: str,
    direction: MovementDirection = MovementDirection.OUTGOING,
) -> None:
    manage_transactions.enter(
        EnterTransactionCommand(
            user_id=USER_ID,
            direction=direction,
            amount=_cop(amount),
            occurred_at=WHEN,
            counterparty="TIENDAS ARA",
            account_id=account.id,
        ),
    )


def test_a_restated_balance_and_its_derived_opening_both_survive_a_reread(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    account = _declare_savings(manage_accounts)
    _spend(manage_transactions, account, "50000")
    _spend(manage_transactions, account, "30000", MovementDirection.INCOMING)

    manage_accounts.restate_balance(
        RestateBalanceCommand(
            user_id=USER_ID,
            account_id=account.id,
            balance=Decimal("1200000"),
        ),
    )

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    assert stored.balance.signed_amount == Decimal("1200000")
    # The half `save` owns. Read back from the table, not from the instance
    # the use case happened to return.
    assert stored.opening_balance.signed_amount == Decimal("1220000")
    assert stored.movements_applied == 2


def test_a_movement_after_a_restatement_moves_the_restated_number(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    """The ledger's atomic add has to work from the corrected balance.

    This is what would break if a restatement wrote the balance without the
    opening balance behind it: the number would look right until the next
    alert, then jump back.
    """
    account = _declare_savings(manage_accounts)
    manage_accounts.restate_balance(
        RestateBalanceCommand(
            user_id=USER_ID,
            account_id=account.id,
            balance=Decimal("1200000"),
        ),
    )

    reloaded = accounts.find(user_id=USER_ID, account_id=account.id)

    assert reloaded is not None

    _spend(manage_transactions, reloaded, "200000")

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    assert stored.balance.signed_amount == Decimal("1000000")


def test_replaying_the_ledger_reproduces_a_restated_balance(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A restatement must leave the account repairable.

    If the opening balance did not absorb the difference, the next replay —
    which any adoption or correction triggers — would silently undo it.
    """
    account = _declare_savings(manage_accounts)
    _spend(manage_transactions, account, "50000")
    manage_accounts.restate_balance(
        RestateBalanceCommand(
            user_id=USER_ID,
            account_id=account.id,
            balance=Decimal("1200000"),
        ),
    )

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None

    stored.rebuild(
        movement.as_movement()
        for movement in ledger.list_movements(user_id=USER_ID, account_id=account.id)
    )

    assert stored.balance.signed_amount == Decimal("1200000")
