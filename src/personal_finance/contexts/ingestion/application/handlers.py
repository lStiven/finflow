from __future__ import annotations

import dataclasses
import enum

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.ports import (
    BankNotificationRepository,
    QueuePublisher,
    UserInboxRepository,
)
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.application.ports import EventPublisher


class ReceiveOutcome(enum.Enum):
    """What happened to the intake, as opposed to what state the notification
    ended up in — which `ProcessingStatus` already answers.
    """

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    UNKNOWN_RECIPIENT = "unknown_recipient"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReceiveBankNotificationResult:
    outcome: ReceiveOutcome
    # Both are absent when the recipient is unknown: with no user to attribute
    # the email to, no notification is ever created.
    notification_id: NotificationId | None = None
    status: ProcessingStatus | None = None


class ReceiveBankNotificationUseCase:
    """Handles an inbound bank email: attributes it to the user who owns the
    recipient address, filters it against that user's approved senders,
    persists it idempotently, hands it to the parsing queue, and publishes
    whatever domain events the aggregate raised.
    """

    def __init__(
        self,
        *,
        repository: BankNotificationRepository,
        inbox_repository: UserInboxRepository,
        queue_publisher: QueuePublisher,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._inbox_repository = inbox_repository
        self._queue_publisher = queue_publisher
        self._event_publisher = event_publisher

    def execute(
        self,
        command: ReceiveBankNotificationCommand,
    ) -> ReceiveBankNotificationResult:
        inbox = self._inbox_repository.find_by_address(command.recipient)

        if inbox is None:
            # Nothing is stored: the webhook is reachable by anyone, and
            # persisting unattributable email would be an unbounded write.
            return ReceiveBankNotificationResult(
                outcome=ReceiveOutcome.UNKNOWN_RECIPIENT,
            )

        notification = self._receive(command, inbox)
        stored = self._repository.add_if_new(notification)

        if stored is not None:
            # Drop the events of the rejected in-memory copy and continue with
            # the stored aggregate, which may still be waiting to be queued
            # because an earlier attempt failed after persisting.
            notification.pull_events()
            notification = stored

        self._mark_first_accepted(inbox, notification)
        self._enqueue_if_pending(notification)
        self._event_publisher.publish(notification.pull_events())

        return ReceiveBankNotificationResult(
            outcome=(ReceiveOutcome.DUPLICATE if stored else ReceiveOutcome.ACCEPTED),
            notification_id=notification.id,
            status=notification.status,
        )

    def _receive(
        self,
        command: ReceiveBankNotificationCommand,
        inbox: UserInbox,
    ) -> BankNotification:
        notification = BankNotification.receive(
            user_id=inbox.user_id,
            message_id=command.message_id,
            sender=command.sender,
            subject=command.subject,
            raw_content=command.raw_content,
            received_at=command.received_at,
        )

        # An unapproved sender is still recorded, so the user can see what
        # arrived and approve it later, but it never reaches the parser.
        if not inbox.sender_policy.is_authorized(command.sender):
            notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

        return notification

    def _mark_first_accepted(
        self,
        inbox: UserInbox,
        notification: BankNotification,
    ) -> None:
        """Record the moment this inbox first let something through.

        Written here rather than counted later because of who asks: the
        screen that walks a new user through connecting their bank polls
        "am I receiving expenses yet?" every few seconds, and that has to
        cost one item read, not a pass over everything the account ever
        received. One write per account — after the first alert the field is
        set and this does nothing.

        A duplicate still counts: it means an accepted email did arrive, and
        the repository keeps the earliest timestamp either way.
        """
        if (
            inbox.first_accepted_at is not None
            or notification.status is ProcessingStatus.IGNORED
        ):
            return

        self._inbox_repository.mark_first_accepted(
            address=inbox.address,
            accepted_at=notification.received_at,
        )

    def _enqueue_if_pending(self, notification: BankNotification) -> None:
        """Queue the notification for parsing unless it was ignored or an
        earlier attempt already queued it.
        """
        if notification.status is not ProcessingStatus.RECEIVED:
            return

        self._queue_publisher.enqueue(
            ParseNotificationMessage(
                notification_id=notification.id,
                user_id=notification.user_id,
                idempotency_key=notification.idempotency_key,
                message_id=notification.message_id,
                received_at=notification.received_at,
            ),
        )
        notification.mark_as_queued()
        self._repository.save(notification)
