"""The day's model reads, against DynamoDB: the conditional write is the
whole mechanism, so it is tested where the condition is actually evaluated.
"""

import datetime
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef
import pytest

from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.contexts.ingestion.infrastructure.persistence.model_usage_dynamodb import (  # noqa: E501
    DynamoDBModelUsageQuota,
    usage_key,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    TTL_ATTRIBUTE,
    provision_table,
)


TABLE_NAME = "bank_notifications"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
DAY = datetime.date(2026, 10, 30)


class Limit:
    """A limit the test can move, the way an operator moves the parameter."""

    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


@pytest.fixture
def limit() -> Limit:
    return Limit(3)


@pytest.fixture
def quota(dynamodb_client: DynamoDBClient, limit: Limit) -> DynamoDBModelUsageQuota:
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    return DynamoDBModelUsageQuota(
        client=dynamodb_client,
        table_name=TABLE_NAME,
        daily_limit=limit,
    )


def _notification() -> NotificationId:
    return NotificationId(value=uuid.uuid4())


def _stored(client: DynamoDBClient) -> dict[str, AttributeValueTypeDef]:
    return client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": usage_key(user_id=USER_ID, day=DAY)}},
    ).get("Item", {})


def _reserve(
    quota: DynamoDBModelUsageQuota,
    notification_id: NotificationId,
    *,
    user_id: UserId = USER_ID,
    day: datetime.date = DAY,
) -> bool:
    return quota.reserve(user_id=user_id, notification_id=notification_id, day=day)


def test_reads_are_granted_up_to_the_limit_and_refused_past_it(
    quota: DynamoDBModelUsageQuota,
) -> None:
    granted = [_reserve(quota, _notification()) for _ in range(3)]

    assert granted == [True, True, True]
    assert _reserve(quota, _notification()) is False


def test_the_same_email_delivered_again_spends_no_second_read(
    quota: DynamoDBModelUsageQuota,
    dynamodb_client: DynamoDBClient,
) -> None:
    notification_id = _notification()

    assert _reserve(quota, notification_id) is True
    assert _reserve(quota, notification_id) is True

    stored = _stored(dynamodb_client)["notifications"].get("SS")
    assert stored == [str(notification_id.value)]


def test_an_email_that_already_holds_a_read_keeps_it_once_the_day_is_full(
    quota: DynamoDBModelUsageQuota,
) -> None:
    # The model was down for this one, and the retry comes after the rest of
    # the day's reads were taken: it already paid its way in.
    first = _notification()
    _reserve(quota, first)
    _reserve(quota, _notification())
    _reserve(quota, _notification())

    assert _reserve(quota, first) is True


def test_each_person_and_each_day_has_a_limit_of_its_own(
    quota: DynamoDBModelUsageQuota,
) -> None:
    for _ in range(3):
        _reserve(quota, _notification())

    assert _reserve(quota, _notification(), user_id=OTHER_USER_ID) is True
    assert _reserve(quota, _notification(), day=DAY + datetime.timedelta(days=1))


def test_a_limit_changed_mid_day_applies_to_the_next_read(
    quota: DynamoDBModelUsageQuota,
    limit: Limit,
) -> None:
    for _ in range(3):
        _reserve(quota, _notification())

    limit.value = 4
    assert _reserve(quota, _notification()) is True

    limit.value = 2
    assert _reserve(quota, _notification()) is False


def test_a_limit_of_zero_refuses_everything(
    quota: DynamoDBModelUsageQuota,
    limit: Limit,
) -> None:
    limit.value = 0

    assert _reserve(quota, _notification()) is False


def test_the_row_expires_a_week_after_its_day(
    quota: DynamoDBModelUsageQuota,
    dynamodb_client: DynamoDBClient,
) -> None:
    _reserve(quota, _notification())

    expires_at = datetime.datetime.fromtimestamp(
        int(_stored(dynamodb_client)[TTL_ATTRIBUTE].get("N", "0")),
        tz=datetime.timezone(datetime.timedelta(hours=-5)),
    )
    # Midnight in Bogotá at the end of the day, plus seven days.
    assert expires_at.date() == datetime.date(2026, 11, 7)
    assert expires_at.time() == datetime.time()


def test_a_released_read_frees_its_place_and_can_be_taken_again(
    quota: DynamoDBModelUsageQuota,
) -> None:
    first = _notification()
    _reserve(quota, first)
    _reserve(quota, _notification())
    _reserve(quota, _notification())

    quota.release(user_id=USER_ID, notification_id=first, day=DAY)

    assert _reserve(quota, _notification()) is True
    assert _reserve(quota, first) is False


def test_releasing_on_a_day_with_no_reads_writes_nothing(
    quota: DynamoDBModelUsageQuota,
    dynamodb_client: DynamoDBClient,
) -> None:
    quota.release(user_id=USER_ID, notification_id=_notification(), day=DAY)

    assert _stored(dynamodb_client) == {}
