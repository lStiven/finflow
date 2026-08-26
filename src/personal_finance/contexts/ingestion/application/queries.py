"""Read side: what a user can see about the mail that arrived for them.

The reader hands over everything one user owns and the filtering, ordering and
counting happen here — the same choice merchant's read side makes, for the
same reason. A personal account holds tens or low hundreds of notifications,
all in one partition, and keeping the list and the counts in one answer stops
two queries from disagreeing about what is in the inbox.

This is the only way ingestion is readable from outside. It exposes what a
person needs to see about their own mail — who sent it, when, and how far it
got — and never the email itself.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.ports import (
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NotificationPage:
    notifications: Sequence[NotificationSummary]
    # How many matched the filter, so a client can page without guessing.
    total: int
    # Every status this user has, filter or no filter: this is the summary on
    # the "connect your bank" screen, and it must not move when somebody
    # narrows the list to one status.
    counts: Mapping[ProcessingStatus, int]


class ListNotificationsUseCase:
    """One user's notifications, newest first."""

    def __init__(self, *, reader: NotificationReader) -> None:
        self._reader = reader

    def execute(self, query: NotificationQuery) -> NotificationPage:
        found = list(self._reader.list_by_user(query.user_id))
        counts = Counter(notification.status for notification in found)

        if query.status is not None:
            found = [
                notification
                for notification in found
                if notification.status is query.status
            ]

        # Newest first: somebody opening this screen is asking whether the
        # email they just forwarded arrived.
        found.sort(
            key=lambda notification: notification.received_at.as_epoch_seconds(),
            reverse=True,
        )
        window = found[query.offset : query.offset + query.limit]

        return NotificationPage(
            notifications=window,
            total=len(found),
            counts=dict(counts),
        )
