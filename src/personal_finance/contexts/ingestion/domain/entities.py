from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationFailed,
    BankNotificationIgnored,
    BankNotificationQueued,
    BankNotificationReceived,
    TransactionExtracted,
    TransactionExtractionDeferred,
)
from personal_finance.contexts.ingestion.domain.exceptions import (
    InvalidNotificationStateError,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId, ValueObject


# States in which the email body has been deliberately discarded.
_BODYLESS_STATUSES = frozenset(
    {ProcessingStatus.IGNORED, ProcessingStatus.PROCESSED},
)


@dataclass(frozen=True, slots=True)
class UserInbox(ValueObject):
    """The inbound address assigned to a user, with the senders they trust.

    This is ingestion's own projection of a user: the context stores what it
    needs to attribute and filter an email, and nothing else about the person.
    """

    user_id: UserId
    address: EmailAddress
    sender_policy: AuthorizedSenderPolicy
    # Two milestones of connecting a bank, kept here rather than derived from
    # the notification table: answering "is this account receiving expenses
    # yet?" is a question a screen asks on a timer, and reading one item is
    # what makes that affordable. Both are written once and never cleared —
    # they record that something happened, not that it is still true.
    forwarding_confirmed_at: PosixTime | None = None
    first_accepted_at: PosixTime | None = None


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
    # Only meaningful (and only ever set) alongside `PENDING_FALLBACK`.
    deferred_reason: NotificationDeferredReason | None = None

    def __post_init__(self) -> None:
        # `ignore` and `complete` drop the body on purpose, and a record read
        # back from storage in either state must rebuild cleanly.
        if self.status not in _BODYLESS_STATUSES and not self.raw_content.strip():
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

    def start_processing(self) -> None:
        if self.status is not ProcessingStatus.QUEUED:
            raise InvalidNotificationStateError(
                f"Cannot start processing a notification with status {self.status}",
            )

        self.status = ProcessingStatus.PROCESSING

    def complete(self, transaction: ExtractedTransaction) -> None:
        if self.status is not ProcessingStatus.PROCESSING:
            raise InvalidNotificationStateError(
                f"Cannot complete a notification with status {self.status}",
            )

        self.status = ProcessingStatus.PROCESSED
        # The body has served its purpose. Dropping it keeps the raw email out
        # of storage for longer than the extraction actually needed it.
        self.raw_content = ""
        self.record_event(
            TransactionExtracted(
                notification_id=self.id,
                user_id=self.user_id,
                message_id=self.message_id,
                transaction=transaction,
            ),
        )

    def defer_to_fallback(self, *, reason: NotificationDeferredReason) -> None:
        """Nothing usable came out of parsing. Keep the body: whichever
        avenue produced `reason` may still be worth another look later, and a
        dropped body cannot be re-read.
        """
        if self.status is not ProcessingStatus.PROCESSING:
            raise InvalidNotificationStateError(
                f"Cannot defer a notification with status {self.status}",
            )

        self.status = ProcessingStatus.PENDING_FALLBACK
        self.deferred_reason = reason
        self.record_event(
            TransactionExtractionDeferred(
                notification_id=self.id,
                user_id=self.user_id,
                message_id=self.message_id,
                sender=self.sender,
                reason=reason,
            ),
        )

    def fail(self, *, reason: str) -> None:
        if self.status is not ProcessingStatus.PROCESSING:
            raise InvalidNotificationStateError(
                f"Cannot fail a notification with status {self.status}",
            )

        self.status = ProcessingStatus.FAILED
        self.record_event(
            BankNotificationFailed(
                notification_id=self.id,
                user_id=self.user_id,
                message_id=self.message_id,
                reason=reason,
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
