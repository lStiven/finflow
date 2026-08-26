"""Turning one SQS batch invocation into Lambda's partial-failure response.

Lambda deletes every message in the batch that the response does not name, so
the only thing this has to get right is which ones must come back. That
inverts the polling loop's rule — there a message is deleted explicitly and
staying on the queue is the default — and getting it backwards silently
destroys work, which is why it lives in one place rather than three.

Shared because it is the SQS-to-Lambda protocol and nothing else: what a
message *means* stays in the context that owns the queue. This only ever sees
a body and a yes/no.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TypedDict


_logger = logging.getLogger(__name__)


# camelCase on purpose: these are AWS's field names on the wire, not ours, and
# renaming them would mean translating every record before reading it.
class SQSRecord(TypedDict):
    messageId: str
    body: str


class SQSEvent(TypedDict):
    Records: list[SQSRecord]


class BatchItemFailure(TypedDict):
    itemIdentifier: str


class BatchResponse(TypedDict):
    batchItemFailures: list[BatchItemFailure]


def drain(event: SQSEvent, *, must_retry: Callable[[str], bool]) -> BatchResponse:
    """Run every record through `must_retry`, reporting the ones to redeliver.

    `must_retry` receives a message body and answers whether it has to come
    back. Anything it raises is treated as a yes: an unexpected failure is
    exactly the case where the message must survive, and letting the exception
    escape would fail the *whole* batch — including the messages already
    handled beside it, which would then be redelivered too.
    """
    failures: list[BatchItemFailure] = []

    for record in event["Records"]:
        message_id = record["messageId"]

        try:
            retry = must_retry(record["body"])
        except Exception:
            _logger.exception(
                "leaving a message on the queue after an unexpected failure",
                extra={"message_id": message_id},
            )
            retry = True

        if retry:
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
