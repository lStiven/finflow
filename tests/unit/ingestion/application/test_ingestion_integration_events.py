from decimal import Decimal

from personal_finance.contexts.ingestion.application.integration_events import (
    SOURCE,
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationFailed,
    BankNotificationIgnored,
    BankNotificationQueued,
    BankNotificationReceived,
    TransactionExtracted,
    TransactionExtractionDeferred,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationId,
    NotificationIgnoredReason,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MESSAGE_ID = EmailMessageId("<abc@bancolombia.com.co>")
NOTIFICATION_ID = NotificationId.for_message(user_id=USER_ID, message_id=MESSAGE_ID)
OCCURRED_AT = PosixTime.from_epoch_seconds(1_700_000_000)


def _transaction(*, instrument: Instrument | None = None) -> ExtractedTransaction:
    return ExtractedTransaction(
        kind=TransactionKind.CARD_PURCHASE,
        direction=TransactionDirection.OUTGOING,
        amount=Money(amount=Decimal("45000.50"), currency=Currency.COP),
        occurred_at=OCCURRED_AT,
        counterparty="EXITO CALI",
        instrument=instrument,
    )


def _extracted(**kwargs: object) -> TransactionExtracted:
    return TransactionExtracted(
        notification_id=NOTIFICATION_ID,
        user_id=USER_ID,
        message_id=MESSAGE_ID,
        transaction=kwargs.get("transaction") or _transaction(),  # pyright: ignore[reportArgumentType]
    )


def test_transaction_extracted_becomes_a_public_event() -> None:
    translator = IngestionIntegrationEventTranslator()

    event = translator.translate(_extracted())

    assert event is not None
    assert event.source == SOURCE
    assert event.detail_type == "TransactionExtracted"
    assert event.version == 1


def test_the_payload_carries_what_downstream_contexts_need() -> None:
    translator = IngestionIntegrationEventTranslator()
    instrument = Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="1234")

    event = translator.translate(
        _extracted(transaction=_transaction(instrument=instrument))
    )

    assert event is not None
    assert event.payload == {
        "notification_id": str(NOTIFICATION_ID.value),
        "user_id": str(USER_ID.value),
        "transaction": {
            "kind": "card_purchase",
            "direction": "outgoing",
            "amount": "45000.50",
            "currency": "COP",
            "occurred_at": 1_700_000_000,
            "counterparty": "EXITO CALI",
            "instrument": {"kind": "credit_card", "last_four": "1234"},
        },
    }


def test_the_amount_stays_a_string_so_no_cent_is_rounded_away() -> None:
    translator = IngestionIntegrationEventTranslator()

    event = translator.translate(_extracted())

    assert event is not None
    transaction = event.payload["transaction"]
    assert isinstance(transaction, dict)
    # A JSON float would lose precision before Financial ever reads it.
    assert transaction["amount"] == "45000.50"
    assert isinstance(transaction["amount"], str)


def test_a_transaction_without_an_instrument_publishes_null() -> None:
    translator = IngestionIntegrationEventTranslator()

    event = translator.translate(_extracted(transaction=_transaction(instrument=None)))

    assert event is not None
    transaction = event.payload["transaction"]
    assert isinstance(transaction, dict)
    assert transaction["instrument"] is None


def test_the_email_identity_never_leaves_the_context() -> None:
    translator = IngestionIntegrationEventTranslator()

    event = translator.translate(_extracted())

    assert event is not None
    # The message id and the sender identify the mailbox, not the movement of
    # money: no downstream context should reason about a customer's email.
    serialized = str(event.payload)
    assert "message_id" not in event.payload
    assert "sender" not in event.payload
    assert MESSAGE_ID.value not in serialized


def test_the_event_id_is_carried_through_for_deduplication() -> None:
    translator = IngestionIntegrationEventTranslator()
    domain_event = _extracted()

    event = translator.translate(domain_event)

    assert event is not None
    assert event.event_id == domain_event.event_id
    assert event.occurred_at == domain_event.occurred_at


def test_ingestions_internal_lifecycle_events_are_not_published() -> None:
    translator = IngestionIntegrationEventTranslator()
    internal = [
        BankNotificationReceived(
            notification_id=NOTIFICATION_ID,
            user_id=USER_ID,
            message_id=MESSAGE_ID,
            sender=EmailAddress("alertas@bancolombia.com.co"),
            received_at=OCCURRED_AT,
        ),
        BankNotificationQueued(
            notification_id=NOTIFICATION_ID,
            user_id=USER_ID,
            message_id=MESSAGE_ID,
        ),
        BankNotificationIgnored(
            notification_id=NOTIFICATION_ID,
            user_id=USER_ID,
            message_id=MESSAGE_ID,
            sender=EmailAddress("phisher@evil.com"),
            reason=NotificationIgnoredReason.UNAUTHORIZED_SENDER,
        ),
        TransactionExtractionDeferred(
            notification_id=NOTIFICATION_ID,
            user_id=USER_ID,
            message_id=MESSAGE_ID,
            sender=EmailAddress("alertas@bancolombia.com.co"),
        ),
        BankNotificationFailed(
            notification_id=NOTIFICATION_ID,
            user_id=USER_ID,
            message_id=MESSAGE_ID,
            reason="unreadable amount",
        ),
    ]

    # These describe how ingestion happens to work; publishing them would let
    # another context couple itself to that.
    assert [translator.translate(event) for event in internal] == [None] * len(internal)
