"""A card paid from outside this app, against a real table.

The reason this needs DynamoDB rather than a fake: what makes the movement a
transfer is a single nested attribute, written by one mapper and read by
another. An in-memory ledger hands back the object it was given, so a leg that
serialised without its marker — or with half a counterpart — would pass every
unit test and come back the next morning as an expense the size of the card's
whole balance, on an account whose number is still right.

The last test is the one that would catch that: the stored balance and the
balance rebuilt by replaying every row have to be the same number.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransactionCommand,
    EnterTransferLegCommand,
    OpenAccountCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    ListTransactionsUseCase,
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryQuery,
    TransactionQuery,
    TransferView,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    BalanceSign,
    InstrumentKind,
    MovementDirection,
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


def _declare_card(manage_accounts: ManageAccountsUseCase, owes: str) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Tarjeta Bancolombia",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="7653",
            opening_balance=_cop(owes),
        ),
    )


def _pay_from_outside(
    manage_transactions: ManageTransactionsUseCase,
    card: Account,
    amount: str,
) -> str:
    movement = manage_transactions.enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER_ID,
            role=TransferRole.DESTINATION,
            amount=_cop(amount),
            occurred_at=WHEN,
            counterparty="Nequi",
            account_id=card.id,
            bank="Bancolombia",
        ),
    )

    return movement.id.value


def test_the_leg_reads_back_as_a_transfer(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    card = _declare_card(manage_accounts, owes="3540258")
    movement_id = _pay_from_outside(manage_transactions, card, "3540258")

    stored = ledger.find(user_id=USER_ID, transaction_id=movement_id)

    assert stored is not None
    assert stored.is_transfer is True


def test_the_leg_reads_back_with_an_external_counterpart(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    card = _declare_card(manage_accounts, owes="3540258")
    movement_id = _pay_from_outside(manage_transactions, card, "3540258")

    stored = ledger.find(user_id=USER_ID, transaction_id=movement_id)

    assert stored is not None
    assert stored.transfer is not None
    assert stored.transfer.counterpart_is_external is True
    assert stored.has_counterpart_movement is False


def test_the_debt_falls_and_stays_fallen(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    card = _declare_card(manage_accounts, owes="3540258")
    _pay_from_outside(manage_transactions, card, "3540258")

    stored = accounts.find(user_id=USER_ID, account_id=card.id)

    assert stored is not None
    assert stored.balance.signed_amount == Decimal("0")
    assert stored.balance.sign is BalanceSign.POSITIVE


def test_the_summary_does_not_count_it(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A real expense sits beside it, so this cannot pass by being empty."""
    card = _declare_card(manage_accounts, owes="3540258")
    manage_transactions.enter(
        EnterTransactionCommand(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=_cop("50000"),
            occurred_at=WHEN,
            counterparty="TIENDAS ARA",
            account_id=card.id,
        ),
    )
    _pay_from_outside(manage_transactions, card, "3540258")

    # `EXCLUDE` spelled out because `MovementFilter` defaults to `INCLUDE`;
    # what makes every total leave transfers out is the query parameter
    # default on `/financial/summary`, not this filter.
    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    assert len(summary.totals) == 1
    assert summary.totals[0].outgoing == Decimal("50000")
    assert summary.totals[0].movements == 1


def test_the_leg_is_still_listed_and_can_be_asked_for_alone(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    card = _declare_card(manage_accounts, owes="3540258")
    movement_id = _pay_from_outside(manage_transactions, card, "3540258")
    listing = ListTransactionsUseCase(ledger=ledger)

    only = listing.execute(
        TransactionQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.ONLY),
        ),
    )
    without = listing.execute(
        TransactionQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    assert [row.transaction.id.value for row in only.transactions] == [movement_id]
    assert without.transactions == []


def test_the_stored_balance_matches_a_rebuild_from_the_rows(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The check that catches a leg written one way and read another. A
    rebuild replays the stored rows; if the round trip changed the movement's
    direction, amount or account, the two numbers part company here."""
    card = _declare_card(manage_accounts, owes="3540258")
    _pay_from_outside(manage_transactions, card, "1540258")
    manage_transactions.enter(
        EnterTransactionCommand(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=_cop("200000"),
            occurred_at=WHEN,
            counterparty="TIENDAS ARA",
            account_id=card.id,
        ),
    )

    stored = accounts.find(user_id=USER_ID, account_id=card.id)
    assert stored is not None

    rebuilt = stored.balance_after(
        movement.as_movement()
        for movement in ledger.list_movements(user_id=USER_ID, account_id=card.id)
    )

    assert rebuilt.signed_amount == stored.balance.signed_amount
    assert rebuilt.signed_amount == Decimal("2200000")
