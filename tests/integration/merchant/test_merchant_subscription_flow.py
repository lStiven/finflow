"""End-to-end proof that a parsed transaction reaches a canonical merchant.

The whole chain is real: ingestion's translator puts `TransactionExtracted` on
the bus, merchant's own rule routes it to merchant's own queue, and merchant's
worker drains it into the table. A rule whose pattern does not match delivers
nowhere and reports nothing, so nothing short of this proves the subscription
is wired.
"""

from collections.abc import Sequence
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.ingestion.application.integration_events import (
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import TransactionExtracted
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    NotificationId,
)
from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.domain.value_objects import AliasFingerprint
from personal_finance.contexts.merchant.infrastructure.messaging.sqs_worker import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
    SQSMerchantWorker,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBMerchantRepository,
    DynamoDBProcessedEventStore,
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
    MERCHANT_EVENTS_RULE,
    MERCHANT_EVENTS_TARGET_ID,
    provision_context_subscription,
    provision_event_bus,
    provision_table,
)


BUS = "finflow"
QUEUE = "merchant-events"
TABLE_NAME = "merchants"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = EmailMessageId("<abc@bancolombia.com.co>")
NOTIFICATION_ID = NotificationId.for_message(user_id=USER_ID, message_id=MESSAGE_ID)


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
        rule_name=MERCHANT_EVENTS_RULE,
        target_id=MERCHANT_EVENTS_TARGET_ID,
        event_pattern={
            "source": [INGESTION_SOURCE],
            "detail-type": [TRANSACTION_EXTRACTED],
        },
    )

    return url


@pytest.fixture
def repository(dynamodb_client: DynamoDBClient) -> DynamoDBMerchantRepository:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
    )

    return DynamoDBMerchantRepository(client=dynamodb_client, table_name=TABLE_NAME)


@pytest.fixture
def worker(
    dynamodb_client: DynamoDBClient,
    sqs_client: SQSClient,
    queue_url: str,
    repository: DynamoDBMerchantRepository,
) -> SQSMerchantWorker:
    return SQSMerchantWorker(
        client=sqs_client,
        queue_url=queue_url,
        use_case=ResolveMerchantUseCase(
            repository=repository,
            processed_events=DynamoDBProcessedEventStore(
                client=dynamodb_client,
                table_name=TABLE_NAME,
            ),
            event_publisher=NullEventPublisher(),
        ),
    )


def _extracted(counterparty: str) -> TransactionExtracted:
    return TransactionExtracted(
        notification_id=NOTIFICATION_ID,
        user_id=USER_ID,
        message_id=MESSAGE_ID,
        transaction=ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=TransactionDirection.OUTGOING,
            amount=Money(amount=Decimal("29259.00"), currency=Currency.COP),
            occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
            counterparty=counterparty,
            bank="bancolombia",
        ),
    )


@pytest.fixture
def publisher(eventbridge_client: EventBridgeClient) -> EventBridgeEventPublisher:
    return EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IngestionIntegrationEventTranslator(),
    )


def test_a_parsed_transaction_becomes_a_merchant(
    publisher: EventBridgeEventPublisher,
    worker: SQSMerchantWorker,
    repository: DynamoDBMerchantRepository,
) -> None:
    publisher.publish([_extracted("TIENDAS ARA 123")])

    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    merchant = repository.find_by_alias(
        user_id=USER_ID,
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
    )
    assert merchant is not None
    assert merchant.display_name == "Tiendas Ara 123"


def test_a_second_spelling_joins_the_merchant_it_belongs_to(
    publisher: EventBridgeEventPublisher,
    worker: SQSMerchantWorker,
    repository: DynamoDBMerchantRepository,
) -> None:
    publisher.publish([_extracted("TIENDAS ARA 123")])
    worker.poll_once(wait_seconds=0)
    publisher.publish([_extracted("ARA CALLE 80")])
    worker.poll_once(wait_seconds=0)

    assert len(repository.list_by_user(USER_ID)) == 1


def test_the_same_event_delivered_twice_is_counted_once(
    publisher: EventBridgeEventPublisher,
    worker: SQSMerchantWorker,
    repository: DynamoDBMerchantRepository,
) -> None:
    event = _extracted("TIENDAS ARA 123")
    publisher.publish([event])
    worker.poll_once(wait_seconds=0)
    # The same domain event again, as a redelivery would carry it.
    publisher.publish([event])
    worker.poll_once(wait_seconds=0)

    merchant = repository.find_by_alias(
        user_id=USER_ID,
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
    )
    assert merchant is not None
    assert merchant.times_seen == 1
