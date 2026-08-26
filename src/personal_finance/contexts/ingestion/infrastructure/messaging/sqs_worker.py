from __future__ import annotations

import dataclasses
import enum
import logging
import uuid

from mypy_boto3_sqs.client import SQSClient
from pydantic import BaseModel, Field, ValidationError

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    MESSAGE_SCHEMA_VERSION,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_POLL = 10
WAIT_TIME_SECONDS = 20


class ParseNotificationBody(BaseModel):
    """The queue payload, validated at the boundary.

    We wrote this message ourselves, but it crossed a queue and may have been
    produced by an older deploy, so it is parsed rather than trusted.
    """

    version: int = Field(ge=1)
    notification_id: uuid.UUID
    user_id: uuid.UUID
    idempotency_key: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    received_at: int

    def to_message(self) -> ParseNotificationMessage:
        return ParseNotificationMessage(
            notification_id=NotificationId(value=self.notification_id),
            user_id=UserId(value=self.user_id),
            idempotency_key=IdempotencyKey(self.idempotency_key),
            message_id=EmailMessageId(self.message_id),
            received_at=PosixTime.from_epoch_seconds(self.received_at),
        )


class MessageOutcome(enum.Enum):
    """What a single message left behind, whoever delivered it.

    Public because the polling loop is no longer the only caller: the Lambda
    entry point reads the same three answers and turns them into the batch
    response AWS expects.
    """

    HANDLED = "handled"
    # Understood, but nothing to do with it. Deleted.
    DISCARDED = "discarded"
    # Left on the queue for somebody who understands it.
    RETRY = "retry"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PollResult:
    received: int = 0
    handled: int = 0
    rejected: int = 0


class SQSParseWorker:
    """Drains the parse queue, one batch at a time.

    A message is deleted only after the use case returns. Anything that raises
    stays on the queue, becomes visible again when the visibility timeout
    expires, and eventually lands in the dead-letter queue — which is safe
    because parsing the same notification twice is a no-op.
    """

    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: ParseNotificationUseCase,
    ) -> None:
        self._client = client
        self._queue_url = queue_url
        self._use_case = use_case

    def poll_once(self, *, wait_seconds: int = WAIT_TIME_SECONDS) -> PollResult:
        response = self._client.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=MAX_MESSAGES_PER_POLL,
            WaitTimeSeconds=wait_seconds,
        )
        messages = response.get("Messages", [])
        handled = 0
        rejected = 0

        for message in messages:
            receipt = message.get("ReceiptHandle")

            if receipt is None:
                continue

            outcome = self.handle(message.get("Body", ""))

            if outcome is MessageOutcome.RETRY:
                rejected += 1
                continue

            if outcome is MessageOutcome.HANDLED:
                handled += 1
            else:
                rejected += 1

            self._delete(receipt)

        return PollResult(
            received=len(messages),
            handled=handled,
            rejected=rejected,
        )

    def handle(self, body: str) -> MessageOutcome:
        try:
            parsed = ParseNotificationBody.model_validate_json(body)
        except ValidationError:
            # Unparseable payloads never become parseable. Retrying one until
            # the DLQ takes it only delays the queue.
            _logger.exception("discarding malformed parse message")

            return MessageOutcome.DISCARDED

        if parsed.version != MESSAGE_SCHEMA_VERSION:
            # Left on the queue on purpose: a version we do not understand was
            # written by a newer deploy, and a newer worker may still pick it
            # up. If none does, redrive moves it to the DLQ.
            _logger.warning(
                "unsupported parse message version",
                extra={"version": parsed.version},
            )

            return MessageOutcome.RETRY

        result = self._use_case.execute(parsed.to_message())
        _logger.info(
            "parsed notification",
            extra={
                "outcome": result.outcome.value,
                "notification_id": str(parsed.notification_id),
            },
        )

        return MessageOutcome.HANDLED

    def _delete(self, receipt_handle: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
