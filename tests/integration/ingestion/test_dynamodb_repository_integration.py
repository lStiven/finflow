from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    TTL_ATTRIBUTE,
    provision_table,
)


TABLE_NAME = "bank_notifications"
RETENTION_DAYS = 90
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def repository(dynamodb_client: DynamoDBClient) -> DynamoDBBankNotificationRepository:
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    return DynamoDBBankNotificationRepository(
        client=dynamodb_client,
        table_name=TABLE_NAME,
        retention_days=RETENTION_DAYS,
    )


def _notification(*, message_id: str = "message-1") -> BankNotification:
    return BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId(message_id),
        sender=EmailAddress("alerts@bank.com"),
        subject="Purchase notification",
        raw_content="Purchase for COP 50,000",
        received_at=PosixTime.now(),
    )


def test_first_write_stores_the_notification(
    repository: DynamoDBBankNotificationRepository,
) -> None:
    assert repository.add_if_new(_notification()) is None


def test_conditional_write_rejects_a_redelivery_and_returns_the_stored_record(
    repository: DynamoDBBankNotificationRepository,
) -> None:
    first = _notification()
    repository.add_if_new(first)

    stored = repository.add_if_new(_notification())

    assert stored is not None
    assert stored.id == first.id
    assert stored.message_id == first.message_id
    assert stored.status is ProcessingStatus.RECEIVED


def test_a_redelivery_never_overwrites_the_stored_record(
    repository: DynamoDBBankNotificationRepository,
) -> None:
    original = _notification()
    repository.add_if_new(original)
    original.mark_as_queued()
    repository.save(original)

    # A retry arrives with a fresh RECEIVED aggregate; the stored QUEUED state
    # must win, otherwise the notification would be enqueued twice.
    stored = repository.add_if_new(_notification())

    assert stored is not None
    assert stored.status is ProcessingStatus.QUEUED


def test_different_emails_do_not_collide(
    repository: DynamoDBBankNotificationRepository,
) -> None:
    assert repository.add_if_new(_notification(message_id="message-1")) is None
    assert repository.add_if_new(_notification(message_id="message-2")) is None


def test_save_persists_a_status_transition(
    repository: DynamoDBBankNotificationRepository,
) -> None:
    notification = _notification()
    repository.add_if_new(notification)
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

    repository.save(notification)

    stored = repository.add_if_new(_notification())
    assert stored is not None
    assert stored.status is ProcessingStatus.IGNORED


def test_table_has_ttl_enabled_on_the_expiry_attribute(
    dynamodb_client: DynamoDBClient,
) -> None:
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    description = dynamodb_client.describe_time_to_live(TableName=TABLE_NAME)
    ttl = description["TimeToLiveDescription"]

    assert ttl.get("TimeToLiveStatus") == "ENABLED"
    assert ttl.get("AttributeName") == TTL_ATTRIBUTE


def test_provisioning_is_idempotent(dynamodb_client: DynamoDBClient) -> None:
    provision_table(dynamodb_client, table_name=TABLE_NAME)
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    table = dynamodb_client.describe_table(TableName=TABLE_NAME)["Table"]

    assert table.get("TableName") == TABLE_NAME


def test_free_tier_table_is_created_with_provisioned_capacity(
    dynamodb_client: DynamoDBClient,
) -> None:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        billing_mode="PROVISIONED",
        read_capacity=5,
        write_capacity=5,
    )

    table = dynamodb_client.describe_table(TableName=TABLE_NAME)["Table"]
    throughput = table.get("ProvisionedThroughput", {})

    # Provisioned capacity is what the always-free DynamoDB allowance covers;
    # an on-demand table is billed from the first request.
    assert throughput.get("ReadCapacityUnits") == 5
    assert throughput.get("WriteCapacityUnits") == 5


def test_on_demand_table_carries_no_throughput_setting(
    dynamodb_client: DynamoDBClient,
) -> None:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        billing_mode="PAY_PER_REQUEST",
    )

    table = dynamodb_client.describe_table(TableName=TABLE_NAME)["Table"]
    summary = table.get("BillingModeSummary", {})

    assert summary.get("BillingMode") == "PAY_PER_REQUEST"


def test_repository_works_against_a_provisioned_table(
    dynamodb_client: DynamoDBClient,
) -> None:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        billing_mode="PROVISIONED",
        read_capacity=5,
        write_capacity=5,
    )
    repository = DynamoDBBankNotificationRepository(
        client=dynamodb_client,
        table_name=TABLE_NAME,
        retention_days=RETENTION_DAYS,
    )

    assert repository.add_if_new(_notification()) is None
    assert repository.add_if_new(_notification()) is not None
