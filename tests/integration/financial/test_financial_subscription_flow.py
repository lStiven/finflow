"""End-to-end proof that a parsed bank alert becomes a durable balance.

The whole chain is real. Ingestion's own translator puts `TransactionExtracted`
on a real EventBridge bus, Financial's own rule routes it to Financial's own
queue, Financial's own worker drains it, and the balance is read back out of
DynamoDB rather than out of the object that wrote it. A rule whose pattern
does not match delivers nowhere and reports nothing, and an in-memory balance
proves nothing about a restart, so nothing short of this proves the pipeline.

Nothing in the payload is hand-built: a field ingestion renames, or a number it
serializes differently, breaks here rather than in production.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
import json

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    OpenAccountCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    RecordMovementUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountKind,
    BalanceSign,
    MovementDirection,
    TransactionStatus,
)
from personal_finance.contexts.financial.infrastructure.messaging.sqs_worker import (
    SQSFinancialWorker,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.contexts.ingestion.application.integration_events import (
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import TransactionExtracted
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    NotificationId,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.eventbridge import (
    EventBridgeEventPublisher,
)
from personal_finance.shared.infrastructure.aws.provisioning import (
    FINANCIAL_EVENTS_RULE,
    FINANCIAL_EVENTS_TARGET_ID,
    provision_context_subscription,
    provision_event_bus,
    provision_table,
)


BUS = "finflow"
QUEUE = "financial-events"
TABLE_NAME = "financial"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")
MESSAGE_ID = EmailMessageId("<abc@bancolombia.com.co>")
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))

CREDIT_CARD = Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="7653")
DEBIT_CARD = Instrument(kind=InstrumentKind.DEBIT_CARD, last_four="1234")


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def queue_url(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
) -> str:
    provision_event_bus(eventbridge_client, event_bus_name=BUS)
    url, _ = provision_context_subscription(
        eventbridge_client,
        sqs_client,
        event_bus_name=BUS,
        queue_name=QUEUE,
        rule_name=FINANCIAL_EVENTS_RULE,
        target_id=FINANCIAL_EVENTS_TARGET_ID,
        event_pattern={
            "source": ["finflow.ingestion"],
            "detail-type": ["TransactionExtracted"],
        },
    )

    return url


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
def accounts(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def ledger(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(client=dynamodb_client, table_name=table)


@pytest.fixture
def worker(
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> SQSFinancialWorker:
    return SQSFinancialWorker(
        client=sqs_client,
        queue_url=queue_url,
        use_case=RecordMovementUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=NullEventPublisher(),
        ),
    )


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


def _declare_card(
    manage_accounts: ManageAccountsUseCase,
    *,
    user_id: UserId = USER_ID,
    name: str = "Tarjeta de crédito",
    kind: AccountKind = AccountKind.CREDIT_CARD,
    instrument: Instrument = CREDIT_CARD,
) -> Account:
    assert instrument.last_four is not None

    return manage_accounts.open(
        OpenAccountCommand(
            user_id=user_id,
            name=name,
            kind=kind,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=instrument.kind.value,
            last_four=instrument.last_four,
        ),
    )


@pytest.fixture
def publisher(eventbridge_client: EventBridgeClient) -> EventBridgeEventPublisher:
    return EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IngestionIntegrationEventTranslator(),
    )


def _alert(
    *,
    counterparty: str = "TIENDAS ARA 123",
    amount: str = "50000.50",
    instrument: Instrument | None = CREDIT_CARD,
    user_id: UserId = USER_ID,
    direction: TransactionDirection = TransactionDirection.OUTGOING,
) -> TransactionExtracted:
    return TransactionExtracted(
        notification_id=NotificationId.for_message(
            user_id=user_id,
            message_id=MESSAGE_ID,
        ),
        user_id=user_id,
        message_id=MESSAGE_ID,
        transaction=ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=direction,
            amount=Money(amount=Decimal(amount), currency=Currency.COP),
            occurred_at=PURCHASE_TIME,
            counterparty=counterparty,
            bank="Bancolombia",
            instrument=instrument,
        ),
    )


def _publish(
    publisher: EventBridgeEventPublisher,
    events: Sequence[TransactionExtracted],
) -> None:
    publisher.publish(list(events))


def _still_held(sqs_client: SQSClient, queue_url: str) -> int:
    """How many messages the queue is holding, visible or not.

    Counted rather than re-received: a message the worker left alone is inside
    its visibility timeout, which is invisible, not gone. Deleting it is the
    thing that would have lost it.
    """
    attributes = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )["Attributes"]

    return int(attributes["ApproximateNumberOfMessages"]) + int(
        attributes["ApproximateNumberOfMessagesNotVisible"],
    )


def _fingerprint(instrument: Instrument) -> AccountFingerprint:
    assert instrument.last_four is not None

    return AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=instrument.kind.value,
        last_four=instrument.last_four,
    )


def test_a_bank_alert_becomes_a_balance_that_survives_the_process(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    _declare_card(manage_accounts)
    _publish(publisher, [_alert()])

    result = worker.poll_once(wait_seconds=0)

    assert result.received == 1
    assert result.handled == 1

    # Read back out of DynamoDB, not out of the object that wrote it.
    account = accounts.find_by_fingerprint(
        user_id=USER_ID,
        fingerprint=_fingerprint(CREDIT_CARD),
    )

    assert account is not None
    assert account.name == "Tarjeta de crédito"
    assert account.kind is AccountKind.CREDIT_CARD
    assert account.category is AccountCategory.LIABILITY
    # Spending on a credit card raises what it owes, and the cents survived
    # both the bus and the table.
    assert account.balance.amount.amount == Decimal("50000.50")
    assert account.balance.sign is BalanceSign.POSITIVE
    assert account.movements_applied == 1


def test_an_alert_with_no_account_declared_is_recorded_and_waits(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Finflow with no accounts at all is a complete answer, not a broken one.

    Somebody who only wants to see what comes in and what goes out declares
    nothing, and every movement is recorded and stays unassigned.
    """
    _publish(publisher, [_alert()])

    assert worker.poll_once(wait_seconds=0).handled == 1
    assert accounts.list_by_user(USER_ID) == []
    assert len(ledger.list_unassigned(USER_ID)) == 1


def test_declaring_an_account_adopts_what_already_arrived(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    manage_accounts: ManageAccountsUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Adding an account is retroactive.

    The alerts that arrived under that card before it existed are still in the
    ledger, and they belong to it. The account opens with the history it
    already had rather than at zero.
    """
    _publish(
        publisher,
        [_alert(), _alert(counterparty="EXITO EXPRESS", amount="20000")],
    )

    assert worker.poll_once(wait_seconds=0).handled == 2
    assert len(ledger.list_unassigned(USER_ID)) == 2

    declared = _declare_card(manage_accounts)

    assert ledger.list_unassigned(USER_ID) == []

    stored = accounts.find(user_id=USER_ID, account_id=declared.id)

    assert stored is not None
    assert stored.movements_applied == 2
    assert stored.balance.amount.amount == Decimal("70000.50")
    assert len(ledger.list_movements(user_id=USER_ID, account_id=declared.id)) == 2


def test_the_same_alert_delivered_twice_moves_the_balance_once(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """The conditional write, doing the only job it exists for.

    Two deliveries of one purchase are two integration events with two
    `event_id`s. The movement's identity comes from its content, so both write
    the same ledger key — and the second write is refused whole, balance
    included.
    """
    _declare_card(manage_accounts)
    _publish(publisher, [_alert(), _alert()])

    assert worker.poll_once(wait_seconds=0).received == 2

    account = accounts.find_by_fingerprint(
        user_id=USER_ID,
        fingerprint=_fingerprint(CREDIT_CARD),
    )

    assert account is not None
    assert account.balance.amount.amount == Decimal("50000.50")
    assert account.movements_applied == 1
    assert len(ledger.list_movements(user_id=USER_ID, account_id=account.id)) == 1


def test_the_balance_replays_from_the_rows_behind_it(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """The ledger is the authority, and this is what makes that true.

    A running total nobody can retrace is a total nobody can repair, so
    replaying the stored rows must reproduce the stored number exactly.
    """
    _declare_card(manage_accounts)
    _publish(
        publisher,
        [
            _alert(),
            _alert(counterparty="EXITO EXPRESS", amount="20000"),
            _alert(
                counterparty="PAGO TARJETA",
                amount="30000",
                direction=TransactionDirection.INCOMING,
            ),
        ],
    )

    assert worker.poll_once(wait_seconds=0).handled == 3

    account = accounts.find_by_fingerprint(
        user_id=USER_ID,
        fingerprint=_fingerprint(CREDIT_CARD),
    )

    assert account is not None

    stored = account.balance
    movements = ledger.list_movements(user_id=USER_ID, account_id=account.id)
    account.rebuild(movement.as_movement() for movement in movements)

    assert len(movements) == 3
    # 50000.50 spent + 20000 spent - 30000 paid off.
    assert account.balance.amount.amount == Decimal("40000.50")
    assert account.balance == stored


def test_two_cards_at_one_bank_keep_two_balances(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    _declare_card(manage_accounts)
    _declare_card(
        manage_accounts,
        name="Cuenta de ahorros",
        kind=AccountKind.SAVINGS,
        instrument=DEBIT_CARD,
    )
    _publish(
        publisher,
        [_alert(), _alert(counterparty="EXITO EXPRESS", instrument=DEBIT_CARD)],
    )

    assert worker.poll_once(wait_seconds=0).handled == 2

    held = accounts.list_by_user(USER_ID)

    assert len(held) == 2

    by_category = {account.category: account for account in held}

    # The debit card is read as the savings account it draws on, so the same
    # spending subtracts there and adds on the credit card.
    assert by_category[AccountCategory.ASSET].balance.sign is BalanceSign.NEGATIVE
    assert by_category[AccountCategory.LIABILITY].balance.sign is BalanceSign.POSITIVE


def test_an_alert_naming_no_card_is_kept_outside_every_balance(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The unassigned path, all the way to storage.

    The money moved and the record is kept; what is missing is only which
    account it belongs to, and that waits for a person rather than a guess.
    """
    _publish(publisher, [_alert(instrument=None)])

    assert worker.poll_once(wait_seconds=0).handled == 1
    assert accounts.list_by_user(USER_ID) == []

    unassigned = ledger.list_unassigned(USER_ID)

    assert len(unassigned) == 1
    movement = unassigned[0]
    assert movement.status is TransactionStatus.UNASSIGNED
    assert movement.direction is MovementDirection.OUTGOING
    assert movement.amount.amount == Decimal("50000.50")
    assert movement.counterparty == "TIENDAS ARA 123"


def test_two_users_sharing_a_cards_digits_keep_separate_accounts(
    publisher: EventBridgeEventPublisher,
    worker: SQSFinancialWorker,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """The isolation `AccountFingerprint` cannot provide on its own.

    Its key is bank, instrument and last four — no user. Two people at one
    bank whose cards end in the same digits produce the same fingerprint, and
    only the user-scoped lookup keeps their money apart.
    """
    _declare_card(manage_accounts)
    _declare_card(manage_accounts, user_id=OTHER_USER)
    _publish(publisher, [_alert(), _alert(user_id=OTHER_USER)])

    assert worker.poll_once(wait_seconds=0).handled == 2

    mine = accounts.list_by_user(USER_ID)
    theirs = accounts.list_by_user(OTHER_USER)

    assert len(mine) == 1
    assert len(theirs) == 1
    assert mine[0].id != theirs[0].id
    # One fingerprint, two accounts: the scoping is what separated them.
    assert mine[0].fingerprints == theirs[0].fingerprints


def test_a_version_this_deploy_cannot_read_stays_on_the_queue(
    sqs_client: SQSClient,
    worker: SQSFinancialWorker,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
) -> None:
    """A newer deploy wrote it, and a newer worker may still take it.

    Discarding would lose a real movement; reading it as v1 would book
    whatever v2 changed straight onto a balance.
    """
    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "source": "finflow.ingestion",
                "detail-type": "TransactionExtracted",
                "detail": {
                    "version": 2,
                    "event_id": "3f1b7c2e-1111-4d63-9c2e-9d3b1f7c5a10",
                    "user_id": str(USER_ID.value),
                    "transaction": {
                        "direction": "outgoing",
                        "amount": "50000",
                        "currency": "COP",
                        "occurred_at": PURCHASE_TIME.as_epoch_seconds(),
                        "counterparty": "TIENDAS ARA",
                        "bank": "bancolombia",
                        "instrument": {"kind": "credit_card", "last_four": "7653"},
                    },
                },
            },
        ),
    )

    result = worker.poll_once(wait_seconds=0)

    assert result.received == 1
    assert result.handled == 0
    assert result.rejected == 1
    assert accounts.list_by_user(USER_ID) == []

    # Still held for a worker that understands it.
    assert _still_held(sqs_client, queue_url) == 1


def test_a_currency_this_deploy_cannot_read_stays_on_the_queue(
    sqs_client: SQSClient,
    worker: SQSFinancialWorker,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
) -> None:
    """`Currency` knows two members today.

    An alert in a third is a real movement the very next deploy would read.
    Deleting it to save a redelivery would destroy it; the dead-letter queue
    is where something genuinely unreadable belongs — somewhere a person sees
    it.
    """
    sqs_client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(
            {
                "source": "finflow.ingestion",
                "detail-type": "TransactionExtracted",
                "detail": {
                    "version": 1,
                    "event_id": "3f1b7c2e-2222-4d63-9c2e-9d3b1f7c5a10",
                    "user_id": str(USER_ID.value),
                    "transaction": {
                        "direction": "outgoing",
                        "amount": "40",
                        "currency": "EUR",
                        "occurred_at": PURCHASE_TIME.as_epoch_seconds(),
                        "counterparty": "CARREFOUR",
                        "bank": "bancolombia",
                        "instrument": {"kind": "credit_card", "last_four": "7653"},
                    },
                },
            },
        ),
    )

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert accounts.list_by_user(USER_ID) == []
    assert _still_held(sqs_client, queue_url) == 1


def test_a_payload_that_will_never_parse_is_dropped_rather_than_retried(
    sqs_client: SQSClient,
    worker: SQSFinancialWorker,
    queue_url: str,
) -> None:
    sqs_client.send_message(QueueUrl=queue_url, MessageBody="not json at all")

    result = worker.poll_once(wait_seconds=0)

    assert result.received == 1
    assert result.rejected == 1
    assert (
        sqs_client.receive_message(
            QueueUrl=queue_url,
            WaitTimeSeconds=0,
        ).get("Messages", [])
        == []
    )
