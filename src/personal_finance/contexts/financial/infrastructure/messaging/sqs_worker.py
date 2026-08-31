"""Drains Financial's integration-event queue.

A message is deleted only after the use case returns. Anything that raises
stays on the queue and eventually reaches the dead-letter queue, which is safe
because applying the same event twice is a no-op: the ledger's conditional
write refuses a row whose movement is already there, and the movement's
identity comes from its content rather than from the delivery.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from personal_finance.contexts.financial.application.handlers import (
    Outcome,
    RecordMovementUseCase,
    RecordTransferUseCase,
)
from personal_finance.contexts.financial.infrastructure.messaging.inbound import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
    TRANSFER_EXTRACTED,
    IntegrationEventEnvelope,
    TransactionExtractedDetail,
    TransferExtractedDetail,
    UnsupportedPayloadVersionError,
)


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


_logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_POLL = 10
WAIT_TIME_SECONDS = 20


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


class SQSFinancialWorker:
    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: RecordMovementUseCase,
        transfer_use_case: RecordTransferUseCase,
    ) -> None:
        self._client = client
        self._queue_url = queue_url
        self._use_case = use_case
        self._transfer_use_case = transfer_use_case

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

        if envelope.source != INGESTION_SOURCE or envelope.detail_type not in {
            TRANSACTION_EXTRACTED,
            TRANSFER_EXTRACTED,
        }:
            # This queue belongs to Financial alone, so an event addressed to
            # somebody else is a misrouted rule, not a message another
            # subscriber is still waiting for.
            _logger.warning(
                "discarding an event this worker does not subscribe to",
                extra={"detail_type": envelope.detail_type, "source": envelope.source},
            )

            return MessageOutcome.DISCARDED

        if envelope.detail_type == TRANSFER_EXTRACTED:
            return self._handle_transfer(envelope)

        try:
            detail = TransactionExtractedDetail.model_validate(envelope.detail)
        except ValidationError:
            _logger.exception("discarding malformed TransactionExtracted payload")

            return MessageOutcome.DISCARDED

        try:
            command = detail.to_command()
        except UnsupportedPayloadVersionError:
            # Left on the queue on purpose: a version we do not understand was
            # written by a newer deploy, and a newer worker may still pick it
            # up. If none does, redrive moves it to the DLQ.
            _logger.warning(
                "unsupported TransactionExtracted version",
                extra={"version": detail.version},
            )

            return MessageOutcome.RETRY
        except ValueError:
            # A direction or currency this cannot read. Left on the queue for
            # the same reason an unknown version is: `Currency` knows two
            # members today, and an alert in a third would be readable by the
            # very next deploy. Deleting it would destroy a real movement to
            # save a redelivery, and the dead-letter queue is where something
            # genuinely unreadable belongs — somewhere a person can see it.
            _logger.exception("leaving an unreadable movement on the queue")

            return MessageOutcome.RETRY

        try:
            result = self._use_case.execute(command)
        except Exception:
            # One unreadable stored row, one throttled write, must not stop
            # the loop for every other message. Left on the queue, so it is
            # retried and eventually dead-lettered rather than lost.
            _logger.exception(
                "leaving a movement that could not be recorded on the queue",
            )

            return MessageOutcome.RETRY

        _logger.info(
            "movement recorded",
            # By id and outcome only. The payload is a line of somebody's
            # spending history — amount, counterparty, bank and card digits in
            # one record — and an audit trail needs neither.
            extra={
                "movement_id": result.transaction.id.value,
                "outcome": result.outcome.value,
                "account_id": (
                    str(result.account.id.value) if result.account is not None else None
                ),
            },
        )

        if result.outcome is Outcome.DUPLICATE:
            _logger.info(
                "movement already in the ledger",
                extra={"movement_id": result.transaction.id.value},
            )

        return MessageOutcome.HANDLED

    def _handle_transfer(self, envelope: IntegrationEventEnvelope) -> MessageOutcome:
        """Both sides of one email, written together.

        Every failure mode is answered the same way the single-movement path
        answers it, and for the same reasons: a shape this cannot read is
        discarded because it will never become readable, while a version or a
        currency it does not know yet is left on the queue for a newer deploy.

        A transfer that raises mid-way has written at most one of its two
        sides. Retrying is what completes it: each side's identity comes from
        its own content, so the side already stored is refused by its own
        conditional write instead of being applied twice.
        """
        try:
            detail = TransferExtractedDetail.model_validate(envelope.detail)
        except ValidationError:
            _logger.exception("discarding malformed TransferExtracted payload")

            return MessageOutcome.DISCARDED

        try:
            command = detail.to_command()
        except UnsupportedPayloadVersionError:
            _logger.warning(
                "unsupported TransferExtracted version",
                extra={"version": detail.version},
            )

            return MessageOutcome.RETRY
        except ValueError:
            _logger.exception("leaving an unreadable transfer on the queue")

            return MessageOutcome.RETRY

        try:
            result = self._transfer_use_case.execute(command)
        except ValueError:
            # The domain refused the pair itself — one instrument paying
            # itself, an empty bank. Retrying cannot change a payload, and
            # only one side of it could ever be recorded, so it is discarded
            # rather than left to redeliver forever.
            _logger.exception("discarding a transfer the domain refused")

            return MessageOutcome.DISCARDED
        except Exception:
            _logger.exception(
                "leaving a transfer that could not be recorded on the queue",
            )

            return MessageOutcome.RETRY

        _logger.info(
            "transfer recorded",
            extra={
                "movement_id": result.source.transaction.id.value,
                "counterpart_movement_id": result.destination.transaction.id.value,
                "outcome": result.outcome.value,
                "source_outcome": result.source.outcome.value,
                "destination_outcome": result.destination.outcome.value,
            },
        )

        return MessageOutcome.HANDLED

    def _delete(self, receipt: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt,
        )
