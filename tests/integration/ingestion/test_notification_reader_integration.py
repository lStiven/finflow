"""Reading one user's notifications off the `by_user` index, against DynamoDB.

The index is what keeps the cost of this list independent of everybody else's
mail, and what makes the scoping structural rather than a filter somebody has
to remember to apply. Both are only true if the table is actually provisioned
with it, which is why this test provisions the real thing.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    NOTIFICATIONS_BY_USER_INDEX,
    SUMMARY_ATTRIBUTES,
    USER_ID_ATTRIBUTE,
    DynamoDBBankNotificationRepository,
    DynamoDBNotificationReader,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    SecondaryIndex,
    provision_table,
)


TABLE_NAME = "bank_notifications"
RETENTION_DAYS = 90
USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        secondary_indexes=(
            SecondaryIndex(
                name=NOTIFICATIONS_BY_USER_INDEX,
                partition_key=USER_ID_ATTRIBUTE,
                projected_attributes=SUMMARY_ATTRIBUTES,
            ),
        ),
    )

    return dynamodb_client


@pytest.fixture
def repository(table: DynamoDBClient) -> DynamoDBBankNotificationRepository:
    return DynamoDBBankNotificationRepository(
        client=table,
        table_name=TABLE_NAME,
        retention_days=RETENTION_DAYS,
    )


@pytest.fixture
def reader(table: DynamoDBClient) -> DynamoDBNotificationReader:
    return DynamoDBNotificationReader(client=table, table_name=TABLE_NAME)


def _notification(
    *,
    user_id: UserId = USER,
    message_id: str = "<1@bank.com>",
    received_at: int = 1_787_000_000,
) -> BankNotification:
    return BankNotification.receive(
        user_id=user_id,
        message_id=EmailMessageId(message_id),
        sender=EmailAddress("alerts@bank.com"),
        subject="Purchase notification",
        raw_content="Bancolombia: Compraste $45.000 en EXITO",
        received_at=PosixTime.from_epoch_seconds(received_at),
    )


def test_a_stored_notification_comes_back_as_a_summary(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    repository.add_if_new(_notification())

    summaries = reader.list_by_user(USER)

    assert len(summaries) == 1
    assert summaries[0].message_id == EmailMessageId("<1@bank.com>")
    assert summaries[0].sender == EmailAddress("alerts@bank.com")
    assert summaries[0].status is ProcessingStatus.RECEIVED
    assert summaries[0].received_at.as_epoch_seconds() == 1_787_000_000


def test_it_returns_nothing_that_belongs_to_another_user(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    repository.add_if_new(_notification(message_id="<mine@bank.com>"))
    repository.add_if_new(
        _notification(user_id=OTHER_USER, message_id="<theirs@bank.com>"),
    )

    assert [summary.message_id.value for summary in reader.list_by_user(USER)] == [
        "<mine@bank.com>",
    ]


def test_a_user_with_no_mail_gets_an_empty_list(
    reader: DynamoDBNotificationReader,
) -> None:
    assert reader.list_by_user(USER) == []


def test_an_ignored_notification_is_still_listed(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    """An unapproved sender is exactly what somebody needs to see: the email
    arrived, it was refused, and approving that sender is the fix.
    """
    notification = _notification()
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
    repository.add_if_new(notification)

    summaries = reader.list_by_user(USER)

    assert summaries[0].status is ProcessingStatus.IGNORED
    assert summaries[0].sender == EmailAddress("alerts@bank.com")


def test_a_deferred_notification_keeps_its_reason(
    repository: DynamoDBBankNotificationRepository,
    reader: DynamoDBNotificationReader,
) -> None:
    notification = _notification()
    repository.add_if_new(notification)
    notification.mark_as_queued()
    notification.start_processing()
    notification.defer_to_fallback(
        reason=NotificationDeferredReason.NO_FALLBACK_CONFIGURED,
    )
    repository.save(notification)

    summary = reader.list_by_user(USER)[0]

    assert summary.status is ProcessingStatus.PENDING_FALLBACK
    assert summary.deferred_reason is (
        NotificationDeferredReason.NO_FALLBACK_CONFIGURED
    )


def test_the_index_does_not_carry_the_email_body(
    repository: DynamoDBBankNotificationRepository,
    table: DynamoDBClient,
) -> None:
    """The projection is the enforcement: an index over `ALL` would keep a
    second copy of every untrusted message for a list that never shows one.
    """
    repository.add_if_new(_notification())

    items = table.query(
        TableName=TABLE_NAME,
        IndexName=NOTIFICATIONS_BY_USER_INDEX,
        KeyConditionExpression=f"{USER_ID_ATTRIBUTE} = :user_id",
        ExpressionAttributeValues={":user_id": {"S": str(USER.value)}},
    )["Items"]

    assert "raw_content" not in items[0]
