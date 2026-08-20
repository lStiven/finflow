from __future__ import annotations

from collections.abc import Sequence
import logging

from personal_finance.shared.domain.events import Event


_logger = logging.getLogger(__name__)


class LoggingEventPublisher:
    """`EventPublisher` that only records events locally.

    Ingestion's current events are internal to the context — no other context
    subscribes to them — so there is nothing to put on the integration bus yet.
    This keeps the audit trail visible until a real cross-context event exists.
    """

    def publish(self, events: Sequence[Event]) -> None:
        for event in events:
            _logger.info(
                "domain_event",
                extra={
                    "event_type": type(event).__name__,
                    "event_id": str(event.event_id),
                    "occurred_at": event.occurred_at.as_epoch_seconds(),
                },
            )
