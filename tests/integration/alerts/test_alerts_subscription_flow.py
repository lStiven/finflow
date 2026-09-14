"""End-to-end proof that a recorded movement reaches somebody's phone.

The whole chain is real: financial's translator puts `MovementRecorded` on the
bus, alerts' own rule routes it to alerts' own queue, and alerts' worker
drains it into a message. **A rule whose pattern does not match delivers
nowhere and reports nothing**, so nothing short of this proves the
subscription is wired — a unit test on the worker passes just as happily with
a pattern that no event will ever match.

The second thing it proves is that draining the same message twice sends one
message, which is the property the delivery marker exists for and the one an
in-memory double could be written to agree with either way.
"""

from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.alerts.application.handlers import (
    DeliverMovementAlertUseCase,
)
from personal_finance.contexts.alerts.application.messages import MovementAlert
from personal_finance.contexts.alerts.domain.entities import AlertChannel
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertType,
    ChannelKind,
    ChatId,
    SecretHash,
)
from personal_finance.contexts.alerts.infrastructure.messaging.inbound import (
    FINANCIAL_SOURCE,
    MOVEMENT_RECORDED,
)
from personal_finance.contexts.alerts.infrastructure.messaging.sqs_worker import (
    SQSAlertsWorker,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAlertChannelRepository,
    DynamoDBDeliveryLog,
)
from personal_finance.contexts.financial.application.integration_events import (
    FinancialIntegrationEventTranslator,
)
from personal_finance.contexts.financial.domain.events import TransactionRecorded
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
    MovementId,
    TransactionOrigin,
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
    ALERTS_EVENTS_RULE,
    ALERTS_EVENTS_TARGET_ID,
    provision_context_subscription,
    provision_event_bus,
    provision_table,
)


BUS = "finflow"
QUEUE = "alerts-events"
TABLE_NAME = "alert_channels"
TIMEZONE = "America/Bogota"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
CHAT = ChatId("123456789")
MOVED_AT = PosixTime.from_epoch_seconds(1_757_800_000)


class RecordingSender:
    def __init__(self) -> None:
        self.alerts: list[tuple[ChatId, MovementAlert]] = []

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self.alerts.append((chat_id, alert))

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        del chat_id

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        del chat_id


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
        rule_name=ALERTS_EVENTS_RULE,
        target_id=ALERTS_EVENTS_TARGET_ID,
        # The same pattern `provision()` writes. If these ever disagree, this
        # test passes and the deployment delivers nothing.
        event_pattern={
            "source": [FINANCIAL_SOURCE],
            "detail-type": [MOVEMENT_RECORDED],
        },
    )

    return url


@pytest.fixture
def channels(dynamodb_client: DynamoDBClient) -> DynamoDBAlertChannelRepository:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=True,
    )

    return DynamoDBAlertChannelRepository(
        client=dynamodb_client,
        table_name=TABLE_NAME,
    )


@pytest.fixture
def sender() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def worker(
    dynamodb_client: DynamoDBClient,
    sqs_client: SQSClient,
    queue_url: str,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> SQSAlertsWorker:
    return SQSAlertsWorker(
        client=sqs_client,
        queue_url=queue_url,
        use_case=DeliverMovementAlertUseCase(
            channels=channels,
            deliveries=DynamoDBDeliveryLog(
                client=dynamodb_client,
                table_name=TABLE_NAME,
            ),
            sender=sender,
        ),
    )


@pytest.fixture
def publisher(eventbridge_client: EventBridgeClient) -> EventBridgeEventPublisher:
    return EventBridgeEventPublisher(
        client=eventbridge_client,
        event_bus_name=BUS,
        translator=FinancialIntegrationEventTranslator(),
    )


def _linked(channels: DynamoDBAlertChannelRepository) -> AlertChannel:
    channel = AlertChannel.pending(
        user_id=USER_ID,
        kind=ChannelKind.TELEGRAM,
        link_hash=SecretHash("sha256:abc"),
        now=MOVED_AT,
    )
    channels.save(channel)
    channel.verify(chat_id=CHAT, label="Ana", now=MOVED_AT)
    channels.save_verified(channel)

    return channel


def _recorded(
    *,
    amount: str = "84300",
    origin: TransactionOrigin = TransactionOrigin.BANK_ALERT,
) -> TransactionRecorded:
    return TransactionRecorded(
        movement_id=MovementId.new(),
        user_id=USER_ID,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        movement_occurred_at=MOVED_AT,
        counterparty="COMPRA EN *PAYU*COL",
        bank="Bancolombia",
        origin=origin,
        account_fingerprint=None,
    )


def test_a_recorded_movement_reaches_a_linked_channel(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> None:
    _linked(channels)

    publisher.publish([_recorded()])
    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    [(chat_id, alert)] = sender.alerts
    assert chat_id == CHAT
    assert alert.amount.amount == Decimal("84300")
    assert alert.counterparty == "COMPRA EN *PAYU*COL"
    assert alert.unassigned is True


def test_the_time_that_survives_the_bus_is_when_the_money_moved(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> None:
    """The transport writes its own `occurred_at` into the same object as the
    payload, so a payload key by that name would be silently overwritten with
    "when the fact was recorded". Only seeing it cross a real bus catches it.
    """
    _linked(channels)

    publisher.publish([_recorded()])
    worker.poll_once(wait_seconds=0)

    [(_, alert)] = sender.alerts
    assert alert.occurred_at == MOVED_AT


def test_the_same_movement_delivered_twice_sends_one_message(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> None:
    """Delivery is at-least-once, and the marker is what makes the second
    arrival quiet. It is written after the send, so this also proves the
    ordering does not cost idempotency on the ordinary path."""
    _linked(channels)
    recorded = _recorded()

    publisher.publish([recorded])
    worker.poll_once(wait_seconds=0)
    # Put the very same event back, as a redrive would.
    publisher.publish([recorded])
    worker.poll_once(wait_seconds=0)

    assert len(sender.alerts) == 1


def test_an_accrual_crosses_the_bus_and_is_not_announced(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> None:
    """Interest and insurance the app itself computed, written in bulk the
    moment somebody opens a credit screen and presses refresh."""
    _linked(channels)

    publisher.publish([_recorded(origin=TransactionOrigin.ACCRUAL)])
    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    assert sender.alerts == []


def test_a_movement_under_the_floor_crosses_the_bus_and_is_not_announced(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    channels: DynamoDBAlertChannelRepository,
    sender: RecordingSender,
) -> None:
    channel = _linked(channels)
    channel.update_preference(
        AlertPreference(
            alert_type=AlertType.MOVEMENT,
            enabled=True,
            minimum_amount=Money(amount=Decimal("100000"), currency=Currency.COP),
        ),
    )
    channels.save(channel)

    publisher.publish([_recorded(amount="84300")])
    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    assert sender.alerts == []


def test_a_movement_with_no_linked_channel_is_done_with(
    publisher: EventBridgeEventPublisher,
    worker: SQSAlertsWorker,
    sender: RecordingSender,
) -> None:
    publisher.publish([_recorded()])
    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    assert sender.alerts == []
