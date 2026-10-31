from __future__ import annotations

import dataclasses
import datetime
import enum
import logging

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.ports import (
    BankNotificationRepository,
    ModelStandingRepository,
    ModelUsageQuota,
    TransactionExtractor,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.model_standing import (
    ModelAccess,
    OffenseOutcome,
)
from personal_finance.contexts.ingestion.domain.parsing.dates import BOGOTA
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.parsing.text import extract_text
from personal_finance.contexts.ingestion.domain.transactions import ExtractedTransfer
from personal_finance.contexts.ingestion.domain.value_objects import (
    NotificationDeferredReason,
    ProcessingStatus,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)

#: How many times one offense is retried against a record another worker
#: keeps changing. Past this the message goes back to the queue.
_STANDING_ATTEMPTS = 5


class StandingContentionError(Exception):
    """The person's record kept changing under this worker; try again later."""


class ParseOutcome(enum.Enum):
    EXTRACTED = "extracted"
    # A template missed and the model read it instead. Kept distinct from
    # `EXTRACTED` because the two cost very different amounts and a rise in
    # this one is the signal that a bank changed its wording.
    EXTRACTED_BY_FALLBACK = "extracted_by_fallback"
    # Neither a template nor the model could read it — or there is no model
    # configured at all. The email is kept, not discarded.
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

    A template may answer with a *transfer* rather than a transaction — money
    moving between two instruments of the same owner, which is two movements
    with one email behind them. The fallback never does: a model asked to
    choose which of two instruments an amount belongs to would be guessing at
    which balance moves, and on a credit card the wrong guess adds the payment
    to the debt it just cleared.

    The fallback is optional. Without one — no API key, or a deployment that
    wants none — an unrecognised alert is kept as `PENDING_FALLBACK` exactly
    as before, so turning the model off degrades coverage instead of losing
    email.

    The quota is not optional: every wiring has to say how much of the model
    one person may spend, because a forgotten limit is no limit at all. Nor
    is the standing: a person the model is suspended for is never read, and
    an email the model judges not to be from a bank counts towards a strike.
    """

    def __init__(
        self,
        *,
        repository: BankNotificationRepository,
        registry: ParserRegistry,
        event_publisher: EventPublisher,
        model_quota: ModelUsageQuota,
        standings: ModelStandingRepository,
        fallback_extractor: TransactionExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._event_publisher = event_publisher
        self._model_quota = model_quota
        self._standings = standings
        self._fallback_extractor = fallback_extractor

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
            # The sender does not name a bank. Before giving up on the
            # templates, ask whether this is a forward: the whole intake model
            # is that people forward their bank's mail, and a forward made by
            # hand arrives from the person rather than from the bank, which
            # used to send every one of them straight past the parser that
            # could read them.
            parser = self._registry.for_forwarded_message(notification.raw_content)

        if parser is None:
            # Nobody knows this bank's templates yet. Not a failure: the
            # fallback can still read it.
            return self._fall_back(notification)

        try:
            transaction = parser.parse(extract_text(notification.raw_content))
        except ValueError as error:
            # A template matched but a field was unreadable — a malformed
            # amount, an impossible date. Never guess: record the failure.
            notification.fail(reason=str(error))

            return self._finish(notification, ParseOutcome.FAILED)

        if transaction is None:
            # The bank is known but this particular alert is not one of its
            # templates — a new wording, or a kind of movement nobody has
            # written a pattern for.
            return self._fall_back(notification)

        if isinstance(transaction, ExtractedTransfer):
            # Two movements out of one email: money left an account and the
            # debt on a card of the same owner fell by the same amount. Only
            # a template ever produces one — the fallback below is asked for a
            # single movement and refuses these on purpose.
            notification.complete_as_transfer(transaction)

            return self._finish(notification, ParseOutcome.EXTRACTED)

        notification.complete(transaction)

        return self._finish(notification, ParseOutcome.EXTRACTED)

    def _fall_back(self, notification: BankNotification) -> ParseNotificationResult:
        """Ask the model, if there is one, and keep the email either way.

        A model that is rate limited or down raises out of here on purpose:
        nothing has been persisted yet, so the stored notification is still
        `QUEUED` and the redelivered message tries again. Recording a
        permanent outcome for a temporary outage would quietly drop a real
        transaction.
        """
        if self._fallback_extractor is None:
            notification.defer_to_fallback(
                reason=NotificationDeferredReason.NO_FALLBACK_CONFIGURED,
            )

            return self._finish(notification, ParseOutcome.DEFERRED)

        now = PosixTime.now()

        # Before the quota: a suspended person spends none of it.
        if self._standings.get(notification.user_id).access(now) is not (
            ModelAccess.OPEN
        ):
            notification.defer_to_fallback(
                reason=NotificationDeferredReason.MODEL_SUSPENDED,
            )

            return self._finish(notification, ParseOutcome.DEFERRED)

        # Reserved before the call, never counted after it: a read the model
        # has already been paid for cannot be refused.
        day = _today()

        if not self._model_quota.reserve(
            user_id=notification.user_id,
            notification_id=notification.id,
            day=day,
        ):
            notification.defer_to_fallback(
                reason=NotificationDeferredReason.DAILY_LIMIT_REACHED,
            )

            return self._finish(notification, ParseOutcome.DEFERRED)

        try:
            reading = self._fallback_extractor.extract(
                sender=notification.sender,
                subject=notification.subject,
                body=notification.raw_content,
                received_at=notification.received_at,
            )
        except Exception:
            # Nothing was read, and the redelivered message reserves again. A
            # failed release only costs one read, so it must not hide the
            # error that decides whether the email is retried.
            try:
                self._model_quota.release(
                    user_id=notification.user_id,
                    notification_id=notification.id,
                    day=day,
                )
            except Exception:
                _logger.exception("could not hand back an unused model read")

            raise

        if reading.not_bank_mail:
            self._record_offense(notification, now=now)

        if reading.transaction is None:
            notification.defer_to_fallback(
                reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
            )

            return self._finish(notification, ParseOutcome.DEFERRED)

        notification.complete(reading.transaction)

        return self._finish(notification, ParseOutcome.EXTRACTED_BY_FALLBACK)

    def _record_offense(
        self, notification: BankNotification, *, now: PosixTime
    ) -> None:
        """Count one email that was not from a bank against its owner.

        Before the notification is saved, and idempotent by its id: if the
        save below fails, the redelivered message reads the model again and
        lands here on an offense already counted. A strike is published as
        soon as it is stored, so a later failure cannot unsay it.
        """
        for _ in range(_STANDING_ATTEMPTS):
            standing = self._standings.get(notification.user_id)
            outcome = standing.record_offense(
                notification_id=notification.id,
                received_at=notification.received_at,
                now=now,
            )

            if outcome is OffenseOutcome.IGNORED:
                # Already counted, or suspended meanwhile: nothing to write.
                return

            if self._standings.save(standing):
                self._event_publisher.publish(standing.pull_events())

                return

        raise StandingContentionError(
            "Another worker kept changing this person's record with the model",
        )

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


def _today() -> datetime.date:
    """The day a read is counted against, in Bogotá like every other day here.

    When the model is called, not when the email arrived: the limit is on what
    is spent today, and a backlog cleared after an outage is spent today.
    """
    return PosixTime.now().to_datetime().astimezone(BOGOTA).date()
