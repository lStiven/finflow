"""Lambda entry point for the financial worker.

The sibling of `presentation/cli/run_financial_worker.py`: same wiring, same
`handle` per message, different thing driving it. There the process owns the
loop and deletes what it finished; here Lambda owns the loop and deletes
everything the response does not name.
"""

from __future__ import annotations

import functools

from personal_finance.contexts.financial.infrastructure.messaging.sqs_worker import (
    SQSFinancialWorker,
)
from personal_finance.contexts.financial.presentation.cli.run_financial_worker import (
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
def get_worker() -> SQSFinancialWorker:
    """Built once per execution environment, not once per message.

    Lambda keeps the process alive between invocations, so this is the same
    reuse `get_dynamodb_client()` already relies on — without it every message
    would rebuild the client and the whole use-case graph.

    Cached rather than built at import: a module that opens AWS clients on
    import cannot be imported to test it, which is the reason `create_app` is
    a factory too. `cache_clear()` is what a test calls between cases.
    """
    configure_logging()

    return build_worker()


def handler(event: SQSEvent, _context: object) -> BatchResponse:
    worker = get_worker()

    return drain(
        event,
        must_retry=lambda body: worker.handle(body) is MessageOutcome.RETRY,
    )
