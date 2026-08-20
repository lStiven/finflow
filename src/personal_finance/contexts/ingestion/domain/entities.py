from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationIgnored,
    BankNotificationQueued,
    BankNotificationReceived,
)
from personal_finance.contexts.ingestion.domain.exceptions import (
    InvalidNotificationStateError,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime


@dataclass(slots=True)
class BankNotification(AggregateRoot[NotificationId]):
    message_id: EmailMessageId
    idempotency_key: IdempotencyKey
    sender: EmailAddress
    subject: str
    raw_content: str
    received_at: PosixTime
    status: ProcessingStatus

    def __post_init__(self) -> None:
        if not self.raw_content.strip():
            raise ValueError("Raw email content cannot be empty")

    @classmethod
    def receive(
        cls,
        *,
        message_id: EmailMessageId,
        sender: EmailAddress,
        subject: str,
        raw_content: str,
        received_at: PosixTime,
    ) -> Self:
        notification = cls(
            id=NotificationId.for_message(message_id),
            message_id=message_id,
            idempotency_key=IdempotencyKey.from_message_id(message_id),
            sender=sender,
            subject=subject.strip(),
            raw_content=raw_content,
            received_at=received_at,
            status=ProcessingStatus.RECEIVED,
        )
        notification.record_event(
            BankNotificationReceived(
                notification_id=notification.id,
                message_id=notification.message_id,
                sender=notification.sender,
                received_at=notification.received_at,
            ),
        )

        return notification

    def mark_as_queued(self) -> None:
        if self.status is not ProcessingStatus.RECEIVED:
            raise InvalidNotificationStateError(
                f"Cannot queue notification with status {self.status}",
            )

        self.status = ProcessingStatus.QUEUED
        self.record_event(
            BankNotificationQueued(
                notification_id=self.id,
                message_id=self.message_id,
            ),
        )

    def ignore(self, *, reason: NotificationIgnoredReason) -> None:
        if self.status is not ProcessingStatus.RECEIVED:
            raise InvalidNotificationStateError(
                f"Cannot ignore notification with status {self.status}",
            )

        self.status = ProcessingStatus.IGNORED
        self.record_event(
            BankNotificationIgnored(
                notification_id=self.id,
                message_id=self.message_id,
                sender=self.sender,
                reason=reason,
            ),
        )
