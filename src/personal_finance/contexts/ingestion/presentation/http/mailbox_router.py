from __future__ import annotations

import functools
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxEvent,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
    HandleMailboxEventUseCase,
    SyncMailboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.mailbox.simulated import (
    SimulatedMailboxReader,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    DynamoDBMailboxConnectionRepository,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.contexts.ingestion.presentation.http.router import get_use_case
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingestion/mailbox-events", tags=["ingestion"])


class SimulatedMailboxEventPayload(BaseModel):
    """The simulated provider's notification.

    Shaped like the real ones in the way that matters: it names a mailbox and
    nothing else. No message, no sender, no content — a provider tells us that
    something changed and we decide what, if anything, we are allowed to read.
    """

    address: str = Field(min_length=3, max_length=320)


class MailboxEventResponse(BaseModel):
    fetched: int
    accepted: int
    duplicates: int


@functools.lru_cache(maxsize=1)
def _build_use_case() -> HandleMailboxEventUseCase:
    settings = get_ingestion_settings()
    client = get_dynamodb_client()
    connection_repository = DynamoDBMailboxConnectionRepository(
        client=client,
        table_name=settings.mailbox_connections_table,
    )

    return HandleMailboxEventUseCase(
        connection_repository=connection_repository,
        sync_use_case=SyncMailboxUseCase(
            # One reader per provider. Adding Gmail is adding an entry here.
            readers={
                MailboxProvider.SIMULATED: SimulatedMailboxReader(
                    client=client,
                    table_name=settings.simulated_mailbox_table,
                ),
            },
            inbox_repository=DynamoDBUserInboxRepository(
                client=client,
                table_name=settings.user_inboxes_table,
            ),
            connection_repository=connection_repository,
            receive_use_case=get_use_case(),
        ),
    )


def get_mailbox_event_use_case() -> HandleMailboxEventUseCase:
    return _build_use_case()


@router.post(
    "/simulated",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MailboxEventResponse,
)
def receive_simulated_mailbox_event(
    payload: SimulatedMailboxEventPayload,
    use_case: Annotated[
        HandleMailboxEventUseCase,
        Depends(get_mailbox_event_use_case),
    ],
) -> MailboxEventResponse:
    """The doorbell for the simulated provider.

    Each real provider gets its own route, because each speaks its own dialect
    — Gmail pushes a base64 Pub/Sub envelope, Graph opens with a validation
    handshake. Only the translation differs; all of them end here, in one
    `MailboxEvent`.
    """
    try:
        event = MailboxEvent(
            provider=MailboxProvider.SIMULATED,
            address=EmailAddress(payload.address),
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    result = use_case.execute(event)
    _logger.info(
        "mailbox event handled",
        extra={
            "provider": MailboxProvider.SIMULATED.value,
            "fetched": result.fetched,
            "accepted": result.accepted,
        },
    )

    # A mailbox we hold no connection for produces zeroes rather than a 404:
    # the notification came from a provider, not a user, and telling it which
    # mailboxes we track is not its business.
    return MailboxEventResponse(
        fetched=result.fetched,
        accepted=result.accepted,
        duplicates=result.duplicates,
    )


@router.post(
    "/{provider}",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    include_in_schema=False,
)
def receive_unsupported_mailbox_event(
    provider: Annotated[str, Path(max_length=32)],
) -> None:
    """Placeholder for the providers that have no adapter yet.

    Explicit so a misconfigured subscription fails loudly during setup rather
    than looking like a working endpoint that quietly drops every mail.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"No mailbox adapter for provider {provider!r} yet",
    )
