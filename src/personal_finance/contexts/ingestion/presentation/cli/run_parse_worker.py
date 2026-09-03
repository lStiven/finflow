"""Runs the parsing worker until interrupted.

just parse-worker
"""

from __future__ import annotations

import logging

from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
)
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.infrastructure.events import (
    build_ingestion_event_publisher,
)
from personal_finance.contexts.ingestion.infrastructure.llm.transaction_extractor import (  # noqa: E501
    build_transaction_extractor,
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
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)
from personal_finance.shared.presentation.worker_loop import drain_until_stopped


_logger = logging.getLogger(__name__)


def build_worker() -> SQSParseWorker:
    settings = get_ingestion_settings()

    if not settings.parse_queue_url:
        raise ValueError(
            "INGESTION_PARSE_QUEUE_URL is not set: there is no queue to drain. "
            "Run `just aws-provision` and copy the URL it prints.",
        )

    extractor = build_transaction_extractor()

    if extractor is None:
        _logger.warning(
            "no model configured: alerts no template matches will be kept "
            "unparsed. Set LLM_API_KEY to turn the fallback on.",
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
            fallback_extractor=extractor,
        ),
    )


def main() -> None:
    configure_logging()
    drain_until_stopped(build_worker(), name="parse")


if __name__ == "__main__":
    main()
