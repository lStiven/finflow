"""Draining an SQS queue, for the three workers that each drain one.

Transport and nothing else, the sibling of `lambda_batch.drain`: that one
answers "which of these does Lambda have to redeliver?", this one answers
"which of these may I delete?", and both ask the owning context the same
per-message question. What a message *means* never comes here — each context
implements `handle` over its own contract and keeps its own vocabulary.

It used to be three copies, one per context, and they had already drifted.
Two of them caught whatever the use case raised and left the message on the
queue; the third let it out of `handle`, where it also escaped `poll_once` and
stopped the loop mid-batch — deleting the messages ahead of it, abandoning the
ones behind it, and killing a process meant to run for weeks. The guard is
here now, once, and it gives the answer `drain` already gave.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


_logger = logging.getLogger(__name__)

MAX_MESSAGES_PER_POLL = 10
WAIT_TIME_SECONDS = 20


class MessageOutcome(enum.Enum):
    """What a single message left behind, whoever delivered it.

    Public because the polling loop is not the only caller: the Lambda entry
    points read the same three answers and turn them into the batch response
    AWS expects.
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


class SQSPollingWorker(abc.ABC):
    """Receives a batch, asks `handle` about each message, deletes what it may.

    A message is deleted only after `handle` returns something other than
    `RETRY`. Anything left stays on the queue, becomes visible again when the
    visibility timeout expires, and eventually reaches the dead-letter queue —
    which is only safe because every subscriber here is idempotent, each in
    its own way.
    """

    def __init__(self, *, client: SQSClient, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    @abc.abstractmethod
    def handle(self, body: str) -> MessageOutcome:
        """Read one message body and say what became of it.

        The context's own contract lives here: the envelope it accepts, the
        payload versions it understands, and which failures are worth
        redelivering. Nothing about SQS belongs in it — the same method
        answers for a message Lambda delivered.
        """

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

            try:
                outcome = self.handle(message.get("Body", ""))
            except Exception:
                # One failing message must not take the batch with it, and
                # must not stop a long-running process either. Left on the
                # queue, which is where something nobody could handle belongs:
                # it is retried, and eventually dead-lettered where a person
                # can see it. `drain` treats an escaping exception the same
                # way, for the same reason.
                _logger.exception(
                    "leaving a message on the queue after an unexpected failure",
                )
                outcome = MessageOutcome.RETRY

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

    def _delete(self, receipt_handle: str) -> None:
        self._client.delete_message(
            QueueUrl=self._queue_url,
            ReceiptHandle=receipt_handle,
        )
