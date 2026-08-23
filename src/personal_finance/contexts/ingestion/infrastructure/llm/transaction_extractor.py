"""The model fallback, as ingestion sees it.

The schema below is the whole security boundary between a language model and
this system's data: the model cannot return anything else, and whatever it
does return still has to survive the domain value objects — an unreadable
amount, an impossible date or an empty counterparty is treated as "could not
read it", never as a transaction.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
import logging
from typing import Literal

from pydantic import BaseModel, Field

from personal_finance.contexts.ingestion.domain.parsing.dates import BOGOTA
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    Instrument,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.llm.prompt import (
    SYSTEM_INSTRUCTION,
    build_prompt,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
)
from personal_finance.shared.infrastructure.config.settings import (
    LLMSettings,
    get_llm_settings,
)
from personal_finance.shared.infrastructure.llm.errors import (
    LLMUnusableResponseError,
)
from personal_finance.shared.infrastructure.llm.gemini import (
    StructuredModel,
    build_structured_model,
    truncate_for_prompt,
)


_logger = logging.getLogger(__name__)

# The wall-clock format the prompt demands back.
LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M"

NO_INSTRUMENT = "none"

# Spelled out rather than derived from the domain enums: a JSON schema needs
# literal members, and a test asserts these stay equal to the enums so the two
# cannot drift apart silently.
type ExtractedKind = Literal[
    "card_purchase",
    "qr_payment",
    "transfer",
    "incoming_payment",
]
type ExtractedDirection = Literal["outgoing", "incoming"]
type ExtractedInstrument = Literal[
    "credit_card",
    "debit_card",
    "savings_account",
    "checking_account",
    "account",
    "none",
]


class ExtractedTransactionSchema(BaseModel):
    """The only shape the model is allowed to answer in.

    The model is told to fill every field, including on a refusal: a partially
    filled object would leave the caller deciding which halves to trust, and
    `understood` is the single flag that settles it. The string fields carry a
    default so an answer that skips one still parses and is judged on its
    content — `bank` does not, because the domain refuses a transaction
    without it, and an omitted field would throw away an email the model had
    otherwise read correctly.
    """

    understood: bool
    kind: ExtractedKind
    direction: ExtractedDirection
    # A string, never a number: JSON has only floats, and a float cannot hold
    # a peso amount exactly.
    amount: str = Field(default="", max_length=32)
    currency: Literal["COP", "USD"]
    occurred_at_local: str = Field(default="", max_length=32)
    counterparty: str = Field(default="", max_length=512)
    # Which institution sent the alert — a template parser already knows its
    # own bank, but the fallback has to name it, since it is the only signal
    # Financial gets to tell two banks' otherwise-identical instruments apart.
    bank: str = Field(max_length=128)
    instrument_kind: ExtractedInstrument
    instrument_last_four: str = Field(default="", max_length=8)


class GeminiTransactionExtractor:
    """`TransactionExtractor` backed by one structured call per email."""

    def __init__(
        self,
        *,
        model: StructuredModel,
        settings: LLMSettings,
    ) -> None:
        self._model = model
        self._settings = settings

    def extract(
        self,
        *,
        sender: EmailAddress,
        subject: str,
        body: str,
        received_at: PosixTime,
    ) -> ExtractedTransaction | None:
        prompt = build_prompt(
            sender=sender.value,
            subject=subject,
            body=truncate_for_prompt(
                body,
                limit=self._settings.max_input_characters,
            ),
            received_at=_local_time(received_at),
        )

        try:
            answer = self._model.complete(
                system_instruction=SYSTEM_INSTRUCTION,
                prompt=prompt,
                schema=ExtractedTransactionSchema,
            )
        except LLMUnusableResponseError:
            # The model answered something that is not the schema. Not worth a
            # retry, and certainly not worth guessing at.
            _logger.exception("fallback extraction returned an unusable answer")

            return None

        if not answer.understood:
            _logger.info(
                "fallback extraction declined the email",
                extra={"sender": sender.value},
            )

            return None

        return _to_transaction(answer)


def _to_transaction(
    answer: ExtractedTransactionSchema,
) -> ExtractedTransaction | None:
    """Turn a validated answer into a domain value, or nothing.

    The domain does the second half of the checking: `Money` refuses a
    negative amount and `ExtractedTransaction` refuses an empty counterparty,
    so a model that satisfied the schema but not the rules still cannot get a
    transaction through.
    """
    amount = _to_decimal(answer.amount)
    occurred_at = _to_instant(answer.occurred_at_local)
    bank = answer.bank.strip()

    if amount is None or occurred_at is None:
        return None

    if not bank:
        # Refused rather than filled in: an institution we cannot name cannot
        # be matched to an account, and a placeholder would merge two banks'
        # cards that happen to share their last four digits into one account
        # holding somebody's money twice.
        _logger.warning("fallback extraction named no bank")

        return None

    try:
        return ExtractedTransaction(
            kind=TransactionKind(answer.kind),
            direction=TransactionDirection(answer.direction),
            amount=Money(amount=amount, currency=Currency(answer.currency)),
            occurred_at=occurred_at,
            counterparty=answer.counterparty.strip(),
            bank=bank,
            instrument=_to_instrument(answer),
        )
    except ValueError:
        _logger.exception("fallback extraction produced an invalid transaction")

        return None


def _to_instrument(answer: ExtractedTransactionSchema) -> Instrument | None:
    if answer.instrument_kind == NO_INSTRUMENT:
        return None

    last_four = answer.instrument_last_four.strip()

    return Instrument(
        kind=InstrumentKind(answer.instrument_kind),
        # Anything that is not four plain digits is dropped rather than
        # rejected: the instrument is context, and losing it is better than
        # losing the whole transaction.
        last_four=last_four if last_four.isdigit() else None,
    )


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.strip())
    except InvalidOperation:
        _logger.warning("fallback extraction returned an unreadable amount")

        return None


def _to_instant(raw: str) -> PosixTime | None:
    """Read the local wall clock the prompt asked for.

    Interpreted in Bogotá for the same reason the deterministic parser does:
    the alerts carry no zone, and reading them as UTC would move every
    late-evening purchase into the next day.
    """
    try:
        local = datetime.datetime.strptime(raw.strip(), LOCAL_TIME_FORMAT)
    except ValueError:
        _logger.warning("fallback extraction returned an unreadable date")

        return None

    return PosixTime.from_datetime(local.replace(tzinfo=BOGOTA))


def _local_time(moment: PosixTime) -> str:
    return moment.to_datetime().astimezone(BOGOTA).strftime(LOCAL_TIME_FORMAT)


def build_transaction_extractor() -> GeminiTransactionExtractor | None:
    """The extractor, or None when this deployment has no model.

    Returning None rather than raising is what makes the fallback optional:
    the parse worker wires whatever it gets, and a deployment with no key
    simply keeps unrecognised alerts instead of reading them.
    """
    settings = get_llm_settings()

    if not settings.configured:
        return None

    return GeminiTransactionExtractor(
        model=build_structured_model(),
        settings=settings,
    )
