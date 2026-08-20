from __future__ import annotations

from datetime import datetime
import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.contexts.ingestion.infrastructure.messaging.sqs import (
    SQSQueuePublisher,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBBankNotificationRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


class BankNotificationWebhookPayload(BaseModel):
    """Inbound webhook body. Every field is untrusted input: the shape is
    checked here, and the domain value objects enforce the actual rules.
    """

    message_id: str = Field(min_length=1, max_length=998)
    sender: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=2_000)
    raw_content: str = Field(min_length=1, max_length=256_000)
    received_at: datetime | None = None


class BankNotificationWebhookResponse(BaseModel):
    notification_id: str
    status: str
    is_duplicate: bool


@functools.lru_cache(maxsize=1)
def _build_use_case() -> ReceiveBankNotificationUseCase:
    settings = get_ingestion_settings()

    return ReceiveBankNotificationUseCase(
        repository=DynamoDBBankNotificationRepository(
            client=get_dynamodb_client(),
            table_name=settings.notifications_table,
            retention_days=settings.retention_days,
        ),
        sender_policy=AuthorizedSenderPolicy(
            allowed_addresses=frozenset(
                EmailAddress(address)
                for address in settings.authorized_sender_addresses
            ),
            allowed_domains=settings.authorized_sender_domains,
        ),
        queue_publisher=SQSQueuePublisher(
            client=get_sqs_client(),
            queue_url=settings.parse_queue_url,
        ),
        event_publisher=LoggingEventPublisher(),
    )


def get_use_case() -> ReceiveBankNotificationUseCase:
    return _build_use_case()


@router.post(
    "/bank-notifications",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BankNotificationWebhookResponse,
)
def receive_bank_notification(
    payload: BankNotificationWebhookPayload,
    use_case: Annotated[ReceiveBankNotificationUseCase, Depends(get_use_case)],
) -> BankNotificationWebhookResponse:
    try:
        command = ReceiveBankNotificationCommand(
            message_id=EmailMessageId(payload.message_id),
            sender=EmailAddress(payload.sender),
            subject=payload.subject,
            raw_content=payload.raw_content,
            received_at=(
                PosixTime.from_datetime(payload.received_at)
                if payload.received_at is not None
                else PosixTime.now()
            ),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    result = use_case.execute(command)

    return BankNotificationWebhookResponse(
        notification_id=str(result.notification_id.value),
        status=result.status.value,
        is_duplicate=result.is_duplicate,
    )
