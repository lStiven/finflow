"""Plan B: what happens when no deterministic template recognises an alert.

The deterministic path is covered in `test_parse_notification.py`. These pin
down the handover to the model, and above all that a model which is merely
unreachable never turns into a permanent outcome for a real email.
"""

from collections.abc import Sequence
import copy
from decimal import Decimal

import pytest

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
    ParseOutcome,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.llm.errors import (
    LLMTemporarilyUnavailableError,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
BANK_SENDER = "alertasynotificaciones@an.notificacionesbancolombia.com"
UNKNOWN_BANK = "alertas@otrobanco.com.co"
# A wording no template covers.
UNRECOGNISED = "Tu compra en LA TIENDA DE LA ESQUINA por $12.500 fue aprobada."
RECOGNISED = (
    "Bancolombia: Compraste COP29.259,00 en TIENDAS ARA con tu T.Cred *7653, "
    "el 20/08/2026 a las 12:00."
)


class InMemoryRepository:
    """Copies on the way in and out, the way real storage does.

    Handing back the same object the use case is mutating would make an
    unsaved change look persisted — which is exactly the distinction the
    outage test exists to check.
    """

    def __init__(self, *notifications: BankNotification) -> None:
        self.stored = {
            notification.idempotency_key: copy.deepcopy(notification)
            for notification in notifications
        }
        self.saves = 0

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        return self.stored.setdefault(
            notification.idempotency_key,
            copy.deepcopy(notification),
        )

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        stored = self.stored.get(idempotency_key)

        return copy.deepcopy(stored) if stored is not None else None

    def save(self, notification: BankNotification) -> None:
        self.saves += 1
        self.stored[notification.idempotency_key] = copy.deepcopy(notification)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


class StubExtractor:
    """Stands in for the model, with the port's exact contract."""

    def __init__(
        self,
        *,
        transaction: ExtractedTransaction | None = None,
        error: Exception | None = None,
    ) -> None:
        self.transaction = transaction
        self.error = error
        self.calls = 0

    def extract(
        self,
        *,
        sender: EmailAddress,
        subject: str,
        body: str,
        received_at: PosixTime,
    ) -> ExtractedTransaction | None:
        del sender, subject, body, received_at
        self.calls += 1

        if self.error is not None:
            raise self.error

        return self.transaction


def _transaction() -> ExtractedTransaction:
    return ExtractedTransaction(
        kind=TransactionKind.CARD_PURCHASE,
        direction=TransactionDirection.OUTGOING,
        amount=Money(amount=Decimal("12500"), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
        counterparty="LA TIENDA DE LA ESQUINA",
        bank="otro banco",
    )


def _notification(
    *,
    sender: str = UNKNOWN_BANK,
    raw_content: str = UNRECOGNISED,
) -> BankNotification:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress(sender),
        subject="Notificación",
        raw_content=raw_content,
        received_at=PosixTime.now(),
    )
    notification.mark_as_queued()
    notification.pull_events()

    return notification


def _message(notification: BankNotification) -> ParseNotificationMessage:
    return ParseNotificationMessage(
        notification_id=notification.id,
        user_id=notification.user_id,
        idempotency_key=notification.idempotency_key,
        message_id=notification.message_id,
        received_at=notification.received_at,
    )


def _make(
    notification: BankNotification,
    extractor: StubExtractor | None,
) -> tuple[ParseNotificationUseCase, InMemoryRepository, RecordingEventPublisher]:
    repository = InMemoryRepository(notification)
    publisher = RecordingEventPublisher()
    use_case = ParseNotificationUseCase(
        repository=repository,
        registry=ParserRegistry(),
        event_publisher=publisher,
        fallback_extractor=extractor,
    )

    return use_case, repository, publisher


def test_without_a_model_an_unrecognised_alert_is_kept_for_later() -> None:
    notification = _notification()
    use_case, repository, _ = _make(notification, None)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PENDING_FALLBACK
    assert stored.deferred_reason is NotificationDeferredReason.NO_FALLBACK_CONFIGURED


def test_the_model_reads_an_alert_no_template_matched() -> None:
    notification = _notification()
    extractor = StubExtractor(transaction=_transaction())
    use_case, repository, publisher = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK
    assert result.status is ProcessingStatus.PROCESSED
    # Downstream cannot tell it apart from a template extraction, which is the
    # point: the merchant and financial contexts see one kind of event.
    assert [type(event).__name__ for event in publisher.published] == [
        "TransactionExtracted",
    ]
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PROCESSED


def test_a_template_that_matches_never_reaches_the_model() -> None:
    notification = _notification(sender=BANK_SENDER, raw_content=RECOGNISED)
    extractor = StubExtractor(transaction=_transaction())
    use_case, _, _ = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    # Deterministic first is not a preference, it is the order the pipeline
    # depends on: cheap, repeatable, auditable.
    assert extractor.calls == 0


def test_a_known_bank_with_an_unknown_wording_still_reaches_the_model() -> None:
    # The sender has templates, but this particular alert matches none of them.
    notification = _notification(sender=BANK_SENDER, raw_content=UNRECOGNISED)
    extractor = StubExtractor(transaction=_transaction())
    use_case, _, _ = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert extractor.calls == 1
    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK


def test_a_model_that_declines_the_email_keeps_it_rather_than_failing_it() -> None:
    notification = _notification()
    use_case, repository, _ = _make(notification, StubExtractor(transaction=None))

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PENDING_FALLBACK
    assert stored.deferred_reason is NotificationDeferredReason.FALLBACK_FOUND_NOTHING


def test_a_model_outage_leaves_the_email_exactly_where_it_was() -> None:
    notification = _notification()
    extractor = StubExtractor(error=LLMTemporarilyUnavailableError("overloaded"))
    use_case, repository, publisher = _make(notification, extractor)

    with pytest.raises(LLMTemporarilyUnavailableError):
        use_case.execute(_message(notification))

    # Nothing was written, so the stored notification is still QUEUED and the
    # redelivered SQS message tries again. Recording a permanent outcome for a
    # temporary outage would quietly drop a real transaction.
    assert repository.saves == 0
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.QUEUED
    assert publisher.published == []
