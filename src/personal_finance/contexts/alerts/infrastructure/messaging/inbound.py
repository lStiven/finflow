"""Reading `finflow.financial` off the bus, at Alerts' own boundary.

Everything here is untrusted: it crossed a queue as JSON, and the fields
inside it were read out of somebody's bank email — in part by a language
model. Pydantic decides the shape, and `to_command` is where it stops being
strings.

Financial's enums and value objects are never imported. The two contexts
happen to spell `outgoing` the same way today, and neither gets a veto over
the other renaming its own members tomorrow. This is the same duplication
Financial and Merchant already keep against Ingestion.

**`MovementRecorded`'s payload is flat, and the transport flattens its own
envelope fields into the same object.** So `version`, `event_id` and
`occurred_at` arrive as siblings of the business fields — and `occurred_at`
is the envelope's, meaning "when the fact was recorded". When the money
actually moved is `movement_occurred_at`, which is why Financial named it
that. An alert built on the wrong one says "acabas de gastar" about a
purchase from three days ago, because a bank alert can arrive that late.
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from personal_finance.contexts.alerts.application.commands import (
    DeliverMovementAlertCommand,
)
from personal_finance.contexts.alerts.application.messages import (
    MovementAlert,
    MovementDirection,
    MovementOrigin,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


FINANCIAL_SOURCE = "finflow.financial"
MOVEMENT_RECORDED = "MovementRecorded"
SUPPORTED_VERSION = 1

# Plain decimal notation only. Rejects a sign, `NaN`, `Infinity` and — the one
# that matters — exponent notation: `1E+1000000` is ten harmless-looking
# characters that expand into a million-digit amount.
AMOUNT_PATTERN = r"^\d+(\.\d+)?$"

# Epoch seconds this side of the year 10000. Unbounded, the conversion raises
# `OSError` or an out-of-range `ValueError` from deep inside `datetime`.
MIN_OCCURRED_AT = 0
MAX_OCCURRED_AT = 253_402_300_799


class UnsupportedPayloadVersionError(Exception):
    """Raised for a `MovementRecorded` version this cannot read.

    Not a malformed payload: a newer deploy wrote it, and a newer worker may
    still be able to take it. The worker leaves such a message on the queue.
    """


def refused_fields(error: ValidationError) -> str:
    """Which fields a payload was refused for — the names, never the values.

    The whole of what a reader needs to fix a producer that changed shape,
    and none of what they must not be shown. A `ValidationError` renders
    `input_value=` for every field it refused, and here those fields are an
    amount, a counterparty and a bank: one line of somebody's spending
    history, in CloudWatch, for the log group's retention. So the message and
    the input are both dropped and only `loc` and `type` survive —
    `bank:string_too_short`, which is a name and a stable Pydantic code.

    Nothing attacker-controlled reaches this: `loc` holds declared field
    names, and both models here ignore what they did not declare.

    This exists because its absence cost a day. A movement entered by hand
    carries no bank, the model demanded one, and every one of them was
    discarded behind "discarding malformed MovementRecorded payload" — true,
    unactionable, and indistinguishable from any other refusal.
    """
    return ", ".join(
        f"{'.'.join(str(part) for part in item['loc']) or '(root)'}:{item['type']}"
        for item in error.errors()
    )


def _required_text(value: str) -> str:
    stripped = value.strip()

    if not stripped:
        raise ValueError("Value cannot be blank")

    return stripped


class IntegrationEventEnvelope(BaseModel):
    """What EventBridge puts on the queue.

    Only the routing fields are read here; the payload is validated
    separately, once we know what it claims to be.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(default="", max_length=256)
    detail_type: str = Field(default="", alias="detail-type", max_length=256)
    detail: dict[str, object] = Field(default_factory=lambda: dict[str, object]())


class MovementRecordedDetail(BaseModel):
    """Financial's flat payload, plus the envelope fields mixed in with it."""

    model_config = ConfigDict(extra="ignore")

    version: int = Field(ge=1)
    event_id: uuid.UUID
    user_id: uuid.UUID
    direction: str = Field(min_length=1, max_length=64)
    # A string, never a JSON number: this is money, and a float loses cents
    # without ever raising.
    amount: str = Field(pattern=AMOUNT_PATTERN, max_length=64)
    currency: str = Field(min_length=1, max_length=8)
    # When the money moved. Not `occurred_at`, which the transport overwrites
    # with when the fact was recorded — see this module's docstring.
    movement_occurred_at: int = Field(ge=MIN_OCCURRED_AT, le=MAX_OCCURRED_AT)
    counterparty: str = Field(min_length=1, max_length=512)
    # Blank where there is no bank to name. A movement entered by hand is the
    # ordinary case of that — Financial publishes `""` rather than inventing
    # an institution — and the message says "Tu banco" instead. Requiring one
    # here discarded every manual movement as malformed, which is a silence
    # nobody could have debugged from the message it logged.
    bank: str = Field(default="", max_length=256)
    origin: str = Field(min_length=1, max_length=64)
    unassigned: bool = False

    @field_validator("counterparty")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("bank")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    def to_command(self) -> DeliverMovementAlertCommand:
        """Read the payload into this context's vocabulary.

        Raises `UnsupportedPayloadVersionError` for a version this cannot
        read: this method is what assigns v1 meaning to these fields, so
        reading a v2 payload here would announce whatever v2 changed — an
        amount in minor units, say — as a figure on somebody's phone.

        Raises `ValueError` on a direction, currency or origin this cannot
        read. None of them has a safe default: a guessed direction calls a
        salary a purchase, a guessed currency puts the wrong symbol in front
        of a figure, and a guessed origin is what decides whether the message
        is sent at all.
        """
        if self.version != SUPPORTED_VERSION:
            raise UnsupportedPayloadVersionError(
                f"Cannot read MovementRecorded version {self.version}",
            )

        return DeliverMovementAlertCommand(
            user_id=UserId(value=self.user_id),
            event_id=self.event_id,
            alert=MovementAlert(
                amount=Money(
                    amount=Decimal(self.amount),
                    currency=_currency(self.currency),
                ),
                direction=_direction(self.direction),
                counterparty=self.counterparty,
                bank=self.bank,
                occurred_at=PosixTime.from_epoch_seconds(self.movement_occurred_at),
                origin=_origin(self.origin),
                unassigned=self.unassigned,
            ),
        )


def _currency(value: str) -> Currency:
    try:
        return Currency(value.strip().upper())
    except ValueError as error:
        raise ValueError(f"Unknown currency: {value!r}") from error


def _direction(value: str) -> MovementDirection:
    try:
        return MovementDirection(value.strip().lower())
    except ValueError as error:
        raise ValueError(f"Unknown direction: {value!r}") from error


def _origin(value: str) -> MovementOrigin:
    try:
        return MovementOrigin(value.strip().lower())
    except ValueError as error:
        raise ValueError(f"Unknown origin: {value!r}") from error
