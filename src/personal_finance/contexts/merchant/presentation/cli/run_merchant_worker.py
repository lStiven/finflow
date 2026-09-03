"""Runs the merchant worker until interrupted.

just merchant-worker
"""

from __future__ import annotations

import logging

from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.infrastructure.events import (
    build_merchant_event_publisher,
)
from personal_finance.contexts.merchant.infrastructure.llm.merchant_advisor import (
    build_merchant_advisor,
)
from personal_finance.contexts.merchant.infrastructure.messaging.sqs_worker import (
    SQSMerchantWorker,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    DynamoDBMerchantRepository,
    DynamoDBProcessedEventStore,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import get_merchant_settings
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)
from personal_finance.shared.presentation.worker_loop import drain_until_stopped


_logger = logging.getLogger(__name__)


def build_worker() -> SQSMerchantWorker:
    settings = get_merchant_settings()

    if not settings.events_queue_url:
        raise ValueError(
            "MERCHANT_EVENTS_QUEUE_URL is not set: there is no queue to drain, "
            "and every extracted transaction would go unattributed. Run "
            "`just aws-provision` and copy the URL it prints.",
        )

    table_name = settings.merchants_table
    advisor = build_merchant_advisor()

    if advisor is None:
        _logger.warning(
            "no model configured: merchants will be grouped and categorized by "
            "the deterministic rules alone. Set LLM_API_KEY to turn the "
            "advisor on.",
        )

    return SQSMerchantWorker(
        client=get_sqs_client(),
        queue_url=settings.events_queue_url,
        use_case=ResolveMerchantUseCase(
            repository=DynamoDBMerchantRepository(
                client=get_dynamodb_client(),
                table_name=table_name,
            ),
            processed_events=DynamoDBProcessedEventStore(
                client=get_dynamodb_client(),
                table_name=table_name,
            ),
            event_publisher=build_merchant_event_publisher(),
            advisor=advisor,
        ),
    )


def main() -> None:
    configure_logging()
    drain_until_stopped(build_worker(), name="merchant")


if __name__ == "__main__":
    main()
