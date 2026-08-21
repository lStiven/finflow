from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from typing import Protocol
import uuid

from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import JsonValue, PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class IntegrationEvent:
    """The public form of a domain event, as other contexts receive it.

    Deliberately separate from the domain event it came from. A domain event
    is free to carry aggregates and value objects and to change shape as the
    model does; this is a contract other contexts read, so its payload is
    built field by field and versioned.
    """

    # What published it, e.g. `finflow.ingestion`. Subscribers filter on this.
    source: str
    # What happened, e.g. `TransactionExtracted`.
    detail_type: str
    # Bumped when the payload's shape changes in a way subscribers must
    # notice. Carried inside the payload so a consumer sees it without
    # depending on the transport's envelope.
    version: int
    # Carried through from the domain event that caused this one. Delivery is
    # at-least-once, so a subscriber needs a stable identity to recognise a
    # replay by — the same discipline ingestion applies to redelivered email.
    event_id: uuid.UUID
    payload: Mapping[str, JsonValue]
    occurred_at: PosixTime


class IntegrationEventTranslator(Protocol):
    """Decides which domain events leave their context, and in what shape.

    Returning None is the normal answer: most domain events are internal
    bookkeeping, and publishing them would turn a context's private lifecycle
    into a contract someone else can depend on.
    """

    def translate(self, event: Event) -> IntegrationEvent | None: ...
