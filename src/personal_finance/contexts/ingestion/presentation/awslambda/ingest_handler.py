"""Lambda entry point for the ingest worker, driven by a schedule.

The only worker whose loop does not survive the move: there is no queue to
subscribe to, so the minute-by-minute `sleep` of
`presentation/cli/run_ingest_worker.py` becomes an EventBridge schedule and
this runs exactly one pass per tick. The reader already opens a fresh IMAP
connection per call and holds nothing between them, so a pass is complete on
its own — that is what makes the loop removable rather than merely relocated.
"""

from __future__ import annotations

import functools
import logging
from typing import TypedDict

from personal_finance.contexts.ingestion.application.ingest_handlers import (
    PollIngestMailboxUseCase,
)
from personal_finance.contexts.ingestion.presentation.cli.run_ingest_worker import (
    build_use_case,
)
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


_logger = logging.getLogger(__name__)


class PollSummary(TypedDict):
    fetched: int
    accepted: int
    duplicates: int
    unknown_recipient: int
    failed: int
    # The control-plane half of a poll. Reported here as well as in the log
    # because in a deployment this handler *is* the worker, and a forwarding
    # request that Google refused is a user stuck on a step with nothing in
    # the movement counters to show for it.
    confirmations: int
    refused_confirmations: int
    unclaimed_confirmations: int


@functools.lru_cache(maxsize=1)
def get_use_case() -> PollIngestMailboxUseCase:
    """Built once per execution environment, not once per tick.

    See `financial_handler.get_worker` for why this is a cache rather than a
    module-level build.
    """
    configure_logging()

    return build_use_case()


def handler(_event: object, _context: object) -> PollSummary:
    """Poll the mailbox once.

    Anything raised is left to fail the invocation, which is the opposite of
    the polling loop's choice and deliberate: there, swallowing a transient
    IMAP hiccup is what keeps a process meant to run for weeks alive. Here the
    process ends either way and the schedule brings the next one, so raising
    costs nothing and is the only thing that puts a broken mailbox on a
    metric. Swallowed, a mailbox that stopped answering would look exactly
    like a mailbox with no new mail.
    """
    result = get_use_case().execute()

    if result.fetched:
        _logger.info(
            "batch polled",
            extra={
                "fetched": result.fetched,
                "accepted": result.accepted,
                "duplicates": result.duplicates,
                "unknown_recipient": result.unknown_recipient,
                "failed": result.failed,
                "confirmations": result.confirmations,
                "refused_confirmations": result.refused_confirmations,
                "unclaimed_confirmations": result.unclaimed_confirmations,
            },
        )

    return {
        "fetched": result.fetched,
        "accepted": result.accepted,
        "duplicates": result.duplicates,
        "unknown_recipient": result.unknown_recipient,
        "failed": result.failed,
        "confirmations": result.confirmations,
        "refused_confirmations": result.refused_confirmations,
        "unclaimed_confirmations": result.unclaimed_confirmations,
    }
