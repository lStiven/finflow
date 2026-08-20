from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ParseNotificationMessage:
    """Work-queue message asking a worker to parse a stored notification.

    Carries only a pointer to the persisted aggregate — never the raw email
    body — so the queue stays small and the worker always reads the
    authoritative record.
    """

    notification_id: NotificationId
    user_id: UserId
    idempotency_key: IdempotencyKey
    message_id: EmailMessageId
    received_at: PosixTime
