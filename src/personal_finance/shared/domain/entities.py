from __future__ import annotations

from collections.abc import Hashable
import dataclasses
from typing import TypeVar

from personal_finance.shared.domain.events import Event


EntityId = TypeVar("EntityId")


@dataclasses.dataclass(eq=False, slots=True)
class Entity[EntityId: Hashable]:
    id: EntityId

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return NotImplemented

        return type(self) is type(other) and self.id == other.id  # type: ignore

    def __hash__(self) -> int:
        return hash((type(self), self.id))


@dataclasses.dataclass(eq=False, slots=True)
class AggregateRoot[EntityId: Hashable](Entity[EntityId]):
    """Base class for aggregate roots. Accumulates domain events to be
    published by the application layer after a successful transaction.
    """

    _pending_events: list[Event] = dataclasses.field(
        default_factory=lambda: [],
        init=False,
        repr=False,
    )

    def record_event(self, event: Event) -> None:
        self._pending_events.append(event)

    def pull_events(self) -> list[Event]:
        events = list(self._pending_events)
        self._pending_events.clear()

        return events
