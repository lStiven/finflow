"""End-to-end proof that a domain event actually reaches a subscriber.

Publishing to a bus with no rule succeeds and delivers nowhere, so a test
that only asserted `put_events` returned cleanly would prove nothing. These
go through the real path: publisher -> bus -> rule -> SQS target -> read back.
"""

from decimal import Decimal
import json

from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.identity.application.integration_events import (
    IdentityIntegrationEventTranslator,
)
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.contexts.ingestion.application.integration_events import (
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationQueued,
    TransactionExtracted,
)
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
    provision_event_bus,
    provision_integration_event_subscription,
)


BUS = "finflow"
QUEUE = "integration-events"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = EmailMessageId("<abc@bancolombia.com.co>")
NOTIFICATION_ID = NotificationId.for_message(user_id=USER_ID, message_id=MESSAGE_ID)


@pytest.fixture
def bus_tap(eventbridge_client: EventBridgeClient, sqs_client: SQSClient) -> str:
    provision_event_bus(eventbridge_client, event_bus_name=BUS)

    return provision_integration_event_subscription(
        eventbridge_client,
        sqs_client,
        event_bus_name=BUS,
        queue_name=QUEUE,
    )


def _read(sqs_client: SQSClient, queue_url: str) -> list[dict[str, object]]:
    received = sqs_client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
    )

    return [
        json.loads(body)
        for message in received.get("Messages", [])
        if (body := message.get("Body")) is not None
    ]


def _transaction() -> ExtractedTransaction:
    return ExtractedTransaction(
        kind=TransactionKind.CARD_PURCHASE,
        direction=TransactionDirection.OUTGOING,
        amount=Money(amount=Decimal("45000"), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
        counterparty="EXITO CALI",
        instrument=Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="1234"),
    )


def test_an_extracted_transaction_reaches_the_subscriber(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
    bus_tap: str,
) -> None:
    publisher = EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IngestionIntegrationEventTranslator(),
    )

    publisher.publish(
        [
            TransactionExtracted(
                notification_id=NOTIFICATION_ID,
                user_id=USER_ID,
                message_id=MESSAGE_ID,
                transaction=_transaction(),
            ),
        ],
    )

    envelopes = _read(sqs_client, bus_tap)

    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope["source"] == "finflow.ingestion"
    assert envelope["detail-type"] == "TransactionExtracted"

    detail = envelope["detail"]
    assert isinstance(detail, dict)
    assert detail["user_id"] == str(USER_ID.value)
    assert detail["version"] == 1
    assert detail["transaction"]["counterparty"] == "EXITO CALI"
    assert detail["transaction"]["amount"] == "45000"


def test_a_user_registration_reaches_the_subscriber(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
    bus_tap: str,
) -> None:
    publisher = EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IdentityIntegrationEventTranslator(),
    )

    publisher.publish(
        [UserRegistered(user_id=USER_ID, email=Email("person@example.com"))],
    )

    envelopes = _read(sqs_client, bus_tap)

    assert len(envelopes) == 1
    # Both contexts publish onto the same bus, and one prefix rule picks up
    # every one of them.
    assert envelopes[0]["source"] == "finflow.identity"
    assert envelopes[0]["detail-type"] == "UserRegistered"


def test_an_internal_event_never_reaches_the_bus(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
    bus_tap: str,
) -> None:
    publisher = EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=IngestionIntegrationEventTranslator(),
    )

    publisher.publish(
        [
            BankNotificationQueued(
                notification_id=NOTIFICATION_ID,
                user_id=USER_ID,
                message_id=MESSAGE_ID,
            ),
        ],
    )

    assert _read(sqs_client, bus_tap) == []


def test_provisioning_the_subscription_twice_is_safe(
    eventbridge_client: EventBridgeClient,
    sqs_client: SQSClient,
    bus_tap: str,
) -> None:
    del bus_tap

    provision_integration_event_subscription(
        eventbridge_client,
        sqs_client,
        event_bus_name=BUS,
        queue_name=QUEUE,
    )

    rules = eventbridge_client.list_rules(EventBusName=BUS)["Rules"]

    assert len(rules) == 1
