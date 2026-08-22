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
    ProvisionedThroughputTypeDef,
)
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient

from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as USERS_PARTITION_KEY,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
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
MAX_RECEIVE_COUNT = 5
# Long enough for a parse plus the LLM fallback, short enough that a crashed
# worker releases the message quickly.
VISIBILITY_TIMEOUT_SECONDS = 120
MESSAGE_RETENTION_SECONDS = 1_209_600  # 14 days, the SQS maximum.


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ProvisionedResources:
    table_name: str
    inboxes_table_name: str
    users_table_name: str
    merchants_table_name: str
    queue_url: str
    dead_letter_queue_url: str
    merchant_events_queue_url: str
    event_bus_name: str
    integration_events_queue_url: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SecondaryIndex:
    """A global secondary index over one attribute.

    Projects every attribute: the indexes here answer "give me the records
    belonging to X", and a key-only projection would force a second read per
    hit to recover the record itself.
    """

    name: str
    partition_key: str


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


def _add_missing_indexes(
    client: DynamoDBClient,
    *,
    table_name: str,
    secondary_indexes: Sequence[SecondaryIndex],
    throughput: ProvisionedThroughputTypeDef | None,
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
            "Projection": {"ProjectionType": "ALL"},
        }

        if throughput is not None:
            action["ProvisionedThroughput"] = throughput

        client.update_table(
            TableName=table_name,
            AttributeDefinitions=[
                {"AttributeName": index.partition_key, "AttributeType": "S"},
            ],
            GlobalSecondaryIndexUpdates=[{"Create": action}],
        )


def provision_table(
    client: DynamoDBClient,
    *,
    table_name: str,
    partition_key: str = PARTITION_KEY,
    sort_key: str | None = None,
    sort_key_type: ScalarAttributeTypeType = "N",
    billing_mode: BillingMode = "PROVISIONED",
    read_capacity: int = 5,
    write_capacity: int = 5,
    enable_ttl: bool = True,
    secondary_indexes: Sequence[SecondaryIndex] = (),
) -> None:
    """Create a table and, unless told otherwise, enable its TTL.

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

    with contextlib.suppress(client.exceptions.ResourceInUseException):
        if billing_mode == "PROVISIONED":
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=attribute_definitions,
                KeySchema=key_schema,
                BillingMode="PROVISIONED",
                ProvisionedThroughput={
                    "ReadCapacityUnits": read_capacity,
                    "WriteCapacityUnits": write_capacity,
                },
            )
        else:
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=attribute_definitions,
                KeySchema=key_schema,
                BillingMode="PAY_PER_REQUEST",
            )

    client.get_waiter("table_exists").wait(TableName=table_name)

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
        )

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

    started = _step(f"table {settings.notifications_table}")
    provision_table(
        get_dynamodb_client(),
        table_name=settings.notifications_table,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
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
        queue_url=queue_url,
        dead_letter_queue_url=dead_letter_url,
        merchant_events_queue_url=merchant_events_url,
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
        capacity = "on-demand"

    print(f"Provisioned against {endpoint} ({aws_settings.region})")
    print(
        f"  DynamoDB table : {resources.table_name} "
        f"({capacity}, TTL on {TTL_ATTRIBUTE})",
    )
    print(f"  DynamoDB table : {resources.inboxes_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.users_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.merchants_table_name} ({capacity})")
    print(f"  SQS queue      : {resources.queue_url}")
    print(f"  SQS DLQ        : {resources.dead_letter_queue_url}")
    print(
        f"  Merchant queue : {resources.merchant_events_queue_url}\n"
        f"                   (rule {MERCHANT_EVENTS_RULE}, "
        f"{INGESTION_SOURCE} {TRANSACTION_EXTRACTED} -> {MERCHANT_SOURCE})",
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
        )
        if current != value
    ]

    if missing:
        lines = "\n".join(f"{name}={value}" for name, value in missing)
        print(f"\nPut this in {ENV_FILE}:\n\n{lines}")


if __name__ == "__main__":
    main()
