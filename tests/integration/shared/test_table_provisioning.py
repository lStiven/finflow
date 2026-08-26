"""What a provisioned table comes with, against moto's real DynamoDB API.

Point-in-time recovery is the one safeguard on this project's list that cannot
be added after the fact: an alarm nobody set up can be set up the day it is
missed, but a table that was never backed up is simply gone. So it is on by
default, and these tests are what keep it that way.
"""

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import (
    GlobalSecondaryIndexDescriptionTypeDef,
    ProvisionedThroughputDescriptionTypeDef,
)
import pytest

from personal_finance.shared.infrastructure.aws import provisioning
from personal_finance.shared.infrastructure.aws.provisioning import (
    PARTITION_KEY,
    SecondaryIndex,
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


def _indexes(
    client: DynamoDBClient,
    table_name: str,
) -> list[GlobalSecondaryIndexDescriptionTypeDef]:
    return list(
        client.describe_table(TableName=table_name)["Table"].get(
            "GlobalSecondaryIndexes",
            [],
        ),
    )


def _capacity(throughput: ProvisionedThroughputDescriptionTypeDef) -> tuple[int, int]:
    return (
        throughput.get("ReadCapacityUnits", 0),
        throughput.get("WriteCapacityUnits", 0),
    )


def _table_capacity(client: DynamoDBClient, table_name: str) -> tuple[int, int]:
    described = client.describe_table(TableName=table_name)["Table"]

    return _capacity(described.get("ProvisionedThroughput", {}))


def _index_capacity(
    client: DynamoDBClient,
    table_name: str,
    index_name: str,
) -> tuple[int, int]:
    throughput = next(
        index.get("ProvisionedThroughput", {})
        for index in _indexes(client, table_name)
        if index.get("IndexName") == index_name
    )

    return _capacity(throughput)


def test_a_table_that_predates_a_capacity_change_is_brought_to_it(
    dynamodb_client: DynamoDBClient,
) -> None:
    """`create_table` is skipped once a table exists, so without this a
    capacity lowered in configuration would reach new environments only —
    and the running one would stay over the always-free allowance the new
    number was chosen to fit inside.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=4,
        write_capacity=4,
    )

    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=3,
        write_capacity=3,
    )

    assert _table_capacity(dynamodb_client, TABLE) == (3, 3)


def test_an_index_follows_the_same_capacity_as_its_table(
    dynamodb_client: DynamoDBClient,
) -> None:
    """DynamoDB charges an index like a table, so an index left behind at the
    old number is exactly the overspend this reconciliation exists to stop.
    """
    index = SecondaryIndex(name="by_user", partition_key="user_id")
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=4,
        write_capacity=4,
        secondary_indexes=(index,),
    )

    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=3,
        write_capacity=3,
        secondary_indexes=(index,),
    )

    assert _index_capacity(dynamodb_client, TABLE, "by_user") == (3, 3)


def test_an_index_is_active_before_provisioning_returns(
    dynamodb_client: DynamoDBClient,
) -> None:
    """The endpoint that reads this index is mounted the moment a deploy
    lands. Returning while DynamoDB is still backfilling would hand somebody
    a live endpoint that errors.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        secondary_indexes=(SecondaryIndex(name="by_user", partition_key="user_id"),),
    )

    indexes = _indexes(dynamodb_client, TABLE)

    assert [index.get("IndexStatus") for index in indexes] == ["ACTIVE"]


def test_an_index_projects_only_what_it_was_asked_for(
    dynamodb_client: DynamoDBClient,
) -> None:
    """An index is a full copy of what it projects. The notifications one
    leaves the raw email out, and that is a decision worth pinning.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        secondary_indexes=(
            SecondaryIndex(
                name="by_user",
                partition_key="user_id",
                projected_attributes=("status", "received_at"),
            ),
        ),
    )

    projection = _indexes(dynamodb_client, TABLE)[0].get("Projection", {})

    assert projection.get("ProjectionType") == "INCLUDE"
    assert sorted(projection.get("NonKeyAttributes", [])) == ["received_at", "status"]


def _billing_mode(client: DynamoDBClient, table_name: str) -> str:
    described = client.describe_table(TableName=table_name)["Table"]

    return described.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")


def test_a_table_that_predates_the_billing_mode_is_moved_onto_it(
    dynamodb_client: DynamoDBClient,
) -> None:
    """The failure this reconciliation exists for: `create_table` decides the
    mode only for a table that does not exist yet, so without it, changing the
    configured mode would reach new environments only — and a running one
    would stay on the old one while the run reported success.
    """
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=3,
        write_capacity=3,
    )

    assert _billing_mode(dynamodb_client, TABLE) == "PROVISIONED"

    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    assert _billing_mode(dynamodb_client, TABLE) == "PAY_PER_REQUEST"


def test_a_table_can_be_moved_back_off_on_demand(
    dynamodb_client: DynamoDBClient,
) -> None:
    # The way out, if the bill ever stops being negligible.
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PROVISIONED",
        read_capacity=3,
        write_capacity=3,
    )

    assert _billing_mode(dynamodb_client, TABLE) == "PROVISIONED"
    assert _table_capacity(dynamodb_client, TABLE) == (3, 3)


def test_an_on_demand_table_is_left_alone_on_a_second_run(
    dynamodb_client: DynamoDBClient,
) -> None:
    # Switching modes is rate-limited by DynamoDB, so a re-run that keeps
    # asking for the mode it is already on would spend that allowance.
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    assert _billing_mode(dynamodb_client, TABLE) == "PAY_PER_REQUEST"


def test_an_on_demand_table_carries_a_ceiling_on_its_requests(
    dynamodb_client: DynamoDBClient,
) -> None:
    """A runaway loop should throttle rather than bill.

    moto does not model `OnDemandThroughput`, so what is checked here is that
    provisioning asks for it — which is the part this project controls.
    """
    asked: list[dict[str, int]] = []
    original = dynamodb_client.create_table

    def recording(**kwargs: object) -> object:
        cap = kwargs.get("OnDemandThroughput")

        if isinstance(cap, dict):
            asked.append(cap)  # type: ignore[arg-type]

        return original(**kwargs)  # type: ignore[arg-type]

    dynamodb_client.create_table = recording  # type: ignore[method-assign]
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PAY_PER_REQUEST",
        max_read_units=25,
        max_write_units=25,
    )

    assert asked == [{"MaxReadRequestUnits": 25, "MaxWriteRequestUnits": 25}]


def test_an_index_added_later_is_ready_before_provisioning_returns(
    dynamodb_client: DynamoDBClient,
) -> None:
    """The table exists first without the index, which is the deploy shape
    that matters: the endpoint querying it is mounted the moment the deploy
    lands, and DynamoDB refuses a query against an index it is still filling.
    """
    provision_table(dynamodb_client, table_name=TABLE, billing_mode="PAY_PER_REQUEST")

    index = SecondaryIndex(name="by_user", partition_key="user_id")
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PAY_PER_REQUEST",
        secondary_indexes=(index,),
    )

    described = next(
        entry
        for entry in _indexes(dynamodb_client, TABLE)
        if entry.get("IndexName") == "by_user"
    )

    assert described.get("IndexStatus") == "ACTIVE"
    assert not described.get("Backfilling", False)


def test_an_index_that_never_appears_is_reported_rather_than_passed_over(
    dynamodb_client: DynamoDBClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`all()` over an empty list is True.

    So a describe reporting no indexes at all — the creation silently lost, or
    the control plane not reflecting it yet — used to satisfy the readiness
    check vacuously, and provisioning would report success on a table whose
    endpoint then failed for every user. Here the index is hidden from every
    describe, which is what that failure looks like from the outside.
    """
    monkeypatch.setattr(provisioning, "INDEX_ACTIVE_TIMEOUT_SECONDS", 0)
    monkeypatch.setattr(provisioning, "INDEX_POLL_SECONDS", 0)
    described = dynamodb_client.describe_table

    def without_indexes(**kwargs: str) -> object:
        answer = described(**kwargs)
        answer["Table"].pop("GlobalSecondaryIndexes", None)

        return answer

    dynamodb_client.describe_table = without_indexes  # type: ignore[assignment]

    with pytest.raises(TimeoutError, match="by_user"):
        provision_table(
            dynamodb_client,
            table_name=TABLE,
            billing_mode="PAY_PER_REQUEST",
            secondary_indexes=(
                SecondaryIndex(name="by_user", partition_key="user_id"),
            ),
        )
