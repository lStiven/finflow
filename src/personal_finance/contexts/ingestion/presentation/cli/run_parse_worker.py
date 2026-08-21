"""Runs the parsing worker until interrupted.

just parse-worker
"""

from __future__ import annotations

import logging
import signal
from types import FrameType

from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
)
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.infrastructure.events import (
    build_ingestion_event_publisher,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs_worker import (
    SQSParseWorker,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


_logger = logging.getLogger(__name__)


class _Stopper:
    """Finishes the batch in flight before exiting, so no message is lost to a
    deploy or a Ctrl-C.
    """

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        del frame
        _logger.info("stop requested", extra={"signal": signum})
        self.requested = True


def build_worker() -> SQSParseWorker:
    settings = get_ingestion_settings()

    if not settings.parse_queue_url:
        raise ValueError(
            "INGESTION_PARSE_QUEUE_URL is not set: there is no queue to drain. "
            "Run `just aws-provision` and copy the URL it prints.",
        )

    return SQSParseWorker(
        client=get_sqs_client(),
        queue_url=settings.parse_queue_url,
        use_case=ParseNotificationUseCase(
            repository=DynamoDBBankNotificationRepository(
                client=get_dynamodb_client(),
                table_name=settings.notifications_table,
                retention_days=settings.retention_days,
            ),
            registry=ParserRegistry(),
            event_publisher=build_ingestion_event_publisher(),
        ),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = build_worker()
    stopper = _Stopper()
    signal.signal(signal.SIGINT, stopper)
    signal.signal(signal.SIGTERM, stopper)

    _logger.info("parse worker started")

    while not stopper.requested:
        result = worker.poll_once()

        if result.received:
            _logger.info(
                "batch drained",
                extra={"handled": result.handled, "rejected": result.rejected},
            )

    _logger.info("parse worker stopped")


if __name__ == "__main__":
    main()
