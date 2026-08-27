"""Polls the ingest mailbox until interrupted.

    just ingest-worker

Unlike the SQS-driven parse worker, there is nothing to long-poll here — IMAP
has no equivalent — so this sleeps between passes instead.
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.ingest_handlers import (
    PollIngestMailboxUseCase,
)
from personal_finance.contexts.ingestion.infrastructure.events import (
    build_ingestion_event_publisher,
)
from personal_finance.contexts.ingestion.infrastructure.ingest.forwarding_confirmer import (  # noqa: E501
    HttpForwardingConfirmer,
)
from personal_finance.contexts.ingestion.infrastructure.ingest.imap_reader import (
    ImapIngestMailboxReader,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    SQSQueuePublisher,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
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


_logger = logging.getLogger(__name__)


class _Stopper:
    """Finishes the poll in flight before exiting, so no message is lost to a
    deploy or a Ctrl-C.
    """

    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        del frame
        _logger.info("stop requested", extra={"signal": signum})
        self.requested = True


def build_use_case() -> PollIngestMailboxUseCase:
    settings = get_ingestion_settings()

    if not settings.parse_queue_url:
        raise ValueError(
            "INGESTION_PARSE_QUEUE_URL is not set: there is no queue to hand "
            "accepted mail to. Run `just aws-provision` and copy the URL it "
            "prints.",
        )

    if not settings.ingest_mailbox_configured:
        raise ValueError(
            "INGESTION_INGEST_MAILBOX_ADDRESS / "
            "INGESTION_INGEST_MAILBOX_APP_PASSWORD are not set: there is no "
            "mailbox to poll. See docs/email-forwarding.md.",
        )

    inbox_repository = DynamoDBUserInboxRepository(
        client=get_dynamodb_client(),
        table_name=settings.user_inboxes_table,
    )

    return PollIngestMailboxUseCase(
        forwarding_confirmer=HttpForwardingConfirmer(),
        reader=ImapIngestMailboxReader(
            host=settings.ingest_mailbox_host,
            port=settings.ingest_mailbox_port,
            address=settings.ingest_mailbox_address,
            app_password=settings.ingest_mailbox_app_password.get_secret_value(),
        ),
        receive_use_case=ReceiveBankNotificationUseCase(
            repository=DynamoDBBankNotificationRepository(
                client=get_dynamodb_client(),
                table_name=settings.notifications_table,
                retention_days=settings.retention_days,
            ),
            inbox_repository=inbox_repository,
            queue_publisher=SQSQueuePublisher(
                client=get_sqs_client(),
                queue_url=settings.parse_queue_url,
            ),
            event_publisher=build_ingestion_event_publisher(),
        ),
    )


def main() -> None:
    configure_logging()
    use_case = build_use_case()
    interval = get_ingestion_settings().ingest_poll_interval_seconds
    stopper = _Stopper()
    signal.signal(signal.SIGINT, stopper)
    signal.signal(signal.SIGTERM, stopper)

    _logger.info("ingest worker started", extra={"poll_interval_seconds": interval})

    while not stopper.requested:
        try:
            result = use_case.execute()
        except Exception:
            # A transient IMAP hiccup must not kill a worker meant to run for
            # weeks; log it and try again next pass.
            _logger.exception("ingest poll failed")
        else:
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
                    },
                )

        # Woken often enough to notice a stop request promptly, rather than
        # sleeping through the whole interval.
        for _ in range(interval):
            if stopper.requested:
                break

            time.sleep(1)

    _logger.info("ingest worker stopped")


if __name__ == "__main__":
    main()
