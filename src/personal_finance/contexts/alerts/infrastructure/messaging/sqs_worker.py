"""What Alerts' integration-event queue carries, message by message.

The draining itself is `shared.infrastructure.messaging.sqs_polling`. What is
decided here is only which failures are worth a redelivery:

* a shape that will never become readable is discarded — retrying it until a
  dead-letter queue takes it only delays the queue;
* a version or a currency this deploy does not know yet is left on the queue,
  because the very next deploy may read it and deleting it would lose a
  purchase nobody was ever told about;
* a transport that is down is left on the queue, for the same reason;
* a destination that refused *permanently* — blocked, kicked — is not a
  failure of this message, and the use case has already moved past it.

Applying the same message twice is quiet: the delivery log is asked before
each send. It is written *after*, so a crash in between repeats one message
rather than silently swallowing it. That trade is the opposite of merchant's
and is explained on the `DeliveryLog` port.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from personal_finance.contexts.alerts.application.ports import (
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.infrastructure.messaging.inbound import (
    FINANCIAL_SOURCE,
    MOVEMENT_RECORDED,
    IntegrationEventEnvelope,
    MovementRecordedDetail,
    UnsupportedPayloadVersionError,
    refused_fields,
)
from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
    SQSPollingWorker,
)


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient

    from personal_finance.contexts.alerts.application.handlers import (
        DeliverMovementAlertUseCase,
    )


_logger = logging.getLogger(__name__)


class SQSAlertsWorker(SQSPollingWorker):
    """What Alerts' integration-event queue carries. Draining is inherited."""

    def __init__(
        self,
        *,
        client: SQSClient,
        queue_url: str,
        use_case: DeliverMovementAlertUseCase,
    ) -> None:
        super().__init__(client=client, queue_url=queue_url)
        self._use_case = use_case

    def handle(self, body: str) -> MessageOutcome:
        try:
            envelope = IntegrationEventEnvelope.model_validate_json(body)
        except ValidationError as error:
            # The field names, never the exception. A Pydantic error renders
            # `input_value=` for every field it refused, and those fields are
            # an amount, a counterparty and a bank — the same spending history
            # the success path forty lines below deliberately keeps out of the
            # log. `refused_fields` is what keeps the two apart.
            _logger.warning(
                "discarding malformed integration event",
                extra={"refused": refused_fields(error)},
            )

            return MessageOutcome.DISCARDED

        if (
            envelope.source != FINANCIAL_SOURCE
            or envelope.detail_type != MOVEMENT_RECORDED
        ):
            # This queue belongs to Alerts alone, so an event addressed to
            # somebody else is a misrouted rule, not a message another
            # subscriber is still waiting for.
            _logger.warning(
                "discarding an event this worker does not subscribe to",
                extra={"detail_type": envelope.detail_type, "source": envelope.source},
            )

            return MessageOutcome.DISCARDED

        try:
            detail = MovementRecordedDetail.model_validate(envelope.detail)
        except ValidationError as error:
            # Named, for the reason above and because a producer that changed
            # shape is otherwise invisible: this branch is a silent discard,
            # and without the field nobody can tell one refusal from another.
            _logger.warning(
                "discarding malformed MovementRecorded payload",
                extra={"refused": refused_fields(error)},
            )

            return MessageOutcome.DISCARDED

        try:
            command = detail.to_command()
        except UnsupportedPayloadVersionError:
            _logger.warning(
                "unsupported MovementRecorded version",
                extra={"version": detail.version},
            )

            return MessageOutcome.RETRY
        except ValueError as error:
            # A direction, currency or origin this cannot read. Left on the
            # queue: `Currency` knows two members today, and an alert in a
            # third would be readable by the very next deploy.
            #
            # The message, not the traceback: these three raise with the one
            # unreadable token in them, which is the whole of what a reader
            # needs and none of the amounts around it.
            _logger.warning(
                "leaving an unreadable movement on the queue",
                extra={"reason": str(error)},
            )

            return MessageOutcome.RETRY

        try:
            delivered = self._use_case.execute(command)
        except TransportUnavailableError:
            # Telegram is down or throttling. The purchase is real and its
            # owner still has not been told, so this waits rather than dies.
            _logger.warning("telegram unavailable; leaving the alert on the queue")

            return MessageOutcome.RETRY
        except Exception:
            _logger.exception("leaving an alert that could not be sent on the queue")

            return MessageOutcome.RETRY

        _logger.info(
            "movement announced",
            # A count, and nothing else. The payload is a line of somebody's
            # spending history — amount, counterparty and bank in one record
            # — and an audit trail needs none of it.
            extra={"event_id": str(command.event_id), "delivered": delivered},
        )

        return MessageOutcome.HANDLED
