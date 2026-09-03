"""Parsing an alert somebody forwarded by hand.

The intake model is that people forward their bank's mail, and there are two
ways that happens. A Gmail rule forwards the message and keeps the bank as its
sender; a person pressing *forward* sends it from their own address and the
bank survives only in the header block the client writes into the body.

Only the first was ever exercised, so every hand-forward went straight past
the templates that could read it and into the fallback — which then refused,
correctly, because a card payment names two of the owner's own instruments and
only a deterministic template may read those. Real emails, all three of them
`pending_fallback` in the development table, cost nothing else: they were kept.
"""

from collections.abc import Sequence
from decimal import Decimal

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
    ParseOutcome,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.events import TransferExtracted
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
    TransferKind,
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


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")

# The address the owner forwards from, and one they approved for themselves.
OWN_ADDRESS = "stiven.ddh@gmail.com"
BANK_SENDER = "alertasynotificaciones@an.notificacionesbancolombia.com"

# Trimmed from the real message, and kept in the shape Gmail produced: the
# marker in English, the field in Spanish, the sentence wrapped mid-phrase.
FORWARDED_CARD_PAYMENT = (
    "---------- Forwarded message ---------\r\n"
    "De: <alertasynotificaciones@bancolombia.com.co>\r\n"
    "Date: mié, 2 sept 2026 a la(s) 4:14 p.m.\r\n"
    "Subject: Alertas y Notificaciones\r\n"
    "To: <STIVEN.DDH@gmail.com>\r\n"
    "\r\n"
    "Bancolombia: Pagaste $2,724,006 en la tarjeta de credito *7653 desde la\r\n"
    "cuenta *5261, el 02/09/2026 16:14. ¿Dudas? Llamanos al 018000912345.\r\n"
    "\r\n"
    "Si recibes un correo sospechoso repórtalo a "
    "correosospechoso@bancolombia.com.co\r\n"
)

FORWARDED_PURCHASE = (
    "---------- Forwarded message ---------\r\n"
    "De: <alertasynotificaciones@bancolombia.com.co>\r\n"
    "Subject: Alertas y Notificaciones\r\n"
    "\r\n"
    "Bancolombia: Compraste $67,400 en CREPES & WAFFLES 45 con tu T.Cred "
    "*7653, el 29/08/2026 20:10.\r\n"
)

# A forward of something no parser knows: the header is read, the bank is not
# one of ours, and the email carries on to the fallback as before.
FORWARDED_FROM_ANOTHER_BANK = (
    "---------- Forwarded message ---------\r\n"
    "De: <alertas@otro-banco.com>\r\n"
    "\r\n"
    "Otro Banco: te informamos de un movimiento por $10.000.\r\n"
)

NOT_A_FORWARD = "Un correo cualquiera que nadie sabe leer."


class InMemoryRepository:
    def __init__(self, notification: BankNotification) -> None:
        self.saved = {notification.idempotency_key: notification}

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


class StubExtractor:
    """Stands in for the model, and counts whether it was asked at all."""

    def __init__(self) -> None:
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

        return ExtractedTransaction(
            kind=TransactionKind.CARD_PURCHASE,
            direction=TransactionDirection.OUTGOING,
            amount=Money(amount=Decimal("1"), currency=Currency.COP),
            occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
            counterparty="LO QUE SEA",
            bank="otro banco",
        )


def _notification(*, sender: str, raw_content: str) -> BankNotification:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId("message-1"),
        sender=EmailAddress(sender),
        subject="Fwd: Alertas y Notificaciones",
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
) -> tuple[ParseNotificationUseCase, RecordingEventPublisher, StubExtractor]:
    publisher = RecordingEventPublisher()
    extractor = StubExtractor()
    use_case = ParseNotificationUseCase(
        repository=InMemoryRepository(notification),
        registry=ParserRegistry(),
        event_publisher=publisher,
        fallback_extractor=extractor,
    )

    return use_case, publisher, extractor


def test_a_hand_forwarded_card_payment_is_read_as_a_transfer() -> None:
    notification = _notification(
        sender=OWN_ADDRESS,
        raw_content=FORWARDED_CARD_PAYMENT,
    )
    use_case, publisher, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    assert result.status is ProcessingStatus.PROCESSED
    # The template answers, so the model is never asked — and could not have
    # answered this anyway.
    assert extractor.calls == 0

    (event,) = publisher.published
    assert isinstance(event, TransferExtracted)


def test_the_transfer_names_both_of_the_owners_instruments() -> None:
    notification = _notification(
        sender=OWN_ADDRESS,
        raw_content=FORWARDED_CARD_PAYMENT,
    )
    use_case, publisher, _ = _make(notification)

    use_case.execute(_message(notification))

    (event,) = publisher.published
    assert isinstance(event, TransferExtracted)

    transfer = event.transfer
    assert transfer.kind is TransferKind.CARD_PAYMENT
    assert transfer.amount == Money(amount=Decimal("2724006"), currency=Currency.COP)
    # The money leaves the savings account and lands on the card, lowering
    # what it owes. Read the other way round it would raise the debt it paid.
    assert transfer.source.kind is InstrumentKind.ACCOUNT
    assert transfer.source.last_four == "5261"
    assert transfer.destination.kind is InstrumentKind.CREDIT_CARD
    assert transfer.destination.last_four == "7653"


def test_an_ordinary_forwarded_purchase_is_read_the_same_way() -> None:
    notification = _notification(sender=OWN_ADDRESS, raw_content=FORWARDED_PURCHASE)
    use_case, publisher, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    assert extractor.calls == 0
    assert [type(event).__name__ for event in publisher.published] == [
        "TransactionExtracted",
    ]


def test_the_bank_delivering_its_own_alert_is_untouched_by_any_of_this() -> None:
    # The path fifteen of this user's sixteen automatic forwards took, and the
    # one that must not move: the sender names the bank, and the forwarded
    # header is never consulted.
    notification = _notification(
        sender=BANK_SENDER,
        raw_content=(
            "Bancolombia: Compraste $67,400 en CREPES & WAFFLES 45 con tu "
            "T.Cred *7653, el 29/08/2026 20:10."
        ),
    )
    use_case, publisher, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    assert extractor.calls == 0
    assert [type(event).__name__ for event in publisher.published] == [
        "TransactionExtracted",
    ]


def test_a_forward_from_a_bank_nobody_parses_still_reaches_the_fallback() -> None:
    notification = _notification(
        sender=OWN_ADDRESS,
        raw_content=FORWARDED_FROM_ANOTHER_BANK,
    )
    use_case, _, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert extractor.calls == 1
    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK


def test_an_email_that_is_not_a_forward_reaches_the_fallback_as_before() -> None:
    notification = _notification(sender=OWN_ADDRESS, raw_content=NOT_A_FORWARD)
    use_case, _, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert extractor.calls == 1
    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK


def test_a_forwarded_alert_the_bank_has_no_template_for_reaches_the_fallback() -> None:
    # The header names Bancolombia, so its templates are tried — and none of
    # them match. That is the fallback's case, exactly as it is for an alert
    # the bank itself delivered.
    notification = _notification(
        sender=OWN_ADDRESS,
        raw_content=(
            "---------- Forwarded message ---------\r\n"
            "De: <alertasynotificaciones@bancolombia.com.co>\r\n"
            "\r\n"
            "Bancolombia: te contamos algo que ninguna plantilla describe.\r\n"
        ),
    )
    use_case, _, extractor = _make(notification)

    result = use_case.execute(_message(notification))

    assert extractor.calls == 1
    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK


def test_without_a_model_a_forward_nobody_parses_is_still_kept() -> None:
    notification = _notification(sender=OWN_ADDRESS, raw_content=NOT_A_FORWARD)
    publisher = RecordingEventPublisher()
    use_case = ParseNotificationUseCase(
        repository=InMemoryRepository(notification),
        registry=ParserRegistry(),
        event_publisher=publisher,
    )

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    assert result.status is ProcessingStatus.PENDING_FALLBACK
    assert (
        notification.deferred_reason
        is NotificationDeferredReason.NO_FALLBACK_CONFIGURED
    )
