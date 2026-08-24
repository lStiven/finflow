"""What a provisioned table comes with, against moto's real DynamoDB API.

Point-in-time recovery is the one safeguard on this project's list that cannot
be added after the fact: an alarm nobody set up can be set up the day it is
missed, but a table that was never backed up is simply gone. So it is on by
default, and these tests are what keep it that way.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.shared.infrastructure.aws.provisioning import (
    PARTITION_KEY,
    provision_table,
)


TABLE = "provisioned"


def _recovery_status(client: DynamoDBClient, table_name: str) -> str | None:
    description = client.describe_continuous_backups(TableName=table_name)

    return (
        description["ContinuousBackupsDescription"]
        .get("PointInTimeRecoveryDescription", {})
        .get("PointInTimeRecoveryStatus")
    )


def test_a_new_table_can_be_restored_to_any_second_it_held(
    dynamodb_client: DynamoDBClient,
) -> None:
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    assert _recovery_status(dynamodb_client, TABLE) == "ENABLED"


def test_a_table_that_predates_backups_gets_them_on_the_next_run(
    dynamodb_client: DynamoDBClient,
) -> None:
    """Provisioning is how an existing environment catches up.

    The table already exists, so creation is skipped — the backup call has to
    sit outside that branch or a deployment made before this change would
    never get it.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PAY_PER_REQUEST",
        enable_point_in_time_recovery=False,
    )

    assert _recovery_status(dynamodb_client, TABLE) == "DISABLED"

    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    assert _recovery_status(dynamodb_client, TABLE) == "ENABLED"


def test_provisioning_twice_is_not_an_error(
    dynamodb_client: DynamoDBClient,
) -> None:
    # Enabling it a second time is refused on a real account, so it asks first.
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    assert _recovery_status(dynamodb_client, TABLE) == "ENABLED"


def test_a_table_whose_rows_never_expire_is_still_backed_up(
    dynamodb_client: DynamoDBClient,
) -> None:
    """`financial` and `user_inboxes` both switch TTL off.

    The backup call must come before that early return, or the ledger — the
    one table nothing else can reconstruct — would be the one without backups.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PAY_PER_REQUEST",
        enable_ttl=False,
    )

    assert _recovery_status(dynamodb_client, TABLE) == "ENABLED"
    assert (
        dynamodb_client.describe_time_to_live(TableName=TABLE)[
            "TimeToLiveDescription"
        ].get("TimeToLiveStatus")
        == "DISABLED"
    )


@pytest.mark.parametrize("sort_key", [None, "entity_id"])
def test_the_key_shape_does_not_change_what_it_comes_with(
    dynamodb_client: DynamoDBClient,
    sort_key: str | None,
) -> None:
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        partition_key=PARTITION_KEY,
        sort_key=sort_key,
        sort_key_type="S",
        billing_mode="PAY_PER_REQUEST",
    )

    assert _recovery_status(dynamodb_client, TABLE) == "ENABLED"
