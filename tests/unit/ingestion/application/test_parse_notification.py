from collections.abc import Sequence

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
    ParseOutcome,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.events import TransactionExtracted
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.transactions import TransactionKind
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    NotificationIgnoredReason,
    ProcessingStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
BANK_SENDER = "alertasynotificaciones@an.notificacionesbancolombia.com"
PURCHASE = (
    "Bancolombia: Compraste COP29.259,00 en TIENDAS ARA con tu T.Cred *7653, "
    "el 20/08/2026 a las 12:00. Estamos cerca."
)


class InMemoryRepository:
    def __init__(self, *notifications: BankNotification) -> None:
        self.saved = {
            notification.idempotency_key: notification for notification in notifications
        }

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        return self.saved.setdefault(notification.idempotency_key, notification)

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        return self.saved.get(idempotency_key)

    def save(self, notification: BankNotification) -> None:
        self.saved[notification.idempotency_key] = notification


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _notification(
    *,
    sender: str = BANK_SENDER,
    raw_content: str = PURCHASE,
    queued: bool = True,
) -> BankNotification:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress(sender),
        subject="Alertas y Notificaciones",
        raw_content=raw_content,
        received_at=PosixTime.now(),
    )

    if queued:
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
    notification: BankNotification | None,
) -> tuple[ParseNotificationUseCase, InMemoryRepository, RecordingEventPublisher]:
    repository = (
        InMemoryRepository(notification)
        if notification is not None
        else InMemoryRepository()
    )
    publisher = RecordingEventPublisher()
    use_case = ParseNotificationUseCase(
        repository=repository,
        registry=ParserRegistry(),
        event_publisher=publisher,
    )

    return use_case, repository, publisher


def _published_types(publisher: RecordingEventPublisher) -> list[str]:
    return [type(event).__name__ for event in publisher.published]


def test_a_recognised_alert_becomes_an_extracted_transaction() -> None:
    notification = _notification()
    use_case, repository, publisher = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    assert result.status is ProcessingStatus.PROCESSED
    assert _published_types(publisher) == ["TransactionExtracted"]

    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PROCESSED


def test_the_extracted_transaction_carries_the_parsed_values() -> None:
    notification = _notification()
    use_case, _, publisher = _make(notification)

    use_case.execute(_message(notification))

    event = publisher.published[0]
    assert isinstance(event, TransactionExtracted)
    transaction = event.transaction
    assert transaction.kind is TransactionKind.CARD_PURCHASE
    assert transaction.counterparty == "TIENDAS ARA"
    assert str(transaction.amount.amount) == "29259.00"


def test_the_email_body_is_dropped_once_extracted() -> None:
    notification = _notification()
    use_case, repository, _ = _make(notification)

    use_case.execute(_message(notification))

    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    # The extraction is done; keeping the raw bank email around any longer
    # serves nothing.
    assert stored.raw_content == ""


def test_an_unknown_bank_is_deferred_to_the_fallback() -> None:
    notification = _notification(sender="alerts@some-other-bank.com")
    use_case, repository, publisher = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    assert result.status is ProcessingStatus.PENDING_FALLBACK
    assert _published_types(publisher) == ["TransactionExtractionDeferred"]

    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    # The body survives: the LLM fallback still needs to read it.
    assert stored.raw_content != ""
    assert stored.deferred_reason is NotificationDeferredReason.NO_FALLBACK_CONFIGURED


def test_an_unrecognised_template_from_a_known_bank_is_deferred() -> None:
    notification = _notification(
        raw_content="Bancolombia: Tu clave fue actualizada el 20/08/2026.",
    )
    use_case, _, _ = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED


def test_a_malformed_field_fails_instead_of_guessing() -> None:
    notification = _notification(
        raw_content=(
            "Bancolombia: Compraste COP29.2.5 en TIENDAS ARA con tu T.Cred "
            "*7653, el 20/08/2026 a las 12:00."
        ),
    )
    use_case, _, publisher = _make(notification)

    result = use_case.execute(_message(notification))

    # The template matched but the amount is unreadable. A wrong number is
    # worse than a failure, because a failure is visible.
    assert result.outcome is ParseOutcome.FAILED
    assert result.status is ProcessingStatus.FAILED
    assert _published_types(publisher) == ["BankNotificationFailed"]


def test_redelivery_of_an_already_parsed_message_is_a_no_op() -> None:
    notification = _notification()
    use_case, _, publisher = _make(notification)

    first = use_case.execute(_message(notification))
    second = use_case.execute(_message(notification))

    assert first.outcome is ParseOutcome.EXTRACTED
    # SQS is at-least-once. Re-running would republish TransactionExtracted and
    # the downstream contexts would book the purchase twice.
    assert second.outcome is ParseOutcome.SKIPPED
    assert _published_types(publisher) == ["TransactionExtracted"]


def test_an_ignored_notification_is_never_parsed() -> None:
    notification = _notification(queued=False)
    notification.ignore(reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER)
    notification.pull_events()
    use_case, _, publisher = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.SKIPPED
    assert publisher.published == []


def test_a_message_for_a_vanished_notification_is_skipped() -> None:
    notification = _notification()
    use_case, _, publisher = _make(None)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.SKIPPED
    assert result.status is None
    assert publisher.published == []
