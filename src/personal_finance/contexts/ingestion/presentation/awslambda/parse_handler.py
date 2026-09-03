"""Lambda entry point for the parse worker.

The sibling of `presentation/cli/run_parse_worker.py`: same wiring, same
`handle` per message, different thing driving it.
"""

from __future__ import annotations

import functools

from personal_finance.contexts.ingestion.infrastructure.messaging.sqs_worker import (
    SQSParseWorker,
)
from personal_finance.contexts.ingestion.presentation.cli.run_parse_worker import (
    build_worker,
)
from personal_finance.shared.infrastructure.messaging.lambda_batch import (
    BatchResponse,
    SQSEvent,
    drain,
)
from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
)
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


@functools.lru_cache(maxsize=1)
def get_worker() -> SQSParseWorker:
    """Built once per execution environment, not once per message.

    See `financial_handler.get_worker` for why this is a cache rather than a
    module-level build.
    """
    configure_logging()

    return build_worker()


def handler(event: SQSEvent, _context: object) -> BatchResponse:
    worker = get_worker()

    return drain(
        event,
        must_retry=lambda body: worker.handle(body) is MessageOutcome.RETRY,
    )
