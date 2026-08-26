"""What arrived for the caller, and how far it got.

Ingestion's only read surface, and the only part of this context mounted in a
real deployment — the webhook next door is a local testing seam. It exists so
somebody who forwarded a bank alert can tell the three answers apart: it never
arrived, it arrived from a sender they never approved, or it arrived and
nothing could be read out of it. Without this the same three failures look
identical from a client: no new movement.

Scoped to the token like every other read here. There is no endpoint that
takes a user id, and the reader queries one partition of the `by_user` index,
so one person's mail is not reachable from another's session.
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
from personal_finance.contexts.ingestion.domain.value_objects import ProcessingStatus
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBNotificationReader,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


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
    # How many matched the filter.
    total: int
    # Every status this user has, filter or no filter — keyed by status, so a
    # summary line does not move when the list is narrowed to one of them.
    counts: dict[str, int]
    limit: int
    offset: int


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
) -> NotificationListResponse:
    """The caller's bank mail, newest first.

    An empty list is an ordinary answer: it means nothing has been forwarded
    yet, or the forwarding rule is not doing what its owner thinks it is.
    """
    page = use_case.execute(
        NotificationQuery(
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        ),
    )

    return NotificationListResponse(
        notifications=[
            _notification_response(notification) for notification in page.notifications
        ],
        total=page.total,
        counts={status.value: count for status, count in page.counts.items()},
        limit=limit,
        offset=offset,
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
