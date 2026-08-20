"""Creates the AWS resources the ingestion context needs.

The same script targets a local emulator or a real account: only the endpoint
and the credentials change. Every step is idempotent, so it is safe to re-run.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json

from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_dynamodb.type_defs import (
    AttributeDefinitionTypeDef,
    KeySchemaElementTypeDef,
)
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient

from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    INBOX_PARTITION_KEY,
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
    get_ingestion_settings,
)


TTL_ATTRIBUTE = "expires_at"
DEAD_LETTER_SUFFIX = "-dlq"
MAX_RECEIVE_COUNT = 5
# Long enough for a parse plus the LLM fallback, short enough that a crashed
# worker releases the message quickly.
VISIBILITY_TIMEOUT_SECONDS = 120
MESSAGE_RETENTION_SECONDS = 1_209_600  # 14 days, the SQS maximum.


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ProvisionedResources:
    table_name: str
    inboxes_table_name: str
    queue_url: str
    dead_letter_queue_url: str
    event_bus_name: str


def provision_table(
    client: DynamoDBClient,
    *,
    table_name: str,
    partition_key: str = PARTITION_KEY,
    billing_mode: BillingMode = "PROVISIONED",
    read_capacity: int = 5,
    write_capacity: int = 5,
    enable_ttl: bool = True,
) -> None:
    """Create a single-key table and, unless told otherwise, enable its TTL.

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


def provision_event_bus(client: EventBridgeClient, *, event_bus_name: str) -> None:
    if event_bus_name == "default":
        # The default bus always exists and cannot be created.
        return

    with contextlib.suppress(client.exceptions.ResourceAlreadyExistsException):
        client.create_event_bus(Name=event_bus_name)


def provision() -> ProvisionedResources:
    settings = get_ingestion_settings()

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
    )
    queue_url, dead_letter_url = provision_queue(
        get_sqs_client(),
        queue_name=settings.parse_queue_name,
    )
    provision_event_bus(
        get_eventbridge_client(),
        event_bus_name=settings.event_bus_name,
    )

    return ProvisionedResources(
        table_name=settings.notifications_table,
        inboxes_table_name=settings.user_inboxes_table,
        queue_url=queue_url,
        dead_letter_queue_url=dead_letter_url,
        event_bus_name=settings.event_bus_name,
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
    print(f"  SQS queue      : {resources.queue_url}")
    print(f"  SQS DLQ        : {resources.dead_letter_queue_url}")
    print(f"  Event bus      : {resources.event_bus_name}")

    if ingestion_settings.parse_queue_url != resources.queue_url:
        print(
            f"\nPut this in {ENV_FILE} — the application reads the queue by URL "
            f"and will refuse to start without it:\n\n"
            f"INGESTION_PARSE_QUEUE_URL={resources.queue_url}",
        )


if __name__ == "__main__":
    main()
