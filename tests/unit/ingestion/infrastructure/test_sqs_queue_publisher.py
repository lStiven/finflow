import json
from typing import cast

from mypy_boto3_sqs.client import SQSClient

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
from personal_finance.shared.domain.value_objects import PosixTime


QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/parse-notifications"
RECEIVED_AT_EPOCH = 1_700_000_000


class FakeSQSClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send_message(self, **kwargs: str) -> dict[str, str]:
        self.sent.append(kwargs)

        return {}


def test_message_body_matches_the_worker_contract() -> None:
    client = FakeSQSClient()
    publisher = SQSQueuePublisher(
        client=cast(SQSClient, client),
        queue_url=QUEUE_URL,
    )
    message_id = EmailMessageId("message-1")

    publisher.enqueue(
        ParseNotificationMessage(
            notification_id=NotificationId.for_message(message_id),
            idempotency_key=IdempotencyKey.from_message_id(message_id),
            message_id=message_id,
            received_at=PosixTime.from_epoch_seconds(RECEIVED_AT_EPOCH),
        ),
    )

    assert len(client.sent) == 1
    assert client.sent[0]["QueueUrl"] == QUEUE_URL

    body = json.loads(client.sent[0]["MessageBody"])
    assert body == {
        "version": 1,
        "notification_id": str(NotificationId.for_message(message_id).value),
        "idempotency_key": IdempotencyKey.from_message_id(message_id).value,
        "message_id": "message-1",
        "received_at": RECEIVED_AT_EPOCH,
    }


def test_body_never_carries_the_raw_email() -> None:
    client = FakeSQSClient()
    publisher = SQSQueuePublisher(
        client=cast(SQSClient, client),
        queue_url=QUEUE_URL,
    )
    message_id = EmailMessageId("message-1")

    publisher.enqueue(
        ParseNotificationMessage(
            notification_id=NotificationId.for_message(message_id),
            idempotency_key=IdempotencyKey.from_message_id(message_id),
            message_id=message_id,
            received_at=PosixTime.now(),
        ),
    )

    assert "raw_content" not in client.sent[0]["MessageBody"]
