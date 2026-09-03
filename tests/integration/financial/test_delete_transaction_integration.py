"""Erasing a movement against a real table.

The reason this needs DynamoDB rather than a fake: an in-memory ledger deletes
by popping a key it was handed, so a `remove` that built the wrong key — the
wrong prefix, the wrong partition — would pass every unit test and come back
the next morning as a movement the owner deleted twice and that is still
sitting in their month, with a balance that no longer matches the rows behind
it.

The last test in each section is the one that would catch it: the stored
balance and the balance rebuilt by replaying every remaining row have to be
the same number.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    DeleteTransactionCommand,
    EnterTransactionCommand,
    EnterTransferLegCommand,
    OpenAccountCommand,
    RecordTransferCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
    RecordTransferUseCase,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
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
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")
WHEN = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))
LATER = PosixTime.from_datetime(datetime(2026, 8, 24, 9, 30, tzinfo=UTC))


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
def record_transfer(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> RecordTransferUseCase:
    return RecordTransferUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _declare_savings(
    manage_accounts: ManageAccountsUseCase,
    *,
    holds: str = "1000000",
    owner: UserId = USER_ID,
) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=owner,
            name="Ahorros Bancolombia",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.ACCOUNT,
            last_four="5261",
            opening_balance=_cop(holds),
        ),
    )


def _declare_card(
    manage_accounts: ManageAccountsUseCase,
    *,
    owes: str = "800000",
) -> Account:
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


def _spend(
    manage_transactions: ManageTransactionsUseCase,
    account: Account,
    *,
    amount: str,
    counterparty: str = "RESTAURANTE EL LAGO",
    occurred_at: PosixTime = WHEN,
) -> str:
    movement = manage_transactions.enter(
        EnterTransactionCommand(
            user_id=account.user_id,
            direction=MovementDirection.OUTGOING,
            amount=_cop(amount),
            occurred_at=occurred_at,
            counterparty=counterparty,
            account_id=account.id,
        ),
    )

    return movement.id.value


def _balance_of(accounts: DynamoDBAccountRepository, account: Account) -> Decimal:
    stored = accounts.find(user_id=account.user_id, account_id=account.id)

    assert stored is not None

    return stored.balance.signed_amount


def _rebuilt_balance_of(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    account: Account,
) -> Decimal:
    stored = accounts.find(user_id=account.user_id, account_id=account.id)

    assert stored is not None

    return stored.balance_after(
        movement.as_movement()
        for movement in ledger.list_movements(
            user_id=account.user_id,
            account_id=account.id,
        )
    ).signed_amount


def test_the_row_is_really_gone_from_the_table(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The key `remove` builds has to be the key `record` wrote, or the delete
    silently succeeds against nothing."""
    account = _declare_savings(manage_accounts)
    movement = _spend(manage_transactions, account, amount="2000")

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=movement),
    )

    assert ledger.find(user_id=USER_ID, transaction_id=movement) is None
    assert ledger.list_all(USER_ID) == []


def test_the_account_gets_its_money_back(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    account = _declare_savings(manage_accounts, holds="1000000")
    movement = _spend(manage_transactions, account, amount="2000")

    assert _balance_of(accounts, account) == Decimal("998000")

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=movement),
    )

    assert _balance_of(accounts, account) == Decimal("1000000")


def test_the_stored_balance_matches_a_rebuild_of_what_is_left(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The check that catches a row deleted from the table while the balance
    was moved by a different rule — the number and the rows part company here.
    """
    account = _declare_savings(manage_accounts, holds="1000000")
    _spend(manage_transactions, account, amount="30000", counterparty="TIENDAS ARA")
    doomed = _spend(manage_transactions, account, amount="2000", occurred_at=LATER)

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=doomed),
    )

    assert _balance_of(accounts, account) == Decimal("970000")
    assert _balance_of(accounts, account) == _rebuilt_balance_of(
        accounts,
        ledger,
        account,
    )


def test_the_movements_applied_count_follows_the_rows(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    """The count is incremented by the ledger's own atomic add, so a rebuild
    that did not write it back would leave the account claiming a movement
    that is no longer there."""
    account = _declare_savings(manage_accounts)
    _spend(manage_transactions, account, amount="30000", counterparty="TIENDAS ARA")
    doomed = _spend(manage_transactions, account, amount="2000", occurred_at=LATER)

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=doomed),
    )

    stored = accounts.find(user_id=USER_ID, account_id=account.id)

    assert stored is not None
    assert stored.movements_applied == 1


def test_erasing_the_same_movement_twice_moves_the_balance_once(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    account = _declare_savings(manage_accounts, holds="1000000")
    movement = _spend(manage_transactions, account, amount="2000")

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=movement),
    )

    with pytest.raises(TransactionNotFoundError):
        manage_transactions.delete(
            DeleteTransactionCommand(user_id=USER_ID, transaction_id=movement),
        )

    assert _balance_of(accounts, account) == Decimal("1000000")


def test_one_person_cannot_erase_another_persons_movement(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The partition key is what keeps two people's money apart, and a delete
    that reached across it would be the worst possible way to find out."""
    account = _declare_savings(manage_accounts, holds="1000000")
    movement = _spend(manage_transactions, account, amount="2000")

    with pytest.raises(TransactionNotFoundError):
        manage_transactions.delete(
            DeleteTransactionCommand(user_id=OTHER_USER, transaction_id=movement),
        )

    assert ledger.find(user_id=USER_ID, transaction_id=movement) is not None
    assert _balance_of(accounts, account) == Decimal("998000")


# ------------------------------------------------- transfers go as a pair


def test_both_sides_of_a_transfer_leave_in_one_write(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    record_transfer: RecordTransferUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Two `Delete`s in one `TransactWriteItems`: a transfer half-erased is
    worse than one left alone."""
    _declare_savings(manage_accounts, holds="1000000")
    _declare_card(manage_accounts, owes="800000")
    paid = record_transfer.execute(
        RecordTransferCommand(
            user_id=USER_ID,
            bank="Bancolombia",
            amount=_cop("500000"),
            occurred_at=WHEN,
            source_instrument_kind="account",
            source_last_four="5261",
            destination_instrument_kind="credit_card",
            destination_last_four="7653",
        ),
    )

    manage_transactions.delete(
        DeleteTransactionCommand(
            user_id=USER_ID,
            transaction_id=paid.destination.transaction.id.value,
        ),
    )

    assert ledger.list_all(USER_ID) == []


def test_erasing_a_transfer_restores_both_balances(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    record_transfer: RecordTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The account gets its money back and the card's debt goes back up. Only
    one of the two restored would be worse than not erasing at all."""
    savings = _declare_savings(manage_accounts, holds="1000000")
    card = _declare_card(manage_accounts, owes="800000")
    paid = record_transfer.execute(
        RecordTransferCommand(
            user_id=USER_ID,
            bank="Bancolombia",
            amount=_cop("500000"),
            occurred_at=WHEN,
            source_instrument_kind="account",
            source_last_four="5261",
            destination_instrument_kind="credit_card",
            destination_last_four="7653",
        ),
    )

    assert _balance_of(accounts, savings) == Decimal("500000")
    assert _balance_of(accounts, card) == Decimal("300000")

    result = manage_transactions.delete(
        DeleteTransactionCommand(
            user_id=USER_ID,
            transaction_id=paid.source.transaction.id.value,
        ),
    )

    assert {account.id for account in result.restored} == {savings.id, card.id}
    assert _balance_of(accounts, savings) == Decimal("1000000")
    assert _balance_of(accounts, card) == Decimal("800000")
    assert _rebuilt_balance_of(accounts, ledger, savings) == Decimal("1000000")
    assert _rebuilt_balance_of(accounts, ledger, card) == Decimal("800000")


def test_a_leg_paid_from_outside_is_erased_alone(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """It has no second row to take with it, and its debt goes back up by
    exactly what the payment took off."""
    card = _declare_card(manage_accounts, owes="800000")
    leg = manage_transactions.enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER_ID,
            role=TransferRole.DESTINATION,
            amount=_cop("500000"),
            occurred_at=WHEN,
            counterparty="Nequi",
            account_id=card.id,
            bank="Bancolombia",
        ),
    )

    assert _balance_of(accounts, card) == Decimal("300000")

    result = manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=leg.id.value),
    )

    assert [movement.id for movement in result.erased] == [leg.id]
    assert _balance_of(accounts, card) == Decimal("800000")
    assert _rebuilt_balance_of(accounts, ledger, card) == Decimal("800000")


def test_the_same_payment_can_be_erased_and_entered_again(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    """A leg's identity comes from its content, and that identity is what the
    ledger's conditional write dedupes on. Erasing the row has to free the key
    again, or somebody who deletes a payment by mistake can never re-enter it.
    """
    card = _declare_card(manage_accounts, owes="800000")
    command = EnterTransferLegCommand(
        user_id=USER_ID,
        role=TransferRole.DESTINATION,
        amount=_cop("500000"),
        occurred_at=WHEN,
        counterparty="Nequi",
        account_id=card.id,
        bank="Bancolombia",
    )
    first = manage_transactions.enter_transfer_leg(command)

    manage_transactions.delete(
        DeleteTransactionCommand(user_id=USER_ID, transaction_id=first.id.value),
    )
    again = manage_transactions.enter_transfer_leg(command)

    assert again.id == first.id
    assert _balance_of(accounts, card) == Decimal("300000")


def test_the_row_and_its_balance_move_together_or_not_at_all(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The invariant this table is built on, read backwards.

    `record` writes the row and the balance change in one `TransactWriteItems`
    so a balance can never move without a row behind it; erasing has to be the
    same write in reverse, or a failure between the two leaves an account
    carrying a movement that is gone — and no retry can fix it, because the
    row it would need is already deleted.

    Forced here by aiming the balance half at an account that is not there:
    its condition fails, and what has to survive that is the *row*.
    """
    account = _declare_savings(manage_accounts, holds="1000000")
    movement = _spend(manage_transactions, account, amount="2000")
    stored = ledger.find(user_id=USER_ID, transaction_id=movement)

    assert stored is not None

    with pytest.raises(ClientError):
        ledger.remove(
            [stored],
            reversals=[
                BalanceReversal(
                    account_id=AccountId.new(),
                    delta=Decimal("2000"),
                    movements=1,
                ),
            ],
        )

    assert ledger.find(user_id=USER_ID, transaction_id=movement) is not None
    assert _balance_of(accounts, account) == Decimal("998000")


def test_rows_from_two_owners_cannot_be_erased_in_one_write(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The balance half of the write is keyed by one partition, so a batch
    spanning two people would move one owner's balance for rows deleted from
    another's. Nothing builds such a batch today; this is what keeps it so.
    """
    mine = _declare_savings(manage_accounts)
    theirs = _declare_savings(manage_accounts, owner=OTHER_USER)
    my_row = ledger.find(
        user_id=USER_ID,
        transaction_id=_spend(manage_transactions, mine, amount="2000"),
    )
    their_row = ledger.find(
        user_id=OTHER_USER,
        transaction_id=_spend(manage_transactions, theirs, amount="3000"),
    )

    assert my_row is not None
    assert their_row is not None

    with pytest.raises(ValueError, match="one owner"):
        ledger.remove([my_row, their_row], reversals=[])

    assert ledger.list_all(USER_ID) != []
    assert ledger.list_all(OTHER_USER) != []


def test_erasing_twice_gives_the_money_back_once(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A delete on a key that is already gone succeeds in DynamoDB.

    So without a condition on it, a retried erasure deletes nothing and unwinds
    the balance a second time — money appearing out of a row that had already
    been taken back. The condition is what makes the second attempt a no-op
    rather than a gift.
    """
    account = _declare_savings(manage_accounts, holds="1000000")
    movement = _spend(manage_transactions, account, amount="2000")
    stored = ledger.find(user_id=USER_ID, transaction_id=movement)

    assert stored is not None

    reversal = [
        BalanceReversal(account_id=account.id, delta=Decimal("2000"), movements=1),
    ]
    ledger.remove([stored], reversals=reversal)
    ledger.remove([stored], reversals=reversal)

    assert _balance_of(accounts, account) == Decimal("1000000")
