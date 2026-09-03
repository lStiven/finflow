from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import uuid

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
from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
    SQSPollingWorker,
)


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


_logger = logging.getLogger(__name__)


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


class SQSParseWorker(SQSPollingWorker):
    """What the parse queue's messages mean. The draining is inherited.

    Anything that raises stays on the queue, becomes visible again when the
    visibility timeout expires, and eventually lands in the dead-letter
    queue — which is safe because parsing the same notification twice is a
    no-op.
    """

    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: ParseNotificationUseCase,
    ) -> None:
        super().__init__(client=client, queue_url=queue_url)
        self._use_case = use_case

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
