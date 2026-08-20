from __future__ import annotations

import json

from mypy_boto3_sqs.client import SQSClient

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)


MESSAGE_SCHEMA_VERSION = 1


class SQSQueuePublisher:
    """`QueuePublisher` backed by an SQS standard queue.

    The body is written field by field rather than dumped from the dataclass:
    it is a wire contract consumed by the parsing worker, and it must not shift
    when the application-layer message is refactored.
    """

    def __init__(self, *, client: SQSClient, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def enqueue(self, message: ParseNotificationMessage) -> None:
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(
                {
                    "version": MESSAGE_SCHEMA_VERSION,
                    "notification_id": str(message.notification_id.value),
                    "idempotency_key": message.idempotency_key.value,
                    "message_id": message.message_id.value,
                    "received_at": message.received_at.as_epoch_seconds(),
                },
            ),
        )
