from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from personal_finance.shared.domain.events import Event


class EventPublisher(Protocol):
    """Port for publishing the domain events an aggregate root accumulated."""

    def publish(self, events: Sequence[Event]) -> None: ...
