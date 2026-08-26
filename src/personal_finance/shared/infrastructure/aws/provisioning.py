"""Creates the AWS resources the ingestion context needs.

The same script targets a local emulator or a real account: only the endpoint
and the credentials change. Every step is idempotent, so it is safe to re-run.
"""

from __future__ import annotations

from collections.abc import Sequence
import contextlib
import dataclasses
import json
import time

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.literals import ScalarAttributeTypeType
from mypy_boto3_dynamodb.type_defs import (
    AttributeDefinitionTypeDef,
    CreateGlobalSecondaryIndexActionTypeDef,
    KeySchemaElementTypeDef,
    OnDemandThroughputTypeDef,
    ProjectionTypeDef,
    ProvisionedThroughputDescriptionTypeDef,
    ProvisionedThroughputTypeDef,
)
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient

from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as FINANCIAL_PARTITION_KEY,
    SORT_KEY as FINANCIAL_SORT_KEY,
)
from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as USERS_PARTITION_KEY,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    NOTIFICATIONS_BY_USER_INDEX,
    PARTITION_KEY,
    SUMMARY_ATTRIBUTES,
    USER_ID_ATTRIBUTE as NOTIFICATION_USER_ID_ATTRIBUTE,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    INBOX_BY_USER_INDEX,
    INBOX_PARTITION_KEY,
    USER_ID_ATTRIBUTE as INBOX_USER_ID_ATTRIBUTE,
)
from personal_finance.contexts.merchant.application.integration_events import (
    SOURCE as MERCHANT_SOURCE,
)
from personal_finance.contexts.merchant.infrastructure.messaging.sqs_worker import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as MERCHANT_PARTITION_KEY,
    SORT_KEY as MERCHANT_SORT_KEY,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_eventbridge_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    ENV_FILE,
    BillingMode,
    get_aws_settings,
    get_financial_settings,
    get_identity_settings,
    get_ingestion_settings,
    get_merchant_settings,
)


TTL_ATTRIBUTE = "expires_at"
DEAD_LETTER_SUFFIX = "-dlq"

# Every context publishes under `finflow.<context>`, so one prefix pattern
# subscribes to all of them.
SOURCE_PREFIX = "finflow"
INTEGRATION_EVENTS_RULE = "finflow-integration-events"
INTEGRATION_EVENTS_TARGET_ID = "integration-events-queue"
# Merchant's own subscription: only the one event it acts on, so its queue
# never fills with other contexts' traffic.
MERCHANT_EVENTS_RULE = "finflow-merchant-transactions"
MERCHANT_EVENTS_TARGET_ID = "merchant-events-queue"

FINANCIAL_EVENTS_RULE = "finflow-financial-transactions"
FINANCIAL_EVENTS_TARGET_ID = "financial-events-queue"
MAX_RECEIVE_COUNT = 5
# Long enough for a parse plus the LLM fallback, short enough that a crashed
# worker releases the message quickly.
VISIBILITY_TIMEOUT_SECONDS = 120
MESSAGE_RETENTION_SECONDS = 1_209_600  # 14 days, the SQS maximum.

# How long to wait for the control plane to finish a change. Backfilling an
# index over a large table is the slow one; everything else settles in
# seconds.
INDEX_ACTIVE_TIMEOUT_SECONDS = 600
INDEX_POLL_SECONDS = 5


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ProvisionedResources:
    table_name: str
    inboxes_table_name: str
    users_table_name: str
    merchants_table_name: str
    financial_table_name: str
    queue_url: str
    dead_letter_queue_url: str
    merchant_events_queue_url: str
    financial_events_queue_url: str
    event_bus_name: str
    integration_events_queue_url: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SecondaryIndex:
    """A global secondary index over one attribute.

    An index is a full copy of what it projects, and DynamoDB charges it like
    a table. Projecting everything is the right default — these indexes answer
    "give me the records belonging to X", and a key-only projection would
    force a second read per hit — but naming attributes explicitly is what
    lets the notifications index leave the raw email body out.
    """

    name: str
    partition_key: str
    # Empty projects every attribute.
    projected_attributes: tuple[str, ...] = ()

    def projection(self) -> ProjectionTypeDef:
        if not self.projected_attributes:
            return {"ProjectionType": "ALL"}

        return {
            "ProjectionType": "INCLUDE",
            "NonKeyAttributes": list(self.projected_attributes),
        }


def _index_throughput(
    *,
    billing_mode: BillingMode,
    read_capacity: int,
    write_capacity: int,
) -> ProvisionedThroughputTypeDef | None:
    """An index inherits the table's billing mode; on-demand tables reject a
    throughput specification on their indexes too.
    """
    if billing_mode != "PROVISIONED":
        return None

    return {
        "ReadCapacityUnits": read_capacity,
        "WriteCapacityUnits": write_capacity,
    }


def _on_demand_cap(
    *,
    billing_mode: BillingMode,
    max_read_units: int,
    max_write_units: int,
) -> OnDemandThroughputTypeDef | None:
    """The per-second ceiling an on-demand table and its indexes run under.

    A blast radius, not a budget: it stops a runaway loop by throttling it,
    while a month pegged at the ceiling would still cost real money. The guard
    for spending is a billing alarm on the account.
    """
    if billing_mode != "PAY_PER_REQUEST":
        return None

    return {
        "MaxReadRequestUnits": max_read_units,
        "MaxWriteRequestUnits": max_write_units,
    }


def _throughput_matches(
    current: ProvisionedThroughputDescriptionTypeDef | None,
    *,
    read_capacity: int,
    write_capacity: int,
) -> bool:
    if current is None:
        return False

    return (
        current.get("ReadCapacityUnits") == read_capacity
        and current.get("WriteCapacityUnits") == write_capacity
    )


def _wait_until_active(client: DynamoDBClient, *, table_name: str) -> None:
    """Block until the table accepts another control-plane change.

    DynamoDB refuses a second `UpdateTable` while a table is `UPDATING`, and
    provisioning issues several in a row — the billing mode, then the
    capacity, then a new index. Without this the second one raises
    `ResourceInUseException` and the run dies partway through, leaving the
    table half-reconciled. moto does not model `UPDATING` at all, which is why
    nothing caught this until it was run against real AWS.
    """
    deadline = time.monotonic() + INDEX_ACTIVE_TIMEOUT_SECONDS

    while True:
        described = client.describe_table(TableName=table_name)["Table"]

        if described.get("TableStatus") == "ACTIVE":
            return

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{table_name} is still {described.get('TableStatus')} after "
                f"{INDEX_ACTIVE_TIMEOUT_SECONDS}s. Check the table in the "
                "console and re-run.",
            )

        time.sleep(INDEX_POLL_SECONDS)


def _reconcile_billing_mode(
    client: DynamoDBClient,
    *,
    table_name: str,
    billing_mode: BillingMode,
    throughput: ProvisionedThroughputTypeDef,
    on_demand_cap: OnDemandThroughputTypeDef | None,
) -> bool:
    """Move an existing table onto the configured billing mode.

    `create_table` only decides the mode of a table that does not exist yet,
    so without this, changing the configured mode would reach new environments
    only — and say nothing while a running one stayed on the old one. That is
    the failure this whole module is written against: a reconciler that
    silently does nothing is worse than one never written, because the run
    reports success either way.

    Returns whether it issued an update, so the caller knows to wait before
    making the next control-plane call.
    """
    described = client.describe_table(TableName=table_name)["Table"]
    # Absent on tables created before the field existed, and those are all
    # provisioned.
    current = described.get("BillingModeSummary", {}).get(
        "BillingMode",
        "PROVISIONED",
    )

    if current == billing_mode:
        return False

    if billing_mode == "PROVISIONED":
        client.update_table(
            TableName=table_name,
            BillingMode="PROVISIONED",
            ProvisionedThroughput=throughput,
        )
    elif on_demand_cap is None:
        client.update_table(TableName=table_name, BillingMode="PAY_PER_REQUEST")
    else:
        client.update_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            OnDemandThroughput=on_demand_cap,
        )

    return True


def _reconcile_throughput(
    client: DynamoDBClient,
    *,
    table_name: str,
    throughput: ProvisionedThroughputTypeDef,
) -> None:
    """Bring an existing table and its indexes to the configured capacity.

    Creation is not enough: `create_table` is skipped once a table exists, so
    a capacity lowered in configuration would apply to new environments only
    and quietly leave a running one paying for the old number — or, when an
    index is added, over the always-free allowance the configured value was
    chosen to fit inside. Same reasoning as point-in-time recovery below:
    applied on every run, not only the first.
    """
    described = client.describe_table(TableName=table_name)["Table"]
    read_capacity = throughput["ReadCapacityUnits"]
    write_capacity = throughput["WriteCapacityUnits"]

    if not _throughput_matches(
        described.get("ProvisionedThroughput"),
        read_capacity=read_capacity,
        write_capacity=write_capacity,
    ):
        client.update_table(
            TableName=table_name,
            ProvisionedThroughput=throughput,
        )
        # The index update below is a second `UpdateTable`, which DynamoDB
        # refuses while this one is still applying.
        _wait_until_active(client, table_name=table_name)

    stale = [
        name
        for index in described.get("GlobalSecondaryIndexes", [])
        if (name := index.get("IndexName")) is not None
        and not _throughput_matches(
            index.get("ProvisionedThroughput"),
            read_capacity=read_capacity,
            write_capacity=write_capacity,
        )
    ]

    if not stale:
        return

    client.update_table(
        TableName=table_name,
        GlobalSecondaryIndexUpdates=[
            {"Update": {"IndexName": name, "ProvisionedThroughput": throughput}}
            for name in stale
        ],
    )


def _wait_until_indexes_are_active(
    client: DynamoDBClient,
    *,
    table_name: str,
    secondary_indexes: Sequence[SecondaryIndex],
) -> None:
    """Block until every index this table is supposed to have can be queried.

    `update_table` returns as soon as DynamoDB accepts the index; it then
    backfills it, and until that finishes a query against it is refused. The
    endpoint reading this index is mounted the moment a deploy lands, so
    returning early here would hand somebody a live endpoint that errors.
    """
    deadline = time.monotonic() + INDEX_ACTIVE_TIMEOUT_SECONDS
    # Named rather than counted: `all()` over an empty list is True, so a
    # describe reporting no indexes at all — the creation silently lost, or the
    # control plane not reflecting it yet — would otherwise pass this check
    # vacuously and report a successful provision of a table whose endpoint
    # then fails for every user.
    expected = {index.name for index in secondary_indexes}

    while True:
        indexes = client.describe_table(TableName=table_name)["Table"].get(
            "GlobalSecondaryIndexes",
            [],
        )
        # `Backfilling` matters as much as the status: an index reports ACTIVE
        # while it is still filling, and querying it then returns a partial
        # answer. A short list of notifications reading as "nothing arrived" is
        # worse than an error — somebody would go and rewrite a forwarding rule
        # that was working.
        ready = {
            name
            for index in indexes
            if (name := index.get("IndexName")) is not None
            and index.get("IndexStatus") == "ACTIVE"
            and not index.get("Backfilling", False)
        }

        if expected <= ready:
            return

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Indexes {sorted(expected - ready)} on {table_name} are still "
                f"not ready after {INDEX_ACTIVE_TIMEOUT_SECONDS}s. Backfilling "
                "a large table can take longer; check the table in the console "
                "and re-run.",
            )

        time.sleep(INDEX_POLL_SECONDS)


def _add_missing_indexes(
    client: DynamoDBClient,
    *,
    table_name: str,
    secondary_indexes: Sequence[SecondaryIndex],
    throughput: ProvisionedThroughputTypeDef | None,
    on_demand_cap: OnDemandThroughputTypeDef | None,
) -> None:
    """Attach indexes a table predating them does not have yet.

    `create_table` is skipped once a table exists, so without this an index
    added later would only ever reach a freshly created environment.
    """
    described = client.describe_table(TableName=table_name)["Table"]
    existing = {
        name
        for index in described.get("GlobalSecondaryIndexes", [])
        if (name := index.get("IndexName")) is not None
    }

    for index in secondary_indexes:
        if index.name in existing:
            continue

        action: CreateGlobalSecondaryIndexActionTypeDef = {
            "IndexName": index.name,
            "KeySchema": [{"AttributeName": index.partition_key, "KeyType": "HASH"}],
            "Projection": index.projection(),
        }

        if throughput is not None:
            action["ProvisionedThroughput"] = throughput

        if on_demand_cap is not None:
            action["OnDemandThroughput"] = on_demand_cap

        client.update_table(
            TableName=table_name,
            AttributeDefinitions=[
                {"AttributeName": index.partition_key, "AttributeType": "S"},
            ],
            GlobalSecondaryIndexUpdates=[{"Create": action}],
        )
        # One index at a time: each is its own `UpdateTable`, and DynamoDB
        # refuses a second one while the first is still applying.
        _wait_until_active(client, table_name=table_name)


def provision_table(
    client: DynamoDBClient,
    *,
    table_name: str,
    partition_key: str = PARTITION_KEY,
    sort_key: str | None = None,
    sort_key_type: ScalarAttributeTypeType = "N",
    billing_mode: BillingMode = "PAY_PER_REQUEST",
    read_capacity: int = 5,
    write_capacity: int = 5,
    max_read_units: int = 25,
    max_write_units: int = 25,
    enable_ttl: bool = True,
    enable_point_in_time_recovery: bool = True,
    secondary_indexes: Sequence[SecondaryIndex] = (),
) -> None:
    """Create a table, back it up continuously, and enable its TTL.

    The two billing modes take different arguments — DynamoDB rejects a
    throughput specification on an on-demand table — so they are two distinct
    calls rather than one call with conditional keyword arguments.
    """
    attribute_definitions: list[AttributeDefinitionTypeDef] = [
        {"AttributeName": partition_key, "AttributeType": "S"},
    ]
    key_schema: list[KeySchemaElementTypeDef] = [
        {"AttributeName": partition_key, "KeyType": "HASH"},
    ]

    if sort_key is not None:
        attribute_definitions.append(
            {"AttributeName": sort_key, "AttributeType": sort_key_type},
        )
        key_schema.append({"AttributeName": sort_key, "KeyType": "RANGE"})

    throughput: ProvisionedThroughputTypeDef = {
        "ReadCapacityUnits": read_capacity,
        "WriteCapacityUnits": write_capacity,
    }
    on_demand_cap = _on_demand_cap(
        billing_mode=billing_mode,
        max_read_units=max_read_units,
        max_write_units=max_write_units,
    )

    with contextlib.suppress(client.exceptions.ResourceInUseException):
        if billing_mode == "PROVISIONED":
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=attribute_definitions,
                KeySchema=key_schema,
                BillingMode="PROVISIONED",
                ProvisionedThroughput=throughput,
            )
        elif on_demand_cap is None:
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=attribute_definitions,
                KeySchema=key_schema,
                BillingMode="PAY_PER_REQUEST",
            )
        else:
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=attribute_definitions,
                KeySchema=key_schema,
                BillingMode="PAY_PER_REQUEST",
                OnDemandThroughput=on_demand_cap,
            )

    client.get_waiter("table_exists").wait(TableName=table_name)

    # The mode first: everything below depends on which one the table is on,
    # and a table that already existed was created under whatever the
    # configuration used to say.
    if _reconcile_billing_mode(
        client,
        table_name=table_name,
        billing_mode=billing_mode,
        throughput=throughput,
        on_demand_cap=on_demand_cap,
    ):
        _wait_until_active(client, table_name=table_name)

    if billing_mode == "PROVISIONED":
        _reconcile_throughput(client, table_name=table_name, throughput=throughput)
        _wait_until_active(client, table_name=table_name)

    # Indexes are attached after the fact even on a brand-new table, so a
    # fresh environment and one predating the index take the same code path.
    if secondary_indexes:
        _add_missing_indexes(
            client,
            table_name=table_name,
            secondary_indexes=secondary_indexes,
            throughput=_index_throughput(
                billing_mode=billing_mode,
                read_capacity=read_capacity,
                write_capacity=write_capacity,
            ),
            on_demand_cap=on_demand_cap,
        )
        _wait_until_indexes_are_active(
            client,
            table_name=table_name,
            secondary_indexes=secondary_indexes,
        )

    if enable_point_in_time_recovery:
        _enable_point_in_time_recovery(client, table_name=table_name)

    if not enable_ttl:
        # Inboxes are user configuration: they must never expire.
        return

    description = client.describe_time_to_live(TableName=table_name)
    ttl_status = description["TimeToLiveDescription"].get("TimeToLiveStatus")

    if ttl_status in {"ENABLED", "ENABLING"}:
        return

    client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": TTL_ATTRIBUTE},
    )


def _enable_point_in_time_recovery(
    client: DynamoDBClient,
    *,
    table_name: str,
) -> None:
    """Let this table be restored to any second in the last 35 days.

    On by default because every table here holds something nobody can
    reconstruct: the ledger these balances are replayed from, the credentials
    people log in with, the merchants they renamed by hand. What it guards
    against is not AWS failing, which is rare — it is the ordinary way data
    dies, which is somebody deleting the wrong table or a bad deploy
    corrupting rows for an hour before anyone notices.

    It is also the only safeguard on the list that cannot be added after the
    fact. An alarm nobody set up can be set up the day it is missed; a table
    that was never backed up is simply gone.

    Applied whether or not the table was just created, so an environment that
    predates this gets it on the next `just aws-provision`. Idempotent: it
    asks first, because enabling it twice is an error on a real account.
    """
    description = client.describe_continuous_backups(TableName=table_name)
    recovery = description["ContinuousBackupsDescription"].get(
        "PointInTimeRecoveryDescription",
        {},
    )

    if recovery.get("PointInTimeRecoveryStatus") in {"ENABLED", "ENABLING"}:
        return

    client.update_continuous_backups(
        TableName=table_name,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )


def provision_queue(client: SQSClient, *, queue_name: str) -> tuple[str, str]:
    """Create the work queue and its dead-letter queue. Returns both URLs.

    A DLQ is not optional here: SQS delivery is at-least-once, and a message
    that keeps failing to parse must stop cycling through the workers.
    """
    dead_letter_url = client.create_queue(
        QueueName=f"{queue_name}{DEAD_LETTER_SUFFIX}",
    )["QueueUrl"]
    dead_letter_arn = client.get_queue_attributes(
        QueueUrl=dead_letter_url,
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]

    queue_url = client.create_queue(QueueName=queue_name)["QueueUrl"]
    client.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "VisibilityTimeout": str(VISIBILITY_TIMEOUT_SECONDS),
            "MessageRetentionPeriod": str(MESSAGE_RETENTION_SECONDS),
            "RedrivePolicy": json.dumps(
                {
                    "deadLetterTargetArn": dead_letter_arn,
                    "maxReceiveCount": MAX_RECEIVE_COUNT,
                },
            ),
        },
    )

    return queue_url, dead_letter_url


def provision_integration_event_subscription(
    events_client: EventBridgeClient,
    sqs_client: SQSClient,
    *,
    event_bus_name: str,
    queue_name: str,
    rule_name: str = INTEGRATION_EVENTS_RULE,
) -> str:
    """Route every `finflow.*` integration event to a queue and return its URL.

    This is the tap that makes the bus observable: without at least one
    target, `put_events` succeeds and the event goes nowhere anybody can look
    at. A real subscriber — the Financial context, once it exists — attaches
    its own rule the same way; this one exists so the bus can be inspected
    and tested before that consumer is written.
    """
    queue_url = sqs_client.create_queue(QueueName=queue_name)["QueueUrl"]
    queue_arn = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]

    events_client.put_rule(
        Name=rule_name,
        EventBusName=event_bus_name,
        # Matches by prefix so a context added later is picked up without
        # touching this rule.
        EventPattern=json.dumps({"source": [{"prefix": f"{SOURCE_PREFIX}."}]}),
        State="ENABLED",
        Description="Every finflow integration event, for inspection and tests.",
    )
    events_client.put_targets(
        Rule=rule_name,
        EventBusName=event_bus_name,
        Targets=[{"Id": INTEGRATION_EVENTS_TARGET_ID, "Arn": queue_arn}],
    )

    return queue_url


def provision_context_subscription(
    events_client: EventBridgeClient,
    sqs_client: SQSClient,
    *,
    event_bus_name: str,
    queue_name: str,
    rule_name: str,
    target_id: str,
    event_pattern: dict[str, list[str]],
) -> tuple[str, str]:
    """Give one context its own queue, fed by the events it subscribes to.

    Returns the queue URL and its dead-letter queue URL. Unlike the bus tap,
    this is a working path: the queue gets a redrive policy so a message that
    keeps failing stops cycling, and a resource policy so EventBridge is
    actually allowed to write to it. Without that policy the rule matches, the
    delivery is refused, and nothing anywhere reports an error.
    """
    queue_url, dead_letter_url = provision_queue(sqs_client, queue_name=queue_name)
    queue_arn = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["QueueArn"],
    )["Attributes"]["QueueArn"]

    rule_arn = events_client.put_rule(
        Name=rule_name,
        EventBusName=event_bus_name,
        EventPattern=json.dumps(event_pattern),
        State="ENABLED",
        Description=f"Events {queue_name} subscribes to.",
    )["RuleArn"]

    sqs_client.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowEventBridgeDelivery",
                            "Effect": "Allow",
                            "Principal": {"Service": "events.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            # Scoped to this one rule: the queue is not a
                            # drop box for anything else on the bus.
                            "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
                        },
                    ],
                },
            ),
        },
    )
    events_client.put_targets(
        Rule=rule_name,
        EventBusName=event_bus_name,
        Targets=[{"Id": target_id, "Arn": queue_arn}],
    )

    return queue_url, dead_letter_url


def provision_event_bus(client: EventBridgeClient, *, event_bus_name: str) -> None:
    if event_bus_name == "default":
        # The default bus always exists and cannot be created.
        return

    with contextlib.suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_event_bus(Name=event_bus_name)


def _step(label: str) -> float:
    """Announce a step before it blocks on the network, and return when it
    started. Table creation in particular waits on a real `table_exists`
    poll — without this, a run against real AWS looks hung for the ten-plus
    seconds that takes, instead of visibly in progress.
    """
    print(f"  {label} ...", flush=True)

    return time.monotonic()


def _done(started_at: float) -> None:
    print(f"    done ({time.monotonic() - started_at:.1f}s)", flush=True)


def provision() -> ProvisionedResources:
    settings = get_ingestion_settings()
    identity_settings = get_identity_settings()
    merchant_settings = get_merchant_settings()
    financial_settings = get_financial_settings()

    started = _step(f"table {settings.notifications_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=settings.notifications_table,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        max_read_units=settings.dynamodb_max_read_units,
        max_write_units=settings.dynamodb_max_write_units,
        secondary_indexes=(
            SecondaryIndex(
                name=NOTIFICATIONS_BY_USER_INDEX,
                partition_key=NOTIFICATION_USER_ID_ATTRIBUTE,
                # Everything a list screen shows, and nothing else. An index is
                # a full copy of what it projects, and duplicating every
                # untrusted email body to answer a question that never involves
                # one is not a trade worth making.
                projected_attributes=SUMMARY_ATTRIBUTES,
            ),
        ),
    )
    _done(started)

    started = _step(f"table {settings.user_inboxes_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=settings.user_inboxes_table,
        partition_key=INBOX_PARTITION_KEY,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        max_read_units=settings.dynamodb_max_read_units,
        max_write_units=settings.dynamodb_max_write_units,
        enable_ttl=False,
        secondary_indexes=(
            SecondaryIndex(
                name=INBOX_BY_USER_INDEX,
                partition_key=INBOX_USER_ID_ATTRIBUTE,
            ),
        ),
    )
    _done(started)

    started = _step(f"table {identity_settings.users_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=identity_settings.users_table,
        partition_key=USERS_PARTITION_KEY,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        max_read_units=settings.dynamodb_max_read_units,
        max_write_units=settings.dynamodb_max_write_units,
        enable_ttl=False,
    )
    _done(started)

    started = _step(f"table {merchant_settings.merchants_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=merchant_settings.merchants_table,
        partition_key=MERCHANT_PARTITION_KEY,
        sort_key=MERCHANT_SORT_KEY,
        sort_key_type="S",
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        max_read_units=settings.dynamodb_max_read_units,
        max_write_units=settings.dynamodb_max_write_units,
        # TTL is on for the handled-event markers, which are the only records
        # in that table carrying an expiry. Merchants have none and stay.
        enable_ttl=True,
    )
    _done(started)

    started = _step(f"queue {settings.parse_queue_name} (+ dead-letter queue)")
    queue_url, dead_letter_url = provision_queue(
        get_sqs_client(),
        queue_name=settings.parse_queue_name,
    )
    _done(started)

    started = _step(f"table {financial_settings.accounts_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=financial_settings.accounts_table,
        partition_key=FINANCIAL_PARTITION_KEY,
        sort_key=FINANCIAL_SORT_KEY,
        sort_key_type="S",
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        max_read_units=settings.dynamodb_max_read_units,
        max_write_units=settings.dynamodb_max_write_units,
        # Nothing here expires: an account, the fingerprints it answers to and
        # every ledger row are the record a balance is rebuilt from.
        enable_ttl=False,
    )
    _done(started)

    started = _step(f"event bus {settings.event_bus_name}")
    provision_event_bus(
        get_eventbridge_client(),
        event_bus_name=settings.event_bus_name,
    )
    _done(started)

    started = _step(f"merchant subscription ({merchant_settings.events_queue_name})")
    merchant_events_url, _ = provision_context_subscription(
        get_eventbridge_client(),
        get_sqs_client(),
        event_bus_name=settings.event_bus_name,
        queue_name=merchant_settings.events_queue_name,
        rule_name=MERCHANT_EVENTS_RULE,
        target_id=MERCHANT_EVENTS_TARGET_ID,
        event_pattern={
            "source": [INGESTION_SOURCE],
            "detail-type": [TRANSACTION_EXTRACTED],
        },
    )
    _done(started)

    started = _step(f"financial subscription ({financial_settings.events_queue_name})")
    financial_events_url, _ = provision_context_subscription(
        get_eventbridge_client(),
        get_sqs_client(),
        event_bus_name=settings.event_bus_name,
        queue_name=financial_settings.events_queue_name,
        rule_name=FINANCIAL_EVENTS_RULE,
        target_id=FINANCIAL_EVENTS_TARGET_ID,
        event_pattern={
            "source": [INGESTION_SOURCE],
            "detail-type": [TRANSACTION_EXTRACTED],
        },
    )
    _done(started)

    started = _step(
        f"integration-events tap ({settings.integration_events_queue_name})",
    )
    integration_events_url = provision_integration_event_subscription(
        get_eventbridge_client(),
        get_sqs_client(),
        event_bus_name=settings.event_bus_name,
        queue_name=settings.integration_events_queue_name,
    )
    _done(started)

    return ProvisionedResources(
        table_name=settings.notifications_table,
        inboxes_table_name=settings.user_inboxes_table,
        users_table_name=identity_settings.users_table,
        merchants_table_name=merchant_settings.merchants_table,
        financial_table_name=financial_settings.accounts_table,
        queue_url=queue_url,
        dead_letter_queue_url=dead_letter_url,
        merchant_events_queue_url=merchant_events_url,
        financial_events_queue_url=financial_events_url,
        event_bus_name=settings.event_bus_name,
        integration_events_queue_url=integration_events_url,
    )


def main() -> None:
    aws_settings = get_aws_settings()
    ingestion_settings = get_ingestion_settings()
    endpoint = aws_settings.endpoint_url or "real AWS"
    print(f"Provisioning against {endpoint} ({aws_settings.region})...", flush=True)
    resources = provision()

    if ingestion_settings.dynamodb_billing_mode == "PROVISIONED":
        capacity = (
            f"{ingestion_settings.dynamodb_read_capacity} read / "
            f"{ingestion_settings.dynamodb_write_capacity} write units"
        )
    else:
        capacity = (
            "on-demand, capped at "
            f"{ingestion_settings.dynamodb_max_read_units} read / "
            f"{ingestion_settings.dynamodb_max_write_units} write units/sec"
        )

    print(f"Provisioned against {endpoint} ({aws_settings.region})")
    print(
        f"  DynamoDB table : {resources.table_name} "
        f"({capacity}, TTL on {TTL_ATTRIBUTE})",
    )
    # Named once rather than on every line: it is the same for all of them,
    # and it is the one thing here that cannot be added after it is needed.
    print(f"  DynamoDB table : {resources.inboxes_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.users_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.merchants_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.financial_table_name} ({capacity})")
    print("                   every table restorable to any second, last 35 days")
    print(f"  SQS queue      : {resources.queue_url}")
    print(f"  SQS DLQ        : {resources.dead_letter_queue_url}")
    print(
        f"  Merchant queue : {resources.merchant_events_queue_url}\n"
        f"                   (rule {MERCHANT_EVENTS_RULE}, "
        f"{INGESTION_SOURCE} {TRANSACTION_EXTRACTED} -> {MERCHANT_SOURCE})",
    )
    print(
        f"  Financial queue: {resources.financial_events_queue_url}\n"
        f"                   (rule {FINANCIAL_EVENTS_RULE}, "
        f"{INGESTION_SOURCE} {TRANSACTION_EXTRACTED} -> balances)",
    )
    print(f"  Event bus      : {resources.event_bus_name}")
    print(
        f"  Bus tap        : {resources.integration_events_queue_url}\n"
        f"                   (rule {INTEGRATION_EVENTS_RULE}, "
        f"pattern source prefix {SOURCE_PREFIX}.)",
    )

    missing = [
        (name, value)
        for name, value, current in (
            (
                "INGESTION_PARSE_QUEUE_URL",
                resources.queue_url,
                ingestion_settings.parse_queue_url,
            ),
            (
                "INGESTION_INTEGRATION_EVENTS_QUEUE_URL",
                resources.integration_events_queue_url,
                ingestion_settings.integration_events_queue_url,
            ),
            (
                "MERCHANT_EVENTS_QUEUE_URL",
                resources.merchant_events_queue_url,
                get_merchant_settings().events_queue_url,
            ),
            (
                "FINANCIAL_EVENTS_QUEUE_URL",
                resources.financial_events_queue_url,
                get_financial_settings().events_queue_url,
            ),
        )
        if current != value
    ]

    if missing:
        lines = "\n".join(f"{name}={value}" for name, value in missing)
        print(f"\nPut this in {ENV_FILE}:\n\n{lines}")


if __name__ == "__main__":
    main()
