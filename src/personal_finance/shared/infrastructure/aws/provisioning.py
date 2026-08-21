"""Creates the AWS resources the ingestion context needs.

The same script targets a local emulator or a real account: only the endpoint
and the credentials change. Every step is idempotent, so it is safe to re-run.
"""

from __future__ import annotations

from collections.abc import Sequence
import contextlib
import dataclasses
import json

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
from personal_finance.contexts.ingestion.infrastructure.mailbox.simulated import (
    MAILBOX_PARTITION_KEY,
    MAILBOX_SORT_KEY,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    CONNECTION_BY_USER_INDEX,
    CONNECTION_PARTITION_KEY,
    USER_ID_ATTRIBUTE as CONNECTION_USER_ID_ATTRIBUTE,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    INBOX_BY_USER_INDEX,
    INBOX_PARTITION_KEY,
    USER_ID_ATTRIBUTE as INBOX_USER_ID_ATTRIBUTE,
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
)


TTL_ATTRIBUTE = "expires_at"
DEAD_LETTER_SUFFIX = "-dlq"

# Every context publishes under `finflow.<context>`, so one prefix pattern
# subscribes to all of them.
SOURCE_PREFIX = "finflow"
INTEGRATION_EVENTS_RULE = "finflow-integration-events"
INTEGRATION_EVENTS_TARGET_ID = "integration-events-queue"
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
    mailbox_connections_table_name: str
    simulated_mailbox_table_name: str
    queue_url: str
    dead_letter_queue_url: str
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


def provision_event_bus(client: EventBridgeClient, *, event_bus_name: str) -> None:
    if event_bus_name == "default":
        # The default bus always exists and cannot be created.
        return

    with contextlib.suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_event_bus(Name=event_bus_name)


def provision() -> ProvisionedResources:
    settings = get_ingestion_settings()
    identity_settings = get_identity_settings()

    provision_table(
        get_dynamodb_client(),
        table_name=settings.notifications_table,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
    )
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
    provision_table(
        get_dynamodb_client(),
        table_name=identity_settings.users_table,
        partition_key=USERS_PARTITION_KEY,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        enable_ttl=False,
    )
    provision_table(
        get_dynamodb_client(),
        table_name=settings.mailbox_connections_table,
        partition_key=CONNECTION_PARTITION_KEY,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        enable_ttl=False,
        secondary_indexes=(
            SecondaryIndex(
                name=CONNECTION_BY_USER_INDEX,
                partition_key=CONNECTION_USER_ID_ATTRIBUTE,
            ),
        ),
    )
    provision_table(
        get_dynamodb_client(),
        table_name=settings.simulated_mailbox_table,
        partition_key=MAILBOX_PARTITION_KEY,
        sort_key=MAILBOX_SORT_KEY,
        billing_mode=settings.dynamodb_billing_mode,
        read_capacity=settings.dynamodb_read_capacity,
        write_capacity=settings.dynamodb_write_capacity,
        enable_ttl=False,
    )
    queue_url, dead_letter_url = provision_queue(
        get_sqs_client(),
        queue_name=settings.parse_queue_name,
    )
    provision_event_bus(
        get_eventbridge_client(),
        event_bus_name=settings.event_bus_name,
    )
    integration_events_url = provision_integration_event_subscription(
        get_eventbridge_client(),
        get_sqs_client(),
        event_bus_name=settings.event_bus_name,
        queue_name=settings.integration_events_queue_name,
    )

    return ProvisionedResources(
        table_name=settings.notifications_table,
        inboxes_table_name=settings.user_inboxes_table,
        users_table_name=identity_settings.users_table,
        mailbox_connections_table_name=settings.mailbox_connections_table,
        simulated_mailbox_table_name=settings.simulated_mailbox_table,
        queue_url=queue_url,
        dead_letter_queue_url=dead_letter_url,
        event_bus_name=settings.event_bus_name,
        integration_events_queue_url=integration_events_url,
    )


def main() -> None:
    aws_settings = get_aws_settings()
    ingestion_settings = get_ingestion_settings()
    endpoint = aws_settings.endpoint_url or "real AWS"
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
    print(f"  DynamoDB table : {resources.mailbox_connections_table_name} ({capacity})")
    print(f"  DynamoDB table : {resources.simulated_mailbox_table_name} ({capacity})")
    print(f"  SQS queue      : {resources.queue_url}")
    print(f"  SQS DLQ        : {resources.dead_letter_queue_url}")
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
        )
        if current != value
    ]

    if missing:
        lines = "\n".join(f"{name}={value}" for name, value in missing)
        print(f"\nPut this in {ENV_FILE}:\n\n{lines}")


if __name__ == "__main__":
    main()
