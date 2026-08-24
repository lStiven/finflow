"""Reading `finflow.ingestion` off the bus, at Financial's own boundary.

Everything here treats the payload as untrusted text: it crossed a queue as
JSON, and the fields inside it were read out of somebody's email — in part by
an LLM. Pydantic decides the shape, and `RecordMovementCommand` is where it
stops being strings.

Ingestion's enums and value objects are never imported. The two contexts
happen to spell `outgoing` the same way today, and neither gets a veto over
the other renaming its own members tomorrow.
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from personal_finance.contexts.financial.application.commands import (
    RecordMovementCommand,
)
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


INGESTION_SOURCE = "finflow.ingestion"
TRANSACTION_EXTRACTED = "TransactionExtracted"
SUPPORTED_VERSION = 1

# Plain decimal notation only. Rejects a sign, `NaN`, `Infinity` and — the one
# that matters — exponent notation: `1E+1000000` is ten harmless-looking
# characters that expand into a million-digit amount, and `Decimal.normalize`
# raises `Overflow` on it rather than the `ValueError` every caller guards for.
# (`$` is end-of-text here: Pydantic v2 compiles this with Rust's regex
# engine, which has no multi-line mode switched on.)
AMOUNT_PATTERN = r"^\d+(\.\d+)?$"

# Epoch seconds this side of the year 10000. Unbounded, the conversion raises
# `OSError` or an out-of-range `ValueError` from deep inside `datetime`, which
# escapes the worker's guard and leaves the message redelivering until the
# dead-letter queue takes it.
MIN_OCCURRED_AT = 0
MAX_OCCURRED_AT = 253_402_300_799


class UnsupportedPayloadVersionError(Exception):
    """Raised for a `TransactionExtracted` version this cannot read.

    Not a malformed payload: a newer deploy wrote it, and a newer worker may
    still be able to take it. The worker leaves such a message on the queue
    rather than discarding it.
    """


def _required_text(value: str) -> str:
    """Non-blank once stripped, which `min_length` alone does not give.

    A field of spaces satisfies `min_length=1` and then raises deep in the
    domain, past the point where the worker can still call the payload
    malformed and drop it.
    """
    stripped = value.strip()

    if not stripped:
        raise ValueError("Value cannot be blank")

    return stripped


class ExtractedTransactionBody(BaseModel):
    """The movement itself, as ingestion serialized it."""

    direction: str = Field(min_length=1, max_length=64)
    # A string, never a JSON number: this is money, and a float loses cents
    # somewhere past the second decimal without ever raising. The pattern is
    # what makes `Decimal(...)` in `to_command` safe without a second parse.
    amount: str = Field(pattern=AMOUNT_PATTERN, max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    occurred_at: int = Field(ge=MIN_OCCURRED_AT, le=MAX_OCCURRED_AT)
    counterparty: str = Field(min_length=1, max_length=512)
    bank: str = Field(min_length=1, max_length=256)
    instrument: InstrumentBody | None = None

    @field_validator("counterparty", "bank")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _required_text(value)


class InstrumentBody(BaseModel):
    kind: str = Field(default="", max_length=64)
    last_four: str | None = Field(default=None, max_length=32)


class TransactionExtractedDetail(BaseModel):
    version: int = Field(ge=1)
    event_id: uuid.UUID
    user_id: uuid.UUID
    transaction: ExtractedTransactionBody

    def to_command(self) -> RecordMovementCommand:
        """Read the payload into Financial's vocabulary.

        Raises `UnsupportedPayloadVersionError` for a version this cannot
        read: `to_command` is what assigns v1 meaning to these fields, so
        reading a v2 payload here would book whatever v2 changed — an amount
        in minor units, say — straight onto a real balance.

        Raises `ValueError` on a direction or currency this cannot read.
        Neither has a safe default: a guessed direction moves a real balance
        the wrong way, and a guessed currency mixes two of them into one
        number. An unreadable *instrument*, by contrast, is not refused here —
        it costs the movement its routing and nothing more, which
        `Transaction.from_alert` decides.
        """
        if self.version != SUPPORTED_VERSION:
            raise UnsupportedPayloadVersionError(
                f"Cannot read TransactionExtracted version {self.version}",
            )

        movement = self.transaction
        instrument = movement.instrument

        return RecordMovementCommand(
            user_id=UserId(value=self.user_id),
            bank=movement.bank,
            direction=MovementDirection.from_alert(movement.direction),
            amount=Money(
                amount=Decimal(movement.amount),
                currency=_currency(movement.currency),
            ),
            occurred_at=PosixTime.from_epoch_seconds(movement.occurred_at),
            counterparty=movement.counterparty,
            instrument_kind=None if instrument is None else instrument.kind,
            last_four=None if instrument is None else instrument.last_four,
        )


class IntegrationEventEnvelope(BaseModel):
    """What EventBridge puts on the queue.

    Only the routing fields are read here; the payload is validated separately,
    once we know what it claims to be.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(default="", max_length=256)
    detail_type: str = Field(default="", alias="detail-type", max_length=256)
    detail: dict[str, object] = Field(default_factory=lambda: dict[str, object]())


def _currency(value: str) -> Currency:
    try:
        return Currency(value.strip().upper())
    except ValueError as error:
        raise ValueError(f"Unknown currency: {value!r}") from error
