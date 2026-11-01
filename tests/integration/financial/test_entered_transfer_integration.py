"""A card paid from savings at another bank, both entered by hand, against a
real table.

What a fake cannot show: that the two rows and the two balance changes land
in one transaction, that a side naming its counterpart by movement alone —
no digits — reads back as a pair rather than as corrupt, and that the stored
balances still match a rebuild from the rows.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    DeleteTransactionCommand,
    EnterTransactionCommand,
    EnterTransferBetweenCommand,
    OpenAccountCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    EnterTransferBetweenUseCase,
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryQuery,
    TransferView,
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
WHEN = PosixTime.from_datetime(datetime(2026, 10, 30, 14, 0, tzinfo=UTC))


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


@pytest.fixture
def enter_transfer(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> EnterTransferBetweenUseCase:
    return EnterTransferBetweenUseCase(
        accounts=accounts,
        ledger=ledger,
        pairs=ledger,
        event_publisher=NullEventPublisher(),
    )


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _savings(manage_accounts: ManageAccountsUseCase) -> Account:
    """No instrument at all: what makes a side name its counterpart by
    movement alone."""
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Ahorros Lulo",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            opening_balance=_cop("5000000"),
        ),
    )


def _card(manage_accounts: ManageAccountsUseCase) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Tarjeta Nu",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="Nu",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="7653",
            opening_balance=_cop("3540258"),
        ),
    )


def _pay(
    enter_transfer: EnterTransferBetweenUseCase,
    savings: Account,
    card: Account,
) -> tuple[str, str]:
    leaving, arriving = enter_transfer.execute(
        EnterTransferBetweenCommand(
            user_id=USER_ID,
            source_account_id=savings.id,
            destination_account_id=card.id,
            amount=_cop("2000000"),
            occurred_at=WHEN,
        ),
    )

    return leaving.id.value, arriving.id.value


def _balance(accounts: DynamoDBAccountRepository, account: Account) -> Decimal:
    stored = accounts.find(user_id=USER_ID, account_id=account.id)
    assert stored is not None

    return stored.balance.signed_amount


def test_both_sides_read_back_as_a_pair(
    manage_accounts: ManageAccountsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    out_id, in_id = _pay(
        enter_transfer, _savings(manage_accounts), _card(manage_accounts)
    )

    leaving = ledger.find(user_id=USER_ID, transaction_id=out_id)
    arriving = ledger.find(user_id=USER_ID, transaction_id=in_id)

    assert leaving is not None and leaving.transfer is not None
    assert arriving is not None and arriving.transfer is not None
    assert leaving.transfer.counterpart_id is not None
    assert leaving.transfer.counterpart_id.value == in_id
    assert arriving.transfer.counterpart_is_external is False
    assert leaving.counterparty == "Tarjeta Nu"


def test_the_savings_and_the_debt_both_fall_and_stay_fallen(
    manage_accounts: ManageAccountsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    savings, card = _savings(manage_accounts), _card(manage_accounts)

    _pay(enter_transfer, savings, card)

    assert _balance(accounts, savings) == Decimal("3000000")
    assert _balance(accounts, card) == Decimal("1540258")


def test_the_same_payment_entered_twice_moves_the_money_once(
    manage_accounts: ManageAccountsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings, card = _savings(manage_accounts), _card(manage_accounts)

    first = _pay(enter_transfer, savings, card)
    second = _pay(enter_transfer, savings, card)

    assert first == second
    assert len(ledger.list_all(USER_ID)) == 2
    assert _balance(accounts, savings) == Decimal("3000000")
    assert _balance(accounts, card) == Decimal("1540258")


def test_the_stored_balances_match_a_rebuild_from_the_rows(
    manage_accounts: ManageAccountsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings, card = _savings(manage_accounts), _card(manage_accounts)
    _pay(enter_transfer, savings, card)

    for account in (savings, card):
        stored = accounts.find(user_id=USER_ID, account_id=account.id)
        assert stored is not None

        rebuilt = stored.balance_after(
            movement.as_movement()
            for movement in ledger.list_movements(
                user_id=USER_ID,
                account_id=account.id,
            )
        )

        assert rebuilt.signed_amount == stored.balance.signed_amount


def test_the_summary_counts_neither_side(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A real expense sits beside it, so this cannot pass by being empty."""
    savings, card = _savings(manage_accounts), _card(manage_accounts)
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
    _pay(enter_transfer, savings, card)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    assert summary.totals[0].outgoing == Decimal("50000")
    assert summary.totals[0].incoming == Decimal("0")
    assert summary.totals[0].movements == 1


def test_deleting_one_side_gives_both_accounts_their_money_back(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings, card = _savings(manage_accounts), _card(manage_accounts)
    _, in_id = _pay(enter_transfer, savings, card)

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=in_id),
    )

    assert ledger.list_all(USER_ID) == []
    assert _balance(accounts, savings) == Decimal("5000000")
    assert _balance(accounts, card) == Decimal("3540258")


def test_a_cuota_to_a_mortgage_is_counted_once_where_it_leaves(
    manage_accounts: ManageAccountsUseCase,
    enter_transfer: EnterTransferBetweenUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A mortgage is watched, never counted: the cuota is spending on the
    savings side, and the mortgage's own leg only lowers its debt."""
    savings = _savings(manage_accounts)
    mortgage = manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Hipoteca",
            kind=AccountKind.MORTGAGE,
            currency=Currency.COP,
            opening_balance=_cop("60000000"),
        ),
    )

    _pay(enter_transfer, savings, mortgage)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    assert _balance(accounts, savings) == Decimal("3000000")
    assert _balance(accounts, mortgage) == Decimal("58000000")
    assert summary.totals[0].outgoing == Decimal("2000000")
    assert summary.totals[0].incoming == Decimal("0")
    assert summary.totals[0].movements == 1
