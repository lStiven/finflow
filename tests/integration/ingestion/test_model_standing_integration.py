"""A person's standing with the model, against DynamoDB: the version check is
what keeps two workers from handing out the same strike twice, so it is
tested where the condition is actually evaluated.
"""

import datetime
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.ingestion.domain.model_standing import (
    ModelAccess,
    ModelStanding,
)
from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.contexts.ingestion.infrastructure.persistence.model_standing_dynamodb import (  # noqa: E501
    DynamoDBModelStandings,
    standing_key,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "bank_notifications"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_datetime(datetime.datetime(2026, 10, 31, 9, tzinfo=datetime.UTC))


@pytest.fixture
def standings(dynamodb_client: DynamoDBClient) -> DynamoDBModelStandings:
    provision_table(dynamodb_client, table_name=TABLE_NAME)

    return DynamoDBModelStandings(client=dynamodb_client, table_name=TABLE_NAME)


def _minutes(minutes: int) -> PosixTime:
    return PosixTime.from_datetime(
        NOW.to_datetime() + datetime.timedelta(minutes=minutes)
    )


def test_somebody_never_seen_has_a_clean_record(
    standings: DynamoDBModelStandings,
) -> None:
    standing = standings.get(USER_ID)

    assert standing.version == 0
    assert standing.access(NOW) is ModelAccess.OPEN


def test_a_strike_survives_the_round_trip(standings: DynamoDBModelStandings) -> None:
    standing = standings.get(USER_ID)
    for minute in range(10):
        standing.record_offense(
            notification_id=NotificationId(value=uuid.uuid4()),
            received_at=_minutes(minute),
            now=_minutes(minute),
        )

    assert standings.save(standing) is True
    stored = standings.get(USER_ID)

    assert stored.version == 1
    assert stored.access(_minutes(30)) is ModelAccess.PAUSED
    assert stored.paused_until == standing.paused_until
    assert stored.strikes == standing.strikes
    assert stored.offenses == {}


def test_offenses_and_a_revocation_survive_the_round_trip(
    standings: DynamoDBModelStandings,
) -> None:
    standing = standings.get(USER_ID)
    offense = NotificationId(value=uuid.uuid4())
    standing.record_offense(notification_id=offense, received_at=NOW, now=NOW)
    standing.revoked = True
    standings.save(standing)

    stored = standings.get(USER_ID)

    assert stored.offenses == {offense: NOW}
    assert stored.access(NOW) is ModelAccess.REVOKED


def test_a_write_made_on_a_stale_copy_loses(standings: DynamoDBModelStandings) -> None:
    first = standings.get(USER_ID)
    second = standings.get(USER_ID)

    assert standings.save(first) is True
    assert standings.save(second) is False

    # And the winner can keep writing on its own copy.
    assert standings.save(first) is True
    assert standings.get(USER_ID).version == 2


def test_the_record_never_expires_and_stays_out_of_the_by_user_index(
    standings: DynamoDBModelStandings,
    dynamodb_client: DynamoDBClient,
) -> None:
    standings.save(standings.get(USER_ID))

    item = dynamodb_client.get_item(
        TableName=TABLE_NAME,
        Key={"pk": {"S": standing_key(USER_ID)}},
    ).get("Item", {})

    assert item

    assert "expires_at" not in item
    assert "user_id" not in item
    assert "received_at" not in item


def test_two_people_do_not_share_a_record(standings: DynamoDBModelStandings) -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.revoked = True
    standings.save(standing)

    other = standings.get(UserId.from_string("22222222-2222-2222-2222-222222222222"))

    assert other.access(NOW) is ModelAccess.OPEN
