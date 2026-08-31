"""End to end through the Lambda entry points instead of the polling loops.

The chain is the real one: ingestion's own translator publishes to a real
EventBridge bus, the context's own rule routes it to its own queue, and the
message is then delivered the way Lambda delivers it — as a batch event whose
records were never received by our code, and whose deletion is decided by what
the response reports rather than by a `delete_message` call.

That inversion is the whole reason this file exists. Under `poll_once` a
message survives by default and leaving it alone is safe; under Lambda a
message is deleted by default and only the response keeps it. The same worker
sits behind both, so nothing but a delivery test can tell whether the new path
kept that promise.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import cast

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.financial.application.commands import OpenAccountCommand
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    RecordMovementUseCase,
    RecordTransferUseCase,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountKind,
    InstrumentKind as FinancialInstrumentKind,
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
from personal_finance.contexts.financial.presentation.awslambda import financial_handler
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
from personal_finance.shared.infrastructure.messaging.lambda_batch import (
    SQSEvent,
    SQSRecord,
)


BUS = "finflow"
QUEUE = "financial-events"
TABLE_NAME = "financial"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = EmailMessageId("<lambda-flow@bancolombia.com.co>")
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))
CREDIT_CARD = Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="7653")


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
def accounts(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def ledger(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(client=dynamodb_client, table_name=table)


@pytest.fixture
def lambda_worker(
    monkeypatch: pytest.MonkeyPatch,
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> SQSFinancialWorker:
    """The handler's own worker, wired to moto rather than to real AWS.

    `build_worker` is what the handler would call in production; replacing it
    here is the only seam this test touches. Everything past it — the worker,
    the use case, the ledger, DynamoDB — is the production object.
    """
    worker = SQSFinancialWorker(
        client=sqs_client,
        queue_url=queue_url,
        use_case=RecordMovementUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=NullEventPublisher(),
        ),
        transfer_use_case=RecordTransferUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=NullEventPublisher(),
        ),
    )
    monkeypatch.setattr(financial_handler, "build_worker", lambda: worker)
    financial_handler.get_worker.cache_clear()

    return worker


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
    message_id: EmailMessageId = MESSAGE_ID,
) -> TransactionExtracted:
    return TransactionExtracted(
        notification_id=NotificationId.for_message(
            user_id=USER_ID,
            message_id=message_id,
        ),
        user_id=USER_ID,
        message_id=message_id,
        transaction=ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=TransactionDirection.OUTGOING,
            amount=Money(amount=Decimal(amount), currency=Currency.COP),
            occurred_at=PURCHASE_TIME,
            counterparty=counterparty,
            bank="Bancolombia",
            instrument=CREDIT_CARD,
        ),
    )


def _declare_card(manage_accounts: ManageAccountsUseCase) -> None:
    assert CREDIT_CARD.last_four is not None
    manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Tarjeta de crédito",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=FinancialInstrumentKind(CREDIT_CARD.kind.value),
            last_four=CREDIT_CARD.last_four,
        ),
    )


def _drain_queue_as_lambda_event(
    sqs_client: SQSClient,
    queue_url: str,
    *,
    extra_bodies: Sequence[str] = (),
) -> SQSEvent:
    """Build the event Lambda would deliver, from what the rule really routed.

    The bodies are whatever EventBridge put on the queue — not something this
    test wrote — so a field the translator renames breaks here. `extra_bodies`
    appends messages that never came from the bus, which is how a poisoned
    batch is assembled.
    """
    received = sqs_client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=0,
    ).get("Messages", [])

    records: list[SQSRecord] = []

    for message in received:
        message_id = message.get("MessageId")
        body = message.get("Body")

        assert message_id is not None, "SQS returned a message with no id"
        assert body is not None, "SQS returned a message with no body"

        records.append({"messageId": message_id, "body": body})

    records.extend(
        {"messageId": f"synthetic-{index}", "body": body}
        for index, body in enumerate(extra_bodies)
    )

    return cast(SQSEvent, {"Records": records})


def _balance(accounts: DynamoDBAccountRepository) -> Money:
    assert CREDIT_CARD.last_four is not None
    account = accounts.find_by_fingerprint(
        user_id=USER_ID,
        fingerprint=AccountFingerprint.from_parts(
            bank="bancolombia",
            instrument_kind=FinancialInstrumentKind(CREDIT_CARD.kind.value),
            last_four=CREDIT_CARD.last_four,
        ),
    )

    assert account is not None

    # `Balance.amount` is a `Money`; comparing the whole thing keeps the
    # currency inside the assertion instead of quietly dropping it.
    return account.balance.amount


def test_a_bus_event_becomes_a_durable_balance_through_the_lambda_handler(
    publisher: EventBridgeEventPublisher,
    lambda_worker: SQSFinancialWorker,
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    del lambda_worker
    _declare_card(manage_accounts)
    publisher.publish([_alert()])

    event = _drain_queue_as_lambda_event(sqs_client, queue_url)

    assert len(event["Records"]) == 1

    response = financial_handler.handler(event, None)

    # Empty is what tells Lambda to delete it. Anything reported here would be
    # redelivered instead.
    assert response == {"batchItemFailures": []}
    assert _balance(accounts) == Money(
        amount=Decimal("50000.50"), currency=Currency.COP
    )


def test_only_the_unreadable_message_comes_back_from_a_poisoned_batch(
    publisher: EventBridgeEventPublisher,
    lambda_worker: SQSFinancialWorker,
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """A batch mixes a real movement with two that cannot be acted on.

    Under Lambda the failure of one must not drag the others back: the good
    one has already moved a balance, and redelivering it would ask the ledger
    to refuse the same movement again forever.

    The two spoiled bodies are the two halves of the deliberate distinction in
    `handle`. Malformed is deleted — it will never become readable. A version
    this deploy does not know is kept, because a newer one may read it, and
    deleting it would destroy a real movement.
    """
    del lambda_worker
    _declare_card(manage_accounts)
    publisher.publish([_alert()])

    delivered = _drain_queue_as_lambda_event(sqs_client, queue_url)

    assert len(delivered["Records"]) == 1

    # Built by mutating what EventBridge actually delivered rather than by
    # hand: a payload assembled here could pass this test while being nothing
    # ingestion would ever write.
    from_the_future = json.loads(delivered["Records"][0]["body"])
    from_the_future["detail"]["version"] = 999

    event = _drain_queue_as_lambda_event(
        sqs_client,
        queue_url,
        extra_bodies=["not json at all", json.dumps(from_the_future)],
    )
    event["Records"] = [*delivered["Records"], *event["Records"]]

    assert len(event["Records"]) == 3

    response = financial_handler.handler(event, None)
    reported = {failure["itemIdentifier"] for failure in response["batchItemFailures"]}

    assert reported == {"synthetic-1"}
    assert _balance(accounts) == Money(
        amount=Decimal("50000.50"), currency=Currency.COP
    )


def test_a_redelivered_batch_moves_the_balance_once(
    publisher: EventBridgeEventPublisher,
    lambda_worker: SQSFinancialWorker,
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """Lambda delivers at least once, and retries a whole batch.

    The polling loop was serial; Lambda runs batches concurrently, so the same
    movement arriving twice is no longer a rare race but the ordinary case.
    The ledger's conditional write is what has to hold, and this reads the
    balance back out of DynamoDB to prove it did.
    """
    del lambda_worker
    _declare_card(manage_accounts)
    publisher.publish([_alert()])

    event = _drain_queue_as_lambda_event(sqs_client, queue_url)

    first = financial_handler.handler(event, None)
    # The identical event, as a redrive would replay it.
    second = financial_handler.handler(event, None)

    assert first == {"batchItemFailures": []}
    assert second == {"batchItemFailures": []}
    assert _balance(accounts) == Money(
        amount=Decimal("50000.50"), currency=Currency.COP
    )


def test_two_distinct_alerts_in_one_batch_both_land(
    publisher: EventBridgeEventPublisher,
    lambda_worker: SQSFinancialWorker,
    sqs_client: SQSClient,
    queue_url: str,
    accounts: DynamoDBAccountRepository,
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """Batching is new: `poll_once` also read ten at a time, but Lambda is the
    first caller whose response has to say something about each one.
    """
    del lambda_worker
    _declare_card(manage_accounts)
    publisher.publish(
        [
            _alert(amount="10000", message_id=EmailMessageId("<one@bank.co>")),
            _alert(amount="25000", message_id=EmailMessageId("<two@bank.co>")),
        ],
    )

    event = _drain_queue_as_lambda_event(sqs_client, queue_url)

    assert len(event["Records"]) == 2

    response = financial_handler.handler(event, None)

    assert response == {"batchItemFailures": []}
    assert _balance(accounts) == Money(amount=Decimal("35000"), currency=Currency.COP)
