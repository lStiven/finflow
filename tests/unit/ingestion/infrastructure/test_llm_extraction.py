"""Turning a model's answer into a transaction, or refusing to.

The schema is the boundary between a language model and this system's money.
These check the half the schema cannot: that an answer which satisfies the
shape but not the rules is dropped rather than believed.
"""

from typing import cast

from pydantic import BaseModel
import pytest

from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.llm.transaction_extractor import (  # noqa: E501
    NO_INSTRUMENT,
    ExtractedDirection,
    ExtractedInstrument,
    ExtractedKind,
    ExtractedTransactionSchema,
    GeminiTransactionExtractor,
)
from personal_finance.shared.domain.value_objects import Currency, PosixTime
from personal_finance.shared.infrastructure.config.settings import LLMSettings
from personal_finance.shared.infrastructure.llm.errors import (
    LLMTemporarilyUnavailableError,
    LLMUnusableResponseError,
)
from personal_finance.shared.infrastructure.llm.gemini import StructuredModel


SENDER = EmailAddress("alertas@otrobanco.com.co")
RECEIVED_AT = PosixTime.from_epoch_seconds(1_755_000_000)


def _literal_values(annotation: object) -> set[str]:
    """The members of one of the module's Literal aliases."""
    return set(annotation.__value__.__args__)  # type: ignore[attr-defined]


class StubModel:
    def __init__(
        self,
        *,
        answer: ExtractedTransactionSchema | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    def complete[SchemaT: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        del system_instruction, schema
        self.prompts.append(prompt)

        if self.error is not None:
            raise self.error

        return cast("SchemaT", self.answer)


def _answer(**overrides: object) -> ExtractedTransactionSchema:
    values: dict[str, object] = {
        "understood": True,
        "kind": "card_purchase",
        "direction": "outgoing",
        "amount": "29259.50",
        "currency": "COP",
        "occurred_at_local": "2026-08-20 12:00",
        "counterparty": "TIENDAS ARA 123",
        "bank": "Otro Banco",
        "instrument_kind": "credit_card",
        "instrument_last_four": "7653",
    }
    values.update(overrides)

    return ExtractedTransactionSchema.model_validate(values)


def _extract(
    answer: ExtractedTransactionSchema | None = None,
    *,
    error: Exception | None = None,
) -> tuple[ExtractedTransaction | None, StubModel]:
    model = StubModel(answer=answer, error=error)
    extractor = GeminiTransactionExtractor(
        model=cast("StructuredModel", model),
        settings=LLMSettings(),
    )

    return (
        extractor.extract(
            sender=SENDER,
            subject="Notificación",
            body="Tu compra fue aprobada.",
            received_at=RECEIVED_AT,
        ),
        model,
    )


def test_the_schema_offers_exactly_the_kinds_the_domain_has() -> None:
    # Spelled out for the JSON schema, so nothing stops the two drifting
    # apart except this.
    assert _literal_values(ExtractedKind) == {kind.value for kind in TransactionKind}


def test_the_schema_offers_exactly_the_directions_the_domain_has() -> None:
    assert _literal_values(ExtractedDirection) == {
        direction.value for direction in TransactionDirection
    }


def test_the_schema_offers_every_instrument_the_domain_has_plus_none() -> None:
    assert _literal_values(ExtractedInstrument) == {
        kind.value for kind in InstrumentKind
    } | {NO_INSTRUMENT}


def test_a_read_alert_becomes_a_transaction() -> None:
    transaction, _ = _extract(_answer())

    assert transaction is not None
    assert transaction.kind is TransactionKind.CARD_PURCHASE
    assert transaction.direction is TransactionDirection.OUTGOING
    assert str(transaction.amount.amount) == "29259.50"
    assert transaction.amount.currency is Currency.COP
    assert transaction.counterparty == "TIENDAS ARA 123"
    assert transaction.bank == "Otro Banco"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.CREDIT_CARD
    assert transaction.instrument.last_four == "7653"


def test_the_local_wall_clock_is_read_in_bogota() -> None:
    transaction, _ = _extract(_answer(occurred_at_local="2026-08-20 12:00"))

    assert transaction is not None
    # 12:00 in Bogotá is 17:00 UTC. Reading it as UTC would move every
    # late-evening purchase into the next day.
    assert transaction.occurred_at.to_datetime().hour == 17


def test_a_declined_email_produces_nothing() -> None:
    transaction, _ = _extract(_answer(understood=False))

    assert transaction is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        # Anything the domain would refuse is treated as "could not read it".
        ("amount", "about forty thousand"),
        ("amount", "-100"),
        ("occurred_at_local", "yesterday"),
        ("counterparty", "   "),
        ("bank", "   "),
    ],
)
def test_an_answer_the_domain_refuses_is_not_a_transaction(
    field: str,
    value: str,
) -> None:
    transaction, _ = _extract(_answer(**{field: value}))

    assert transaction is None


def test_an_instrument_the_email_did_not_name_is_simply_absent() -> None:
    transaction, _ = _extract(
        _answer(instrument_kind=NO_INSTRUMENT, instrument_last_four=""),
    )

    assert transaction is not None
    assert transaction.instrument is None


def test_a_masked_marker_that_is_not_digits_is_dropped_not_fatal() -> None:
    transaction, _ = _extract(_answer(instrument_last_four="****"))

    assert transaction is not None
    assert transaction.instrument is not None
    # Losing the card's last four is better than losing the transaction.
    assert transaction.instrument.last_four is None


def test_an_answer_that_is_not_the_schema_is_dropped() -> None:
    transaction, _ = _extract(error=LLMUnusableResponseError("not json"))

    assert transaction is None


def test_an_outage_is_raised_so_the_email_can_be_tried_again() -> None:
    model = StubModel(error=LLMTemporarilyUnavailableError("overloaded"))
    extractor = GeminiTransactionExtractor(
        model=cast("StructuredModel", model),
        settings=LLMSettings(),
    )

    with pytest.raises(LLMTemporarilyUnavailableError):
        extractor.extract(
            sender=SENDER,
            subject="Notificación",
            body="Tu compra fue aprobada.",
            received_at=RECEIVED_AT,
        )


def test_the_email_is_wrapped_in_markers_the_instruction_names() -> None:
    _, model = _extract(_answer())

    prompt = model.prompts[0]
    # The body is delimited so the system instruction can say, credibly, that
    # everything inside is data rather than orders.
    assert "BEGIN EMAIL" in prompt
    assert "END EMAIL" in prompt
    assert "Tu compra fue aprobada." in prompt


def test_the_prompt_carries_the_received_date_as_local_context() -> None:
    _, model = _extract(_answer())

    # An alert that writes a date with no year is resolved against this.
    assert "received_at: 2025-08-12" in model.prompts[0]
