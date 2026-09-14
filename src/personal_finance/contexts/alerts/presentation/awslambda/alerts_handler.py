"""Lambda entry point for the alerts worker.

The sibling of `presentation/cli/run_alerts_worker.py`: same wiring, same
`handle` per message, different thing driving it. There the process owns the
loop and deletes what it finished; here Lambda owns the loop and deletes
everything the response does not name.
"""

from __future__ import annotations

import functools

from personal_finance.contexts.alerts.infrastructure.messaging.sqs_worker import (
    SQSAlertsWorker,
)
from personal_finance.contexts.alerts.presentation.cli.run_alerts_worker import (
    build_worker,
)
from personal_finance.shared.infrastructure.messaging.lambda_batch import (
    BatchResponse,
    SQSEvent,
    drain,
)
from personal_finance.shared.infrastructure.messaging.sqs_polling import MessageOutcome
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


@functools.lru_cache(maxsize=1)
def get_worker() -> SQSAlertsWorker:
    """Built once per execution environment, not once per message.

    Cached rather than built at import: a module that opens AWS clients on
    import cannot be imported to test it. `cache_clear()` is what a test
    calls between cases.
    """
    configure_logging()

    return build_worker()


def handler(event: SQSEvent, _context: object) -> BatchResponse:
    worker = get_worker()

    return drain(
        event,
        must_retry=lambda body: worker.handle(body) is MessageOutcome.RETRY,
    )
