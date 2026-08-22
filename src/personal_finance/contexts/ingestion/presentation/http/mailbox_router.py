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
from personal_finance.contexts.ingestion.application.subscription_handlers import (
    KeepSubscriptionAliveUseCase,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
    HandleMailboxEventUseCase,
    SyncMailboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.mailbox.registry import (
    configured_providers,
    get_readers,
    get_subscribers,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    DynamoDBMailboxConnectionRepository,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.contexts.ingestion.presentation.http.gmail_notifications import (
    PubSubEnvelope,
    UndecodableNotificationError,
    to_mailbox_event,
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
def _build_keep_alive() -> KeepSubscriptionAliveUseCase:
    return KeepSubscriptionAliveUseCase(
        subscribers=get_subscribers(),
        connection_repository=build_connection_repository(),
    )


def get_keep_alive_use_case() -> KeepSubscriptionAliveUseCase:
    return _build_keep_alive()


@functools.lru_cache(maxsize=1)
def build_connection_repository() -> DynamoDBMailboxConnectionRepository:
    return DynamoDBMailboxConnectionRepository(
        client=get_dynamodb_client(),
        table_name=get_ingestion_settings().mailbox_connections_table,
    )


@functools.lru_cache(maxsize=1)
def build_sync_use_case() -> SyncMailboxUseCase:
    """Shared by the doorbell and by an on-demand refresh, so both go through
    exactly the same filtered read.
    """
    settings = get_ingestion_settings()

    return SyncMailboxUseCase(
        readers=get_readers(),
        inbox_repository=DynamoDBUserInboxRepository(
            client=get_dynamodb_client(),
            table_name=settings.user_inboxes_table,
        ),
        connection_repository=build_connection_repository(),
        receive_use_case=get_use_case(),
    )


@functools.lru_cache(maxsize=1)
def _build_use_case() -> HandleMailboxEventUseCase:
    return HandleMailboxEventUseCase(
        connection_repository=build_connection_repository(),
        sync_use_case=build_sync_use_case(),
        # Every notification is a free chance to renew: a mailbox that gets
        # mail keeps its own subscription alive, leaving the scheduled sweep
        # only the quiet ones to worry about.
        keep_alive=_build_keep_alive(),
    )


def get_mailbox_event_use_case() -> HandleMailboxEventUseCase:
    return _build_use_case()


def _handle(
    use_case: HandleMailboxEventUseCase,
    event: MailboxEvent,
) -> MailboxEventResponse:
    result = use_case.execute(event)
    _logger.info(
        "mailbox event handled",
        extra={
            "provider": event.provider.value,
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
    """The doorbell for the simulated provider."""
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

    return _handle(use_case, event)


@router.post(
    "/gmail",
    status_code=status.HTTP_204_NO_CONTENT,
)
def receive_gmail_notification(
    envelope: PubSubEnvelope,
    use_case: Annotated[
        HandleMailboxEventUseCase,
        Depends(get_mailbox_event_use_case),
    ],
) -> None:
    """Google's push notification, wrapped by Pub/Sub.

    Always answers 204, even for a notification we can make no sense of.
    Pub/Sub retries anything that is not a success, with backoff, for days —
    so returning an error for a message that will never become valid means
    being redelivered the same broken message indefinitely.
    """
    try:
        event = to_mailbox_event(envelope)
    except UndecodableNotificationError:
        _logger.exception("discarding unreadable gmail notification")

        return

    _handle(use_case, event)


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
        detail=(
            f"No mailbox adapter for provider {provider!r}. "
            f"Configured: {', '.join(configured_providers()) or 'none'}"
        ),
    )
