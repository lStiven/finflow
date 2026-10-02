"""`GET /alerts/inbox`: the alerts the app shows, for the screen that polls.

Every alert that went — or would have gone — to Telegram, kept for 30 days,
newest first, for everybody whether or not they linked a channel. The screen
asks every few seconds while it is visible and shows what is new as a
floating notification, which is as close to real time as a deployment
without a socket gets.

Facts, never sentences: the counterparty and the bank are what the bank
wrote, untrusted, and the screen draws them as text.
"""

from __future__ import annotations

import functools
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from personal_finance.contexts.alerts.application.inbox import (
    DEFAULT_INBOX_PAGE,
    MAX_INBOX_PAGE,
    InboxEntry,
    InboxEntryNotFoundError,
    ListInboxUseCase,
    ManageInboxUseCase,
)
from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    WeeklySummary,
)
from personal_finance.contexts.alerts.infrastructure.financial.adapters import (
    build_movement_presence,
)
from personal_finance.contexts.alerts.infrastructure.persistence.inbox import (
    DynamoDBInbox,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_alerts_settings


router = APIRouter(prefix="/alerts", tags=["alerts"])


class InboxBudgetResponse(BaseModel):
    name: str
    currency: str
    limit: str
    spent: str
    #: Negative once the cap was passed.
    remaining: str
    state: Literal["ok", "warning", "over"]


class InboxMovementResponse(BaseModel):
    #: Null only for an alert published before movements carried their id.
    movement_id: str | None
    direction: Literal["outgoing", "incoming"]
    amount: str
    currency: str
    #: What the bank wrote. Untrusted: draw it as text.
    counterparty: str
    bank: str
    #: When the money moved, epoch seconds.
    occurred_at: int
    origin: Literal["bank_alert", "manual", "accrual", "scheduled"]
    unassigned: bool
    #: The budgets this purchase counts against, worst first. Empty for most.
    budgets: list[InboxBudgetResponse]


class InboxRiseResponse(BaseModel):
    category: str
    #: The owner's own name for a category they wrote; the English label for
    #: a shipped one, which a screen restates from `category`.
    label: str
    spent: str
    typical: str


class InboxSummaryResponse(BaseModel):
    week_start: str
    #: Sunday, inclusive.
    week_end: str
    currency: str
    spent: str
    movements: int
    #: The owner's average week before this one; null in their first week.
    typical: str | None
    rise: InboxRiseResponse | None


class InboxEntryResponse(BaseModel):
    id: str
    kind: Literal["movement", "weekly_summary"]
    #: When the alert was recorded, epoch seconds. What the list is ordered by.
    created_at: int
    movement: InboxMovementResponse | None
    summary: InboxSummaryResponse | None


class InboxResponse(BaseModel):
    entries: list[InboxEntryResponse]


@functools.lru_cache(maxsize=1)
def _build_inbox() -> DynamoDBInbox:
    return DynamoDBInbox(
        client=get_dynamodb_client(),
        table_name=get_alerts_settings().channels_table,
    )


@functools.lru_cache(maxsize=1)
def _build_list_inbox() -> ListInboxUseCase:
    return ListInboxUseCase(inbox=_build_inbox(), movements=build_movement_presence())


@functools.lru_cache(maxsize=1)
def _build_manage_inbox() -> ManageInboxUseCase:
    return ManageInboxUseCase(inbox=_build_inbox())


def get_list_inbox_use_case() -> ListInboxUseCase:
    return _build_list_inbox()


def get_manage_inbox_use_case() -> ManageInboxUseCase:
    return _build_manage_inbox()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


@router.get("/inbox", response_model=InboxResponse)
def list_inbox(
    user_id: CurrentUser,
    use_case: Annotated[ListInboxUseCase, Depends(get_list_inbox_use_case)],
    response: Response,
    limit: Annotated[int, Query(ge=1, le=MAX_INBOX_PAGE)] = DEFAULT_INBOX_PAGE,
) -> InboxResponse:
    """The newest alerts, this user's only, newest first."""
    # Somebody's purchases: nothing in between keeps a copy.
    response.headers["Cache-Control"] = "no-store"

    return InboxResponse(
        entries=[
            _entry(entry) for entry in use_case.execute(user_id=user_id, limit=limit)
        ],
    )


@router.delete("/inbox/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_inbox_entry(
    user_id: CurrentUser,
    entry_id: uuid.UUID,
    use_case: Annotated[ManageInboxUseCase, Depends(get_manage_inbox_use_case)],
) -> None:
    """Hide one alert from the app. 404 when this user has no such entry.

    Hidden for good: an alert redelivered later lands on the same row and
    stays hidden. Telegram is not touched — a message already sent stays sent.
    """
    try:
        use_case.dismiss(user_id=user_id, entry_id=entry_id)
    except InboxEntryNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such alert",
        ) from error


@router.delete("/inbox", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_inbox(
    user_id: CurrentUser,
    use_case: Annotated[ManageInboxUseCase, Depends(get_manage_inbox_use_case)],
) -> None:
    """Hide every alert this user has in the app."""
    use_case.dismiss_all(user_id=user_id)


def _entry(entry: InboxEntry) -> InboxEntryResponse:
    return InboxEntryResponse(
        id=str(entry.entry_id),
        kind=entry.kind.value,
        created_at=entry.created_at.as_epoch_seconds(),
        movement=None if entry.movement is None else _movement(entry.movement),
        summary=None if entry.summary is None else _summary(entry.summary),
    )


def _movement(alert: MovementAlert) -> InboxMovementResponse:
    return InboxMovementResponse(
        movement_id=alert.movement_id,
        direction=alert.direction.value,
        amount=str(alert.amount.amount),
        currency=alert.amount.currency.value,
        counterparty=alert.counterparty,
        bank=alert.bank,
        occurred_at=alert.occurred_at.as_epoch_seconds(),
        origin=alert.origin.value,
        unassigned=alert.unassigned,
        budgets=[
            InboxBudgetResponse(
                name=budget.name,
                currency=budget.currency.value,
                limit=str(budget.limit),
                spent=str(budget.spent),
                remaining=str(budget.remaining),
                state=budget.state.value,
            )
            for budget in alert.budgets
        ],
    )


def _summary(summary: WeeklySummary) -> InboxSummaryResponse:
    rise = summary.rise

    return InboxSummaryResponse(
        week_start=summary.week_start.isoformat(),
        week_end=summary.week_end.isoformat(),
        currency=summary.currency.value,
        spent=str(summary.spent),
        movements=summary.movements,
        typical=None if summary.typical is None else str(summary.typical),
        rise=(
            None
            if rise is None
            else InboxRiseResponse(
                category=rise.category,
                label=rise.label,
                spent=str(rise.spent),
                typical=str(rise.typical),
            )
        ),
    )
