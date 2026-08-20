from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.ports import (
    BankNotificationRepository,
    QueuePublisher,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    NotificationId,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.application.ports import EventPublisher


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReceiveBankNotificationResult:
    notification_id: NotificationId
    status: ProcessingStatus
    is_duplicate: bool


class ReceiveBankNotificationUseCase:
    """Handles an inbound bank email: filters it by authorized sender,
    persists the notification idempotently, hands it to the parsing queue,
    and publishes whatever domain events the aggregate raised.
    """

    def __init__(
        self,
        *,
        repository: BankNotificationRepository,
        sender_policy: AuthorizedSenderPolicy,
        queue_publisher: QueuePublisher,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._sender_policy = sender_policy
        self._queue_publisher = queue_publisher
        self._event_publisher = event_publisher

    def execute(
        self,
        command: ReceiveBankNotificationCommand,
    ) -> ReceiveBankNotificationResult:
        notification = BankNotification.receive(
            message_id=command.message_id,
            sender=command.sender,
            subject=command.subject,
            raw_content=command.raw_content,
            received_at=command.received_at,
        )

        if not self._sender_policy.is_authorized(command.sender):
            notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)

        stored = self._repository.add_if_new(notification)

        if stored is not None:
            # Drop the events of the rejected in-memory copy and continue with
            # the stored aggregate, which may still be waiting to be queued
            # because an earlier attempt failed after persisting.
            notification.pull_events()
            notification = stored

        self._enqueue_if_pending(notification)
        self._event_publisher.publish(notification.pull_events())

        return ReceiveBankNotificationResult(
            notification_id=notification.id,
            status=notification.status,
            is_duplicate=stored is not None,
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
                idempotency_key=notification.idempotency_key,
                message_id=notification.message_id,
                received_at=notification.received_at,
            ),
        )
        notification.mark_as_queued()
        self._repository.save(notification)
