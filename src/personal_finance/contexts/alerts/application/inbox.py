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
from typing import TYPE_CHECKING
import uuid

from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    WeeklySummary,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.application.ports import Inbox


#: How many entries a screen may ask for at once.
MAX_INBOX_PAGE = 50
DEFAULT_INBOX_PAGE = 20


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
    def __init__(self, *, inbox: Inbox) -> None:
        self._inbox = inbox

    def execute(
        self, *, user_id: UserId, limit: int = DEFAULT_INBOX_PAGE
    ) -> Sequence[InboxEntry]:
        """Newest first. Only this user's: the port cannot answer otherwise."""
        return self._inbox.recent(
            user_id=user_id, limit=max(1, min(limit, MAX_INBOX_PAGE))
        )
