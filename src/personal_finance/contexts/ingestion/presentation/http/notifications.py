"""What arrived for the caller, and how far it got.

Ingestion's only read surface, and the only part of this context mounted in a
real deployment — the webhook next door is a local testing seam. It exists so
somebody who forwarded a bank alert can tell the three answers apart: it never
arrived, it arrived from a sender they never approved, or it arrived and
nothing could be read out of it. Without this the same three failures look
identical from a client: no new movement.

Scoped to the token like every other read here. There is no endpoint that
takes a user id, and the reader queries one partition of the `by_user_received_at`
index, so one person's mail is not reachable from another's session.
"""

from __future__ import annotations

import functools
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.ports import NotificationSummary
from personal_finance.contexts.ingestion.application.queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ListNotificationsUseCase,
    NotificationQuery,
)
from personal_finance.contexts.ingestion.domain.setup import SetupStep
from personal_finance.contexts.ingestion.domain.transactions import InstrumentKind
from personal_finance.contexts.ingestion.domain.value_objects import (
    NotificationDeferredReason,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBNotificationReader,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)
from personal_finance.shared.presentation.catalog import CatalogOption, options


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


class IngestionCatalogResponse(BaseModel):
    """Ingestion's vocabulary, for the screens that render or filter by it.

    `instrument_kinds` is the one a client sends rather than displays, and it
    is published from here rather than from Financial because these are the
    words a bank alert arrives with. An account declared with any other
    spelling is accepted and then never matches an alert — silently, since
    nothing about that is an error.
    """

    processing_statuses: list[CatalogOption]
    ignored_reasons: list[CatalogOption]
    deferred_reasons: list[CatalogOption]
    instrument_kinds: list[CatalogOption]
    # In the order a connect-your-bank screen shows them, which is the one
    # place that order gets decided.
    setup_steps: list[CatalogOption]


class NotificationResponse(BaseModel):
    """One arrival. No body: a list never shows the email itself, and two of
    the states below discarded theirs on purpose.
    """

    id: str
    message_id: str
    sender: str
    subject: str
    # `received` | `queued` | `processing` | `processed` | `pending_fallback`
    # | `failed` | `ignored`.
    status: str
    # Only alongside `pending_fallback`: `no_fallback_configured` when no model
    # is set up, `fallback_found_nothing` when one read the email and declined.
    # Either way the state is final for that attempt, not "still going".
    deferred_reason: str | None
    received_at: int


class NotificationListResponse(BaseModel):
    notifications: list[NotificationResponse]
    # Whether another page is behind this one. This rather than a total,
    # because a total is a statement about every email the account ever
    # received and this list polls.
    has_more: bool
    limit: int
    offset: int
    # Both only when `with_counts` asked for them, and null otherwise: they
    # are the one answer here that reads the account's whole history.
    counts: dict[str, int] | None = None
    # How many matched the filter.
    total: int | None = None


@functools.lru_cache(maxsize=1)
def _build_list_use_case() -> ListNotificationsUseCase:
    settings = get_ingestion_settings()

    return ListNotificationsUseCase(
        reader=DynamoDBNotificationReader(
            client=get_dynamodb_client(),
            table_name=settings.notifications_table,
        ),
    )


def get_list_notifications_use_case() -> ListNotificationsUseCase:
    return _build_list_use_case()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


@router.get("/catalog", response_model=IngestionCatalogResponse)
def get_catalog() -> IngestionCatalogResponse:
    """What this context's states are called, and what an instrument may be."""
    return IngestionCatalogResponse(
        processing_statuses=options(ProcessingStatus),
        ignored_reasons=options(NotificationIgnoredReason),
        deferred_reasons=options(NotificationDeferredReason),
        instrument_kinds=options(InstrumentKind),
        setup_steps=options(SetupStep),
    )


@router.get("/notifications", response_model=NotificationListResponse)
def list_notifications(
    user_id: CurrentUser,
    use_case: Annotated[
        ListNotificationsUseCase,
        Depends(get_list_notifications_use_case),
    ],
    status: Annotated[ProcessingStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    with_counts: Annotated[bool, Query()] = False,
) -> NotificationListResponse:
    """The caller's bank mail, newest first.

    An empty list is an ordinary answer: it means nothing has been forwarded
    yet, or the forwarding rule is not doing what its owner thinks it is.

    `with_counts` adds the per-status summary and the total. It is off by
    default because it is the one part of this answer that reads everything
    the account ever received, while the page itself reads a page — a screen
    that polls asks for it once, not on every refresh.
    """
    page = use_case.execute(
        NotificationQuery(
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
            with_counts=with_counts,
        ),
    )

    return NotificationListResponse(
        notifications=[
            _notification_response(notification) for notification in page.notifications
        ],
        has_more=page.has_more,
        limit=limit,
        offset=offset,
        counts=(
            None
            if page.counts is None
            else {found.value: count for found, count in page.counts.items()}
        ),
        total=page.total,
    )


def _notification_response(notification: NotificationSummary) -> NotificationResponse:
    return NotificationResponse(
        id=str(notification.id.value),
        message_id=notification.message_id.value,
        sender=notification.sender.value,
        subject=notification.subject,
        status=notification.status.value,
        deferred_reason=(
            None
            if notification.deferred_reason is None
            else notification.deferred_reason.value
        ),
        received_at=notification.received_at.as_epoch_seconds(),
    )
