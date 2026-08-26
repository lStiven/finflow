"""Draining the integration events merchant subscribes to.

EventBridge delivers to this context's own queue, wrapping the published event
in its envelope: `detail-type`, `source`, and the payload under `detail`.

Nothing here imports from ingestion. What arrives is a JSON contract that
crossed a bus, and it is validated as untrusted input — including the
transaction kind, which is read as a string and mapped into this context's own
vocabulary rather than borrowing another context's enum.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import uuid

from mypy_boto3_sqs.client import SQSClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.domain.value_objects import CounterpartyKind
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_POLL = 10
WAIT_TIME_SECONDS = 20

INGESTION_SOURCE = "finflow.ingestion"
TRANSACTION_EXTRACTED = "TransactionExtracted"
SUPPORTED_VERSION = 1

# Which kinds name a business. Everything else — a transfer, an incoming
# payment — may well name a person, and people must never be grouped by a
# shared first name, so they resolve without the sub-brand guess.
_BUSINESS_KINDS = frozenset({"card_purchase", "qr_payment"})


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


class ExtractedTransactionBody(BaseModel):
    kind: str = Field(default="", max_length=64)
    counterparty: str = Field(min_length=1, max_length=512)
    occurred_at: int


class TransactionExtractedDetail(BaseModel):
    version: int = Field(ge=1)
    event_id: uuid.UUID
    user_id: uuid.UUID
    transaction: ExtractedTransactionBody

    def to_command(self) -> RecordSightingCommand:
        return RecordSightingCommand(
            user_id=UserId(value=self.user_id),
            counterparty=self.transaction.counterparty,
            occurred_at=PosixTime.from_epoch_seconds(self.transaction.occurred_at),
            event_id=self.event_id,
            kind=(
                CounterpartyKind.BUSINESS
                if self.transaction.kind in _BUSINESS_KINDS
                else CounterpartyKind.UNKNOWN
            ),
        )


class IntegrationEventEnvelope(BaseModel):
    """What EventBridge puts on the queue. Only the routing fields are read
    here; the payload is validated separately, once we know what it is.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(default="", max_length=256)
    detail_type: str = Field(default="", alias="detail-type", max_length=256)
    detail: dict[str, object] = Field(default_factory=lambda: dict[str, object]())


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PollResult:
    received: int = 0
    handled: int = 0
    rejected: int = 0


class SQSMerchantWorker:
    """Drains merchant's integration-event queue, one batch at a time.

    A message is deleted only after the use case returns. Anything that raises
    stays on the queue and eventually reaches the dead-letter queue, which is
    safe because applying the same event twice is a no-op: the use case claims
    the event id before it does any work.
    """

    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: ResolveMerchantUseCase,
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
            envelope = IntegrationEventEnvelope.model_validate_json(body)
        except ValidationError:
            # Unparseable payloads never become parseable. Retrying one until
            # the DLQ takes it only delays the queue.
            _logger.exception("discarding malformed integration event")

            return MessageOutcome.DISCARDED

        if (
            envelope.source != INGESTION_SOURCE
            or envelope.detail_type != TRANSACTION_EXTRACTED
        ):
            # This queue belongs to merchant alone, so an event addressed to
            # somebody else is a misrouted rule, not a message another
            # subscriber is still waiting for.
            _logger.warning(
                "discarding an event this worker does not subscribe to",
                extra={"detail_type": envelope.detail_type, "source": envelope.source},
            )

            return MessageOutcome.DISCARDED

        try:
            detail = TransactionExtractedDetail.model_validate(envelope.detail)
        except ValidationError:
            _logger.exception("discarding malformed TransactionExtracted payload")

            return MessageOutcome.DISCARDED

        if detail.version != SUPPORTED_VERSION:
            # Left on the queue on purpose: a version we do not understand was
            # written by a newer deploy, and a newer worker may still pick it
            # up. If none does, redrive moves it to the DLQ.
            _logger.warning(
                "unsupported TransactionExtracted version",
                extra={"version": detail.version},
            )

            return MessageOutcome.RETRY

        result = self._use_case.execute(detail.to_command())
        _logger.info(
            "counterparty resolved",
            extra={
                "resolution": result.resolution.value,
                "merchant_id": (
                    str(result.merchant.id.value) if result.merchant else None
                ),
            },
        )

        return MessageOutcome.HANDLED

    def _delete(self, receipt_handle: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
