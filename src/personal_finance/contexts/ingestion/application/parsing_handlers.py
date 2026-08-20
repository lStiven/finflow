from __future__ import annotations

import dataclasses
import enum

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.ports import (
    BankNotificationRepository,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.parsing.text import extract_text
from personal_finance.contexts.ingestion.domain.value_objects import ProcessingStatus
from personal_finance.shared.application.ports import EventPublisher


class ParseOutcome(enum.Enum):
    EXTRACTED = "extracted"
    # No deterministic template matched; the LLM fallback owns it now.
    DEFERRED = "deferred"
    # The message pointed at a notification that is not waiting to be parsed:
    # already done, ignored, or gone. Redelivery lands here.
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ParseNotificationResult:
    outcome: ParseOutcome
    status: ProcessingStatus | None = None


class ParseNotificationUseCase:
    """Turns one queued notification into an extracted transaction.

    Tries the bank's own templates first and only defers to the LLM fallback
    when none of them matches, which is the order the whole pipeline depends
    on: a deterministic read is cheap, repeatable and auditable.
    """

    def __init__(
        self,
        *,
        repository: BankNotificationRepository,
        registry: ParserRegistry,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._event_publisher = event_publisher

    def execute(self, message: ParseNotificationMessage) -> ParseNotificationResult:
        notification = self._repository.get(message.idempotency_key)

        if notification is None or notification.status is not ProcessingStatus.QUEUED:
            # SQS delivery is at-least-once, so the same message arrives again
            # after a slow ack. Anything not sitting in QUEUED was already
            # handled, and re-running it would republish its events.
            return ParseNotificationResult(
                outcome=ParseOutcome.SKIPPED,
                status=notification.status if notification else None,
            )

        notification.start_processing()
        parser = self._registry.for_sender(notification.sender)

        if parser is None:
            # Nobody knows this bank's templates yet. Not a failure: the
            # fallback can still read it.
            notification.defer_to_fallback()

            return self._finish(notification, ParseOutcome.DEFERRED)

        try:
            transaction = parser.parse(extract_text(notification.raw_content))
        except ValueError as error:
            # A template matched but a field was unreadable — a malformed
            # amount, an impossible date. Never guess: record the failure.
            notification.fail(reason=str(error))

            return self._finish(notification, ParseOutcome.FAILED)

        if transaction is None:
            notification.defer_to_fallback()

            return self._finish(notification, ParseOutcome.DEFERRED)

        notification.complete(transaction)

        return self._finish(notification, ParseOutcome.EXTRACTED)

    def _finish(
        self,
        notification: BankNotification,
        outcome: ParseOutcome,
    ) -> ParseNotificationResult:
        # Persist before publishing: an event about a state that was never
        # stored would be a lie the rest of the system acts on.
        self._repository.save(notification)
        self._event_publisher.publish(notification.pull_events())

        return ParseNotificationResult(outcome=outcome, status=notification.status)
