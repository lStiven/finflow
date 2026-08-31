from decimal import Decimal

from personal_finance.contexts.ingestion.application.integration_events import (
    SOURCE,
    TRANSFER_EXTRACTED,
    IngestionIntegrationEventTranslator,
)
from personal_finance.contexts.ingestion.domain.events import (
    BankNotificationFailed,
    BankNotificationIgnored,
    BankNotificationQueued,
    BankNotificationReceived,
    TransactionExtracted,
    TransactionExtractionDeferred,
    TransferExtracted,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    ExtractedTransfer,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
    TransferKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
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
        bank="bancolombia",
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
            "bank": "bancolombia",
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
            reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
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


# --------------------------------------------------------------- traslados


def _transfer() -> ExtractedTransfer:
    return ExtractedTransfer(
        kind=TransferKind.CARD_PAYMENT,
        amount=Money(amount=Decimal("3540258"), currency=Currency.COP),
        occurred_at=OCCURRED_AT,
        bank="bancolombia",
        source=Instrument(kind=InstrumentKind.ACCOUNT, last_four="5261"),
        destination=Instrument(kind=InstrumentKind.CREDIT_CARD, last_four="7653"),
    )


def _transfer_event() -> TransferExtracted:
    return TransferExtracted(
        notification_id=NOTIFICATION_ID,
        user_id=USER_ID,
        message_id=MESSAGE_ID,
        transfer=_transfer(),
    )


def test_a_transfer_is_published_under_its_own_detail_type() -> None:
    """Not a version of `TransactionExtracted`: Merchant subscribes to that
    one and must never see a card payment, and a rule can select on a detail
    type where it cannot select on a version."""
    event = IngestionIntegrationEventTranslator().translate(_transfer_event())

    assert event is not None
    assert event.source == SOURCE
    assert event.detail_type == TRANSFER_EXTRACTED
    assert event.version == 1


def test_a_transfer_payload_carries_both_instruments() -> None:
    event = IngestionIntegrationEventTranslator().translate(_transfer_event())

    assert event is not None
    transfer = event.payload["transfer"]
    assert transfer == {
        "kind": "card_payment",
        "amount": "3540258",
        "currency": "COP",
        "occurred_at": OCCURRED_AT.as_epoch_seconds(),
        "bank": "bancolombia",
        "source": {"kind": "account", "last_four": "5261"},
        "destination": {"kind": "credit_card", "last_four": "7653"},
    }


def test_a_transfer_amount_stays_a_string() -> None:
    """One amount, two balances: a float that rounds a cent away here would
    round it away twice."""
    event = IngestionIntegrationEventTranslator().translate(_transfer_event())

    assert event is not None
    transfer = event.payload["transfer"]
    assert isinstance(transfer, dict)
    assert isinstance(transfer["amount"], str)


def test_a_transfer_keeps_the_email_out_of_the_payload() -> None:
    event = IngestionIntegrationEventTranslator().translate(_transfer_event())

    assert event is not None
    assert "message_id" not in event.payload
    assert "sender" not in event.payload
