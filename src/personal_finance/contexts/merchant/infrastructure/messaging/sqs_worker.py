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
from typing import TYPE_CHECKING
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.domain.normalization import (
    normalize_counterparty,
)
from personal_finance.contexts.merchant.domain.value_objects import CounterpartyKind
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


_logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_POLL = 10
WAIT_TIME_SECONDS = 20

INGESTION_SOURCE = "finflow.ingestion"
TRANSACTION_EXTRACTED = "TransactionExtracted"
SUPPORTED_VERSION = 1

# Epoch seconds this side of the year 10000. Unbounded, the conversion raises
# from deep inside `datetime` — `ValueError` for a year out of range, and
# `OverflowError` for anything past `time_t`, which is *not* a `ValueError` and
# escapes any guard written for one. So the bound is the protection and the
# guards below are only the second line. What arrives here came out of an
# email, which makes it untrusted by definition.
#
# Financial declares the same two constants for the same reason. Sharing them
# would mean one context importing another's boundary, which costs more than
# the duplication does.
MIN_OCCURRED_AT = 0
MAX_OCCURRED_AT = 253_402_300_799

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

    @field_validator("counterparty")
    @classmethod
    def _has_recognisable_text(cls, value: str) -> str:
        """Refuse here what the domain would refuse anyway.

        `min_length` counts spaces, so `"   "` passes it — and so do `"***"`
        and a name in a script `normalize_counterparty` does not keep, both of
        which normalize to nothing. All three reach `AliasFingerprint.from_raw`
        and raise there: inside the use case, *after* the event id is claimed,
        which spends the claim on work that never happened.

        Asking the normalizer rather than re-deriving the rule is the point —
        a boundary that guesses at what the domain accepts drifts from it.
        """
        if not normalize_counterparty(value):
            raise ValueError("counterparty has no recognisable text")

        return value

    occurred_at: int = Field(ge=MIN_OCCURRED_AT, le=MAX_OCCURRED_AT)


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

        try:
            command = detail.to_command()
        except (ValueError, OverflowError, OSError):
            # All three, not just `ValueError`: `datetime` raises `OverflowError`
            # past `time_t` and `OSError` on some platforms, and neither is a
            # `ValueError`. The bound above already refuses the timestamp that
            # reached here, so this is the second line for whatever field is
            # added next — and a second line written for one exception type is
            # not a second line at all.
            _logger.exception("discarding an unreadable sighting")

            # Malformed, not early. Nothing a later deploy does makes year
            # 33658 readable, so retrying it only delays the queue.
            return MessageOutcome.DISCARDED

        try:
            result = self._use_case.execute(command)
        except Exception:
            # One failing message must not take the batch with it: everything
            # after it in the same receive would go unprocessed, while the
            # messages before it are already deleted.
            #
            # It does *not* reach the dead-letter queue, and saying otherwise
            # would be wrong: `execute` claims the event id before it works, so
            # the redelivery is answered `DUPLICATE`, deleted, and no alarm
            # fires. What is lost is one sighting's counters, which the next
            # sighting of the same spelling recreates — the trade `ports.claim`
            # documents and accepts. Financial cannot make that trade and does
            # not: its identity comes from the movement's content.
            _logger.exception(
                "leaving a sighting that could not be resolved on the queue",
            )

            return MessageOutcome.RETRY

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
