"""A payment to a card at another bank, declared a transfer, against a real table.

The case that started it, with the alert's own figures: Bancolombia emails
"Pagaste $3,625,733.00 a BANCO COMERCIAL AV VILLAS desde tu producto *5261",
AV Villas emails a receipt nothing can read as a movement, and the card's debt
never falls. Recorded as it arrives, the savings account is right and net
worth is wrong by the whole payment.

Why DynamoDB rather than a fake: the declaration is two conditional updates on
a nested attribute and a conditional put with an `ADD` beside it, all in one
transaction. A fake would accept whatever it was handed; this is where a
condition spelled wrong, or a marker that reads back as spending, would show.
"""

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.bills import (
    DeclareBillCommand,
    ManageBillsUseCase,
    SettleBillChargeUseCase,
)
from personal_finance.contexts.financial.application.commands import (
    EnterTransferLegCommand,
    OpenAccountCommand,
    RecordMovementCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
    Outcome,
    RecordMovementUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    ListAccountsUseCase,
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryQuery,
    TransferView,
)
from personal_finance.contexts.financial.application.transfers import (
    DeclareTransferCommand,
    DeclareTransferUseCase,
)
from personal_finance.contexts.financial.domain.bills import BillCadence
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.exceptions import (
    TransferDeclarationError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransferBasis,
    TransferRole,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBScheduledBillRepository,
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
# 30/12/2025 11:17 in Bogotá, as the alert prints it.
PAID_AT = PosixTime.from_datetime(dt.datetime(2025, 12, 30, 16, 17, tzinfo=dt.UTC))
PAYMENT = "3625733.00"


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.events.extend(events)


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
def bills(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBScheduledBillRepository:
    return DynamoDBScheduledBillRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def manage_accounts(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> ManageAccountsUseCase:
    return ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    )


@pytest.fixture
def record(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> RecordMovementUseCase:
    return RecordMovementUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    )


@pytest.fixture
def transfers(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    bills: DynamoDBScheduledBillRepository,
    publisher: RecordingPublisher,
) -> DeclareTransferUseCase:
    return DeclareTransferUseCase(
        accounts=accounts,
        ledger=ledger,
        declarations=ledger,
        bills=bills,
        event_publisher=publisher,
    )


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _open_savings(manage_accounts: ManageAccountsUseCase) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Ahorros Bancolombia",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.ACCOUNT,
            last_four="5261",
            opening_balance=_cop("5000000"),
        ),
    )


def _open_card(manage_accounts: ManageAccountsUseCase) -> Account:
    """A card nothing emails about: it is declared with no instrument."""
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Tarjeta AV Villas",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="AV Villas",
            opening_balance=_cop(PAYMENT),
        ),
    )


def _open_lulo(manage_accounts: ManageAccountsUseCase) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Lulo",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Lulo Bank",
            instrument_kind=InstrumentKind.ACCOUNT,
            last_four="0042",
            opening_balance=_cop("0"),
        ),
    )


def _bancolombia_alert(record: RecordMovementUseCase, **overrides: object) -> str:
    """What the template reads out of the alert, field for field."""
    parts: dict[str, object] = {
        "user_id": USER_ID,
        "bank": "bancolombia",
        "direction": MovementDirection.OUTGOING,
        "amount": _cop(PAYMENT),
        "occurred_at": PAID_AT,
        "counterparty": "BANCO COMERCIAL AV VILLAS",
        "instrument_kind": "account",
        "last_four": "5261",
    }
    parts.update(overrides)
    result = record.execute(RecordMovementCommand(**parts))  # pyright: ignore[reportArgumentType]

    return result.transaction.id.value


def _balance(accounts: DynamoDBAccountRepository, account: Account) -> Decimal:
    stored = accounts.find(user_id=USER_ID, account_id=account.id)
    assert stored is not None

    return stored.balance.signed_amount


def _net_worth(accounts: DynamoDBAccountRepository) -> Decimal:
    view = ListAccountsUseCase(accounts=accounts).execute(user_id=USER_ID)

    return view.net_worth[0].total


def _spent(
    ledger: DynamoDBTransactionLedger,
    accounts: DynamoDBAccountRepository,
) -> Decimal:
    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    return summary.totals[0].outgoing if summary.totals else Decimal(0)


def _rebuilt_matches(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    account: Account,
) -> bool:
    stored = accounts.find(user_id=USER_ID, account_id=account.id)
    assert stored is not None
    rebuilt = stored.balance_after(
        movement.as_movement()
        for movement in ledger.list_movements(user_id=USER_ID, account_id=account.id)
    )

    return rebuilt.signed_amount == stored.balance.signed_amount


# ------------------------------------------------------------ before anything


def test_recorded_as_it_arrives_net_worth_is_off_by_the_whole_payment(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The problem, measured, so the fix below is measured against it."""
    _open_savings(manage_accounts)
    _open_card(manage_accounts)
    _bancolombia_alert(record)

    # 5 000 000 - 3 625 733 held, 3 625 733 still owed on a card already paid.
    assert _net_worth(accounts) == Decimal("-2251466.00")
    assert _spent(ledger, accounts) == Decimal(PAYMENT)


# ---------------------------------------------- the other side, written for it


def test_the_suggestion_is_the_card_the_alert_names(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
) -> None:
    _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    _open_lulo(manage_accounts)
    movement_id = _bancolombia_alert(record)

    options = transfers.options(user_id=USER_ID, transaction_id=movement_id)

    assert options.refusal is None
    assert options.suggested == frozenset({card.id})
    assert options.accounts[0].id == card.id


def test_declaring_it_lowers_the_debt_and_puts_net_worth_right(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings = _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)

    transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )

    assert _balance(accounts, savings) == Decimal("1374267.00")
    assert _balance(accounts, card) == Decimal("0.00")
    # What was held before the payment, less what was owed: nothing changed.
    assert _net_worth(accounts) == Decimal("1374267.00")
    assert _spent(ledger, accounts) == Decimal(0)
    assert _rebuilt_matches(accounts, ledger, savings)
    assert _rebuilt_matches(accounts, ledger, card)


def test_both_sides_read_back_as_one_declared_transfer(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)

    result = transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )
    declared = ledger.find(user_id=USER_ID, transaction_id=movement_id)
    written = ledger.find(user_id=USER_ID, transaction_id=result.movements[1].id.value)

    assert declared is not None
    assert written is not None
    assert declared.transfer is not None
    assert written.transfer is not None
    assert declared.transfer.basis is TransferBasis.RECLASSIFIED
    assert written.transfer.basis is TransferBasis.COUNTERPART
    assert declared.transfer.transfer_id == written.transfer.transfer_id
    assert declared.transfer.counterpart_id == written.id
    assert written.transfer.counterpart_id == declared.id
    assert written.account_id == card.id
    assert written.counterparty == "Ahorros Bancolombia"


def test_the_alert_arriving_again_is_still_a_duplicate(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """SQS redelivers. The alert's identity is its content, so it lands on the
    row that now says "transfer" and is refused there — it neither becomes a
    second expense nor turns the declared row back into spending."""
    savings = _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )

    again = record.execute(
        RecordMovementCommand(
            user_id=USER_ID,
            bank="bancolombia",
            direction=MovementDirection.OUTGOING,
            amount=_cop(PAYMENT),
            occurred_at=PAID_AT,
            counterparty="BANCO COMERCIAL AV VILLAS",
            instrument_kind="account",
            last_four="5261",
        ),
    )
    stored = ledger.find(user_id=USER_ID, transaction_id=movement_id)

    assert again.outcome is Outcome.DUPLICATE
    assert stored is not None
    assert stored.is_transfer is True
    assert _balance(accounts, savings) == Decimal("1374267.00")
    assert len(ledger.list_all(USER_ID)) == 2


def test_declaring_twice_writes_one_side_and_moves_the_debt_once(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    command = DeclareTransferCommand(
        user_id=USER_ID,
        transaction_id=movement_id,
        counterpart_account_id=card.id,
    )

    transfers.declare(command)
    transfers.declare(command)

    assert _balance(accounts, card) == Decimal("0.00")
    assert len(ledger.list_all(USER_ID)) == 2
    assert _rebuilt_matches(accounts, ledger, card)


def test_undoing_it_erases_the_written_side_and_gives_the_debt_back(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings = _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    declared = transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )
    written_id = declared.movements[1].id.value

    undone = transfers.undo(user_id=USER_ID, transaction_id=movement_id)
    restored = ledger.find(user_id=USER_ID, transaction_id=movement_id)

    assert undone.erased == [written_id]
    assert ledger.find(user_id=USER_ID, transaction_id=written_id) is None
    assert restored is not None
    assert restored.transfer is None
    assert _balance(accounts, card) == Decimal(PAYMENT)
    assert _balance(accounts, savings) == Decimal("1374267.00")
    assert _spent(ledger, accounts) == Decimal(PAYMENT)
    assert _rebuilt_matches(accounts, ledger, card)


def test_undoing_from_the_written_side_undoes_the_whole_declaration(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    declared = transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )

    transfers.undo(user_id=USER_ID, transaction_id=declared.movements[1].id.value)
    restored = ledger.find(user_id=USER_ID, transaction_id=movement_id)

    assert restored is not None
    assert restored.transfer is None
    assert _balance(accounts, card) == Decimal(PAYMENT)
    assert len(ledger.list_all(USER_ID)) == 1


def test_a_second_undo_is_refused_rather_than_unwinding_twice(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=movement_id,
            counterpart_account_id=card.id,
        ),
    )
    transfers.undo(user_id=USER_ID, transaction_id=movement_id)

    with pytest.raises(TransferDeclarationError):
        transfers.undo(user_id=USER_ID, transaction_id=movement_id)

    assert _balance(accounts, card) == Decimal(PAYMENT)


# ------------------------------------------ both banks emailed: pair the two


def test_pairing_two_movements_moves_no_balance_and_counts_neither(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Bancolombia says the money left, Lulo says it arrived. Recorded as they
    come, the month shows it spent and earned; writing a side on Lulo would
    count it twice there. Pairing is the only answer that is right."""
    savings = _open_savings(manage_accounts)
    lulo = _open_lulo(manage_accounts)
    sent = _bancolombia_alert(
        record,
        amount=_cop("504179.72"),
        counterparty="LULO BANK S A",
    )
    arrived = _bancolombia_alert(
        record,
        bank="lulo bank",
        direction=MovementDirection.INCOMING,
        amount=_cop("504179.72"),
        occurred_at=PosixTime.from_epoch_seconds(PAID_AT.as_epoch_seconds() + 60),
        counterparty="NOMBRE APELLIDO",
        last_four="0042",
    )
    options = transfers.options(user_id=USER_ID, transaction_id=sent)

    transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=sent,
            counterpart_movement_id=arrived,
        ),
    )

    assert [candidate.id.value for candidate in options.counterparts] == [arrived]
    assert _balance(accounts, savings) == Decimal("4495820.28")
    assert _balance(accounts, lulo) == Decimal("504179.72")
    assert _spent(ledger, accounts) == Decimal(0)
    assert len(ledger.list_all(USER_ID)) == 2


def test_undoing_a_pair_puts_both_movements_back(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    savings = _open_savings(manage_accounts)
    _open_lulo(manage_accounts)
    sent = _bancolombia_alert(record, amount=_cop("504179.72"))
    arrived = _bancolombia_alert(
        record,
        bank="lulo bank",
        direction=MovementDirection.INCOMING,
        amount=_cop("504179.72"),
        counterparty="NOMBRE APELLIDO",
        last_four="0042",
    )
    transfers.declare(
        DeclareTransferCommand(
            user_id=USER_ID,
            transaction_id=sent,
            counterpart_movement_id=arrived,
        ),
    )

    transfers.undo(user_id=USER_ID, transaction_id=arrived)

    for movement_id in (sent, arrived):
        stored = ledger.find(user_id=USER_ID, transaction_id=movement_id)
        assert stored is not None
        assert stored.transfer is None

    assert _balance(accounts, savings) == Decimal("4495820.28")
    assert len(ledger.list_all(USER_ID)) == 2


# ----------------------------------------------------------------- refusals


def test_a_movement_a_bill_counts_as_its_charge_is_refused(
    manage_accounts: ManageAccountsUseCase,
    record: RecordMovementUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    savings = _open_savings(manage_accounts)
    card = _open_card(manage_accounts)
    movement_id = _bancolombia_alert(record)
    bill = ManageBillsUseCase(bills=bills, accounts=accounts, charges=ledger).declare(
        DeclareBillCommand(
            user_id=USER_ID,
            name="Tarjeta AV Villas",
            amount=_cop(PAYMENT),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date(2025, 12, 30),
            account_id=savings.id,
        ),
    )
    SettleBillChargeUseCase(
        bills=bills,
        accounts=accounts,
        charges=ledger,
        transactions=ManageTransactionsUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=RecordingPublisher(),
        ),
    ).link(
        user_id=USER_ID,
        bill_id=bill.bill.id,
        period=dt.date(2025, 12, 30),
        movement_id=movement_id,
    )

    options = transfers.options(user_id=USER_ID, transaction_id=movement_id)

    with pytest.raises(TransferDeclarationError, match="bill"):
        transfers.declare(
            DeclareTransferCommand(
                user_id=USER_ID,
                transaction_id=movement_id,
                counterpart_account_id=card.id,
            ),
        )

    assert options.refusal is not None
    assert options.refusal.value == "linked_to_bill"
    assert _balance(accounts, card) == Decimal(PAYMENT)


def test_a_transfer_entered_as_one_cannot_be_undone(
    manage_accounts: ManageAccountsUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """A leg entered as a transfer from the start has no earlier version to
    put back; `DELETE` is what removes it, and undo must not quietly turn it
    into income."""
    card = _open_card(manage_accounts)
    leg = ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER_ID,
            role=TransferRole.DESTINATION,
            amount=_cop(PAYMENT),
            occurred_at=PAID_AT,
            counterparty="Nequi",
            account_id=card.id,
        ),
    )

    with pytest.raises(TransferDeclarationError, match="not declared"):
        transfers.undo(user_id=USER_ID, transaction_id=leg.id.value)

    stored = ledger.find(user_id=USER_ID, transaction_id=leg.id.value)
    assert stored is not None
    assert stored.is_transfer is True


def test_a_leg_entered_as_a_transfer_is_refused_a_second_declaration(
    manage_accounts: ManageAccountsUseCase,
    transfers: DeclareTransferUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    card = _open_card(manage_accounts)
    leg = ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=RecordingPublisher(),
    ).enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER_ID,
            role=TransferRole.DESTINATION,
            amount=_cop(PAYMENT),
            occurred_at=PAID_AT,
            counterparty="Nequi",
            account_id=card.id,
        ),
    )

    options = transfers.options(user_id=USER_ID, transaction_id=leg.id.value)

    with pytest.raises(TransferDeclarationError, match="already"):
        transfers.declare(
            DeclareTransferCommand(user_id=USER_ID, transaction_id=leg.id.value),
        )

    assert options.refusal is not None
    assert options.refusal.value == "already_transfer"
