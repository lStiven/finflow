"""Running a worker until somebody stops it.

The other half of what used to be copied per context: a signal handler that
lets the batch in flight finish, and the loop around `poll_once`. Deploys send
SIGTERM and a person sends SIGINT; both have to mean "stop after this batch",
never "stop now", because a message received and not yet deleted would come
back — which is safe — while one deleted and not yet finished would not.

The ingest worker shares the stopper and not the loop: it polls a mailbox on a
timer rather than long-polling a queue, so its loop is genuinely a different
one.
"""

from __future__ import annotations

import logging
import signal
from types import FrameType
from typing import Self

from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    SQSPollingWorker,
)


_logger = logging.getLogger(__name__)


class StopSignal:
    """Finishes the work in flight before exiting, so nothing is lost to a
    deploy or a Ctrl-C.
    """

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        del frame
        _logger.info("stop requested", extra={"signal": signum})
        self.requested = True

    def install(self) -> Self:
        signal.signal(signal.SIGINT, self)
        signal.signal(signal.SIGTERM, self)

        return self


def drain_until_stopped(worker: SQSPollingWorker, *, name: str) -> None:
    """Long-poll the queue until SIGINT or SIGTERM asks for the exit."""
    stopper = StopSignal().install()
    _logger.info("worker started", extra={"worker": name})

    while not stopper.requested:
        result = worker.poll_once()

        if result.received:
            _logger.info(
                "batch drained",
                extra={
                    "worker": name,
                    "handled": result.handled,
                    "rejected": result.rejected,
                },
            )

    _logger.info("worker stopped", extra={"worker": name})
