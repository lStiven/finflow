"""Read side: what a user can see about the mail that arrived for them.

The reader answers with one window of one user's notifications, newest first,
and DynamoDB does the ordering and the trimming — the list is the only thing
here that polls, and a page of twenty must cost twenty rows rather than
everything the account ever received.

The counts are the other half of that: a total is a statement about the whole
history, so it cannot be windowed, and it is asked for rather than paid for on
every refresh.

This is the only way ingestion is readable from outside. It exposes what a
person needs to see about their own mail — who sent it, when, and how far it
got — and never the email itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.ports import (
    NotificationPageRequest,
    NotificationReader,
    NotificationSummary,
)
from personal_finance.contexts.ingestion.domain.value_objects import ProcessingStatus
from personal_finance.shared.domain.value_objects import UserId


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NotificationQuery:
    user_id: UserId
    status: ProcessingStatus | None = None
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    # The counts read the account's whole history, so nothing pays for them
    # without asking.
    with_counts: bool = False


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NotificationPageView:
    notifications: Sequence[NotificationSummary]
    # Whether another page is behind this one, which is what a list needs to
    # know and costs one row to answer.
    has_more: bool
    # Every status this user has, filter or no filter: this is the summary on
    # the "connect your bank" screen, and it must not move when somebody
    # narrows the list to one status. None unless it was asked for.
    counts: Mapping[ProcessingStatus, int] | None
    # How many matched the filter. Comes out of the counts, and is None
    # whenever they are.
    total: int | None


class ListNotificationsUseCase:
    """One user's notifications, newest first."""

    def __init__(self, *, reader: NotificationReader) -> None:
        self._reader = reader

    def execute(self, query: NotificationQuery) -> NotificationPageView:
        page = self._reader.page_by_user(
            NotificationPageRequest(
                user_id=query.user_id,
                status=query.status,
                limit=query.limit,
                offset=query.offset,
            ),
        )

        if not query.with_counts:
            return NotificationPageView(
                notifications=page.notifications,
                has_more=page.has_more,
                counts=None,
                total=None,
            )

        counts = self._reader.count_by_status(query.user_id)

        return NotificationPageView(
            notifications=page.notifications,
            has_more=page.has_more,
            counts=counts,
            total=(
                sum(counts.values())
                if query.status is None
                else counts.get(query.status, 0)
            ),
        )
