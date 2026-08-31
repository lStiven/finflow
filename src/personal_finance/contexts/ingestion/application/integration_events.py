"""What ingestion publishes to the rest of the system.

This module is the context's outward contract. Nothing here is derived
automatically from a domain object: every field is listed by hand, so adding
a field to an aggregate or a value object can never silently widen what other
contexts receive.
"""

from __future__ import annotations

from personal_finance.contexts.ingestion.domain.events import (
    TransactionExtracted,
    TransferExtracted,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    ExtractedTransfer,
    Instrument,
)
from personal_finance.shared.application.integration import IntegrationEvent
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import JsonValue


SOURCE = "finflow.ingestion"

TRANSACTION_EXTRACTED = "TransactionExtracted"
TRANSACTION_EXTRACTED_VERSION = 1

# A detail type of its own rather than a second version of the one above.
# Version is for a payload whose *shape* changed; this is a different fact
# with different subscribers — Financial writes both of its sides, Merchant
# must never see it — and a rule can select on a detail type where it cannot
# select on a version. It also deploys in either order: a consumer that does
# not know this type simply never matches it, where an unknown version of a
# type it does subscribe to would sit on its queue until the dead-letter.
TRANSFER_EXTRACTED = "TransferExtracted"
TRANSFER_EXTRACTED_VERSION = 1


def _transaction_payload(transaction: ExtractedTransaction) -> dict[str, JsonValue]:
    instrument = transaction.instrument

    return {
        "kind": transaction.kind.value,
        "direction": transaction.direction.value,
        # The amount stays a string: it is a Decimal, and a JSON float would
        # round a cent away before the Financial context ever sees it.
        "amount": str(transaction.amount.amount),
        "currency": transaction.amount.currency.value,
        "occurred_at": transaction.occurred_at.as_epoch_seconds(),
        "counterparty": transaction.counterparty,
        "bank": transaction.bank,
        "instrument": (
            None
            if instrument is None
            else {"kind": instrument.kind.value, "last_four": instrument.last_four}
        ),
    }


def _instrument_payload(instrument: Instrument) -> dict[str, JsonValue]:
    return {"kind": instrument.kind.value, "last_four": instrument.last_four}


def _transfer_payload(transfer: ExtractedTransfer) -> dict[str, JsonValue]:
    return {
        "kind": transfer.kind.value,
        # A string for the same reason as above: a JSON float rounds a cent
        # away, and this one amount moves two balances.
        "amount": str(transfer.amount.amount),
        "currency": transfer.amount.currency.value,
        "occurred_at": transfer.occurred_at.as_epoch_seconds(),
        "bank": transfer.bank,
        "source": _instrument_payload(transfer.source),
        "destination": _instrument_payload(transfer.destination),
    }


class IngestionIntegrationEventTranslator:
    """Publishes the one event other contexts are waiting for.

    Everything else ingestion records — received, queued, ignored, deferred,
    failed — is its own lifecycle. Those stay inside the context: publishing
    them would let another context couple itself to how ingestion happens to
    work today.
    """

    def translate(self, event: Event) -> IntegrationEvent | None:
        if isinstance(event, TransferExtracted):
            return self._transfer(event)

        if not isinstance(event, TransactionExtracted):
            return None

        payload: dict[str, JsonValue] = {
            "notification_id": str(event.notification_id.value),
            "user_id": str(event.user_id.value),
            "transaction": _transaction_payload(event.transaction),
        }

        # Deliberately absent: `message_id` and `sender`. They identify the
        # email, not the movement of money, and no downstream context should
        # be reasoning about a customer's mailbox.
        #
        # Deliberately never logged either: this payload is a line of
        # somebody's spending history — amount, counterparty, bank and card
        # digits together. `LoggingEventPublisher` records that the event
        # happened, by type and id, which is what an audit trail needs.
        return IntegrationEvent(
            source=SOURCE,
            detail_type=TRANSACTION_EXTRACTED,
            version=TRANSACTION_EXTRACTED_VERSION,
            event_id=event.event_id,
            payload=payload,
            occurred_at=event.occurred_at,
        )

    def _transfer(self, event: TransferExtracted) -> IntegrationEvent:
        """Both sides of one email, as one event.

        One event rather than two `TransactionExtracted`s, because the two
        sides have to be written by something that knows they belong
        together: published apart, a redelivery could apply one and not the
        other, and nothing downstream could tell that the pair was a pair.
        """
        return IntegrationEvent(
            source=SOURCE,
            detail_type=TRANSFER_EXTRACTED,
            version=TRANSFER_EXTRACTED_VERSION,
            event_id=event.event_id,
            payload={
                "notification_id": str(event.notification_id.value),
                "user_id": str(event.user_id.value),
                "transfer": _transfer_payload(event.transfer),
            },
            occurred_at=event.occurred_at,
        )
