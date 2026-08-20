import json

from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    SQSQueuePublisher,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    MAX_RECEIVE_COUNT,
    VISIBILITY_TIMEOUT_SECONDS,
    provision_queue,
)


QUEUE_NAME = "parse-notifications"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def queue_url(sqs_client: SQSClient) -> str:
    url, _ = provision_queue(sqs_client, queue_name=QUEUE_NAME)

    return url


def _message() -> ParseNotificationMessage:
    message_id = EmailMessageId("message-1")

    return ParseNotificationMessage(
        notification_id=NotificationId.for_message(
            user_id=USER_ID,
            message_id=message_id,
        ),
        user_id=USER_ID,
        idempotency_key=IdempotencyKey.from_message(
            user_id=USER_ID,
            message_id=message_id,
        ),
        message_id=message_id,
        received_at=PosixTime.now(),
    )


def test_enqueued_message_is_readable_by_a_worker(
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    SQSQueuePublisher(client=sqs_client, queue_url=queue_url).enqueue(_message())

    received = sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)

    messages = received.get("Messages", [])
    assert len(messages) == 1
    body = json.loads(messages[0].get("Body", ""))
    assert body["message_id"] == "message-1"
    assert body["version"] == 1
    assert "raw_content" not in body


def test_queue_is_configured_with_a_dead_letter_queue(
    sqs_client: SQSClient,
    queue_url: str,
) -> None:
    attributes = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["RedrivePolicy", "VisibilityTimeout"],
    )["Attributes"]

    redrive = json.loads(attributes["RedrivePolicy"])
    assert redrive["maxReceiveCount"] == MAX_RECEIVE_COUNT
    assert redrive["deadLetterTargetArn"].endswith(f"{QUEUE_NAME}-dlq")
    assert attributes["VisibilityTimeout"] == str(VISIBILITY_TIMEOUT_SECONDS)


def test_provisioning_the_queue_twice_is_safe(sqs_client: SQSClient) -> None:
    first, first_dlq = provision_queue(sqs_client, queue_name=QUEUE_NAME)
    second, second_dlq = provision_queue(sqs_client, queue_name=QUEUE_NAME)

    assert first == second
    assert first_dlq == second_dlq
