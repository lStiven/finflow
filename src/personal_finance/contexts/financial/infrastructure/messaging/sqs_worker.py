"""What Financial's integration-event queue carries, message by message.

The draining itself is `shared.infrastructure.messaging.sqs_polling`. A
message is deleted only after the use case returns; anything that raises stays
on the queue and eventually reaches the dead-letter queue, which is safe
because applying the same event twice is a no-op: the ledger's conditional
write refuses a row whose movement is already there, and the movement's
identity comes from its content rather than from the delivery.
"""

from __future__ import annotations

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
from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
    SQSPollingWorker,
)


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


_logger = logging.getLogger(__name__)


class SQSFinancialWorker(SQSPollingWorker):
    """What Financial's integration-event queue carries. Draining is inherited."""

    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: RecordMovementUseCase,
        transfer_use_case: RecordTransferUseCase,
    ) -> None:
        super().__init__(client=client, queue_url=queue_url)
        self._use_case = use_case
        self._transfer_use_case = transfer_use_case

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
