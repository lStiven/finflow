"""What the app itself shows: every alert, kept for the screen as well.

Telegram is one place an alert goes; the in-app inbox is the other, and it
exists for everybody — with or without a channel — because the question it
answers is «¿cuál fue mi último movimiento?», which somebody asks from inside
the app. An entry holds the same facts the message was built from, never the
message: the words belong to whoever draws them.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import enum
import logging
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    WeeklySummary,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.ports import (
        Inbox,
        MovementPresence,
    )


_logger = logging.getLogger(__name__)


#: How many entries a screen may ask for at once.
MAX_INBOX_PAGE = 50
DEFAULT_INBOX_PAGE = 20
#: How far back a read looks for alerts still standing once erased movements
#: have left a page short.
MAX_CANDIDATES = 200


class InboxKind(enum.Enum):
    MOVEMENT = "movement"
    WEEKLY_SUMMARY = "weekly_summary"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboxEntry:
    """One alert, as the app keeps it.

    `entry_id` is the fact's own id — the integration event's for a movement,
    a derived one for a week — so writing the same fact twice lands on the
    same entry instead of beside it.

    Exactly one of `movement` and `summary` is set, the one `kind` names.
    """

    user_id: UserId
    entry_id: uuid.UUID
    created_at: PosixTime
    kind: InboxKind
    movement: MovementAlert | None = None
    summary: WeeklySummary | None = None

    def __post_init__(self) -> None:
        expected = (
            self.kind is InboxKind.MOVEMENT,
            self.kind is InboxKind.WEEKLY_SUMMARY,
        )

        if (self.movement is not None, self.summary is not None) != expected:
            raise ValueError(
                f"An inbox entry of kind {self.kind.value} needs its facts"
            )


class ListInboxUseCase:
    def __init__(
        self,
        *,
        inbox: Inbox,
        movements: MovementPresence | None = None,
    ) -> None:
        self._inbox = inbox
        self._movements = movements

    def execute(
        self, *, user_id: UserId, limit: int = DEFAULT_INBOX_PAGE
    ) -> Sequence[InboxEntry]:
        """Newest first, this user's only, and never about an erased movement.

        Asked at read time rather than cleaned up when the movement goes:
        whichever order the erasure and the alert arrive in, the inbox reads
        the ledger as it is now.
        """
        size = max(1, min(limit, MAX_INBOX_PAGE))
        window = size
        existing: set[str] = set()
        asked: set[str] = set()

        # Wider only when erased movements left the page short: a page that
        # comes back empty with valid alerts behind it reads as "no alerts".
        while True:
            entries = self._inbox.recent(user_id=user_id, limit=window)

            if self._movements is None:
                return entries[:size]

            about = [
                movement_id
                for entry in entries
                if (movement_id := _movement_id(entry)) is not None
                and movement_id not in asked
            ]

            if about:
                try:
                    existing |= self._movements.existing(
                        user_id=user_id, movement_ids=about
                    )
                except Exception:
                    # Broad on purpose: one stale entry is better than an
                    # inbox that does not load. Logged without the error.
                    _logger.warning("could not check which movements still exist")

                    return entries[:size]

                asked.update(about)

            kept = [
                entry
                for entry in entries
                if (movement_id := _movement_id(entry)) is None
                or movement_id in existing
            ]

            if len(kept) >= size or len(entries) < window or window >= MAX_CANDIDATES:
                return kept[:size]

            window = min(window * 2, MAX_CANDIDATES)


def _movement_id(entry: InboxEntry) -> str | None:
    return None if entry.movement is None else entry.movement.movement_id


class InboxEntryNotFoundError(Exception):
    """Dismissing an entry this user does not have."""


class ManageInboxUseCase:
    """What the owner may do to their own inbox: hide one, or hide all."""

    def __init__(self, *, inbox: Inbox) -> None:
        self._inbox = inbox

    def dismiss(self, *, user_id: UserId, entry_id: uuid.UUID) -> None:
        if not self._inbox.dismiss(user_id=user_id, entry_id=entry_id):
            raise InboxEntryNotFoundError(str(entry_id))

    def dismiss_all(self, *, user_id: UserId) -> int:
        return self._inbox.dismiss_all(user_id=user_id)
