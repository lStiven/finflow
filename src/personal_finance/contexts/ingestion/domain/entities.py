from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Self

from personal_finance.contexts.financial.domain.exceptions import (
    InvalidNotificationStateError,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
    ProcessingStatus,
)


@dataclass(slots=True)
class BankNotification:
    id: NotificationId
    message_id: EmailMessageId
    idempotency_key: IdempotencyKey
    sender: EmailAddress
    subject: str
    raw_content: str
    received_at: datetime
    status: ProcessingStatus

    def __post_init__(self) -> None:
        if not self.raw_content.strip():
            raise ValueError("Raw email content cannot be empty")

        if self.received_at.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")

    @classmethod
    def receive(
        cls,
        *,
        message_id: EmailMessageId,
        sender: EmailAddress,
        subject: str,
        raw_content: str,
        received_at: datetime,
    ) -> Self:
        return cls(
            id=NotificationId.new(),
            message_id=message_id,
            idempotency_key=IdempotencyKey.from_message_id(message_id),
            sender=sender,
            subject=subject.strip(),
            raw_content=raw_content,
            received_at=received_at,
            status=ProcessingStatus.RECEIVED,
        )

    def mark_as_queued(self) -> None:
        if self.status is not ProcessingStatus.RECEIVED:
            raise InvalidNotificationStateError(
                f"Cannot queue notification with status {self.status}",
            )

        self.status = ProcessingStatus.QUEUED
