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
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId, ValueObject


@dataclass(frozen=True, slots=True)
class UserInbox(ValueObject):
    """The inbound address assigned to a user, with the senders they trust.

    This is ingestion's own projection of a user: the context stores what it
    needs to attribute and filter an email, and nothing else about the person.
    """

    user_id: UserId
    address: EmailAddress
    sender_policy: AuthorizedSenderPolicy


@dataclass(slots=True)
class BankNotification(AggregateRoot[NotificationId]):
    user_id: UserId
    message_id: EmailMessageId
    idempotency_key: IdempotencyKey
    sender: EmailAddress
    subject: str
    raw_content: str
    received_at: PosixTime
    status: ProcessingStatus

    def __post_init__(self) -> None:
        # An ignored notification is the one exception: `ignore` drops the body
        # on purpose, and a record read back from storage must rebuild cleanly.
        if self.status is not ProcessingStatus.IGNORED and not self.raw_content.strip():
            raise ValueError("Raw email content cannot be empty")

    @classmethod
    def receive(
        cls,
        *,
        user_id: UserId,
        message_id: EmailMessageId,
        sender: EmailAddress,
        subject: str,
        raw_content: str,
        received_at: PosixTime,
    ) -> Self:
        notification = cls(
            id=NotificationId.for_message(user_id=user_id, message_id=message_id),
            user_id=user_id,
            message_id=message_id,
            idempotency_key=IdempotencyKey.from_message(
                user_id=user_id,
                message_id=message_id,
            ),
            sender=sender,
            subject=subject.strip(),
            raw_content=raw_content,
            received_at=received_at,
            status=ProcessingStatus.RECEIVED,
        )
        notification.record_event(
            BankNotificationReceived(
                notification_id=notification.id,
                user_id=notification.user_id,
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
                user_id=self.user_id,
                message_id=self.message_id,
            ),
        )

    def ignore(self, *, reason: NotificationIgnoredReason) -> None:
        """Reject the notification and discard its body.

        Only the metadata survives — who sent it and when — so the user can
        later decide to approve that sender. The email itself stays in their
        mailbox, unread and untouched: an email we are not allowed to read is
        one we must not keep either.
        """
        if self.status is not ProcessingStatus.RECEIVED:
            raise InvalidNotificationStateError(
                f"Cannot ignore notification with status {self.status}",
            )

        self.status = ProcessingStatus.IGNORED
        self.raw_content = ""
        self.record_event(
            BankNotificationIgnored(
                notification_id=self.id,
                user_id=self.user_id,
                message_id=self.message_id,
                sender=self.sender,
                reason=reason,
            ),
        )
