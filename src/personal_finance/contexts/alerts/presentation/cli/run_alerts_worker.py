"""Runs the alerts worker until interrupted.

just alerts-worker
"""

from __future__ import annotations

from personal_finance.contexts.alerts.application.handlers import (
    DeliverMovementAlertUseCase,
)
from personal_finance.contexts.alerts.infrastructure.messaging.sqs_worker import (
    SQSAlertsWorker,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    DynamoDBAlertChannelRepository,
    DynamoDBDeliveryLog,
)
from personal_finance.contexts.alerts.presentation.http.dependencies import (
    build_message_sender,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import get_alerts_settings
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)
from personal_finance.shared.presentation.worker_loop import drain_until_stopped


def build_worker() -> SQSAlertsWorker:
    settings = get_alerts_settings()

    if not settings.events_queue_url:
        raise ValueError(
            "ALERTS_EVENTS_QUEUE_URL is not set: there is no queue to drain, "
            "and every movement would go unannounced. Run `just aws-provision` "
            "and copy the URL it prints.",
        )

    dynamodb = get_dynamodb_client()
    table_name = settings.channels_table

    return SQSAlertsWorker(
        client=get_sqs_client(),
        queue_url=settings.events_queue_url,
        use_case=DeliverMovementAlertUseCase(
            channels=DynamoDBAlertChannelRepository(
                client=dynamodb,
                table_name=table_name,
            ),
            deliveries=DynamoDBDeliveryLog(client=dynamodb, table_name=table_name),
            # The same builder the webhook uses, so a message sent by the
            # worker and the hello sent at linking go out the same way.
            sender=build_message_sender(),
        ),
    )


def main() -> None:
    configure_logging()
    drain_until_stopped(build_worker(), name="alerts")


if __name__ == "__main__":
    main()
