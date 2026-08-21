from __future__ import annotations

from collections.abc import Sequence

from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.events import Event


class CompositeEventPublisher:
    """Fans one batch of domain events out to several publishers.

    The order matters: publishers run in the order given, and the first one to
    raise stops the rest. Put the local, cheap one first, so the audit trail
    is already written when a remote publish fails.
    """

    def __init__(self, *publishers: EventPublisher) -> None:
        self._publishers = publishers

    def publish(self, events: Sequence[Event]) -> None:
        for publisher in self._publishers:
            publisher.publish(events)
