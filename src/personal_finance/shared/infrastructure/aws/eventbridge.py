from __future__ import annotations

from collections.abc import Sequence
import itertools
import json
import logging

from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_events.type_defs import PutEventsRequestEntryTypeDef

from personal_finance.shared.application.integration import (
    IntegrationEvent,
    IntegrationEventTranslator,
)
from personal_finance.shared.domain.events import Event


_logger = logging.getLogger(__name__)

# `PutEvents` accepts at most 10 entries per call.
MAX_ENTRIES_PER_CALL = 10

VERSION_FIELD = "version"
EVENT_ID_FIELD = "event_id"
OCCURRED_AT_FIELD = "occurred_at"


class IntegrationEventPublishError(Exception):
    """Raised when EventBridge rejected one or more entries.

    Not swallowed: a dropped integration event is a downstream context that
    silently never learns what happened.
    """


class EventBridgeEventPublisher:
    """`EventPublisher` that puts a context's public events on the bus.

    Only what the translator recognises is published. Everything else is a
    context's own business and stops here, which is what keeps an aggregate's
    internals from becoming someone else's contract.
    """

    def __init__(
        self,
        *,
        client: EventBridgeClient,
        event_bus_name: str,
        translator: IntegrationEventTranslator,
    ) -> None:
        self._client = client
        self._event_bus_name = event_bus_name
        self._translator = translator

    def publish(self, events: Sequence[Event]) -> None:
        entries = [
            self._entry(integration_event)
            for event in events
            if (integration_event := self._translator.translate(event)) is not None
        ]

        # strict=False: the final batch is normally short, and dropping it
        # would silently lose events.
        for batch in itertools.batched(entries, MAX_ENTRIES_PER_CALL, strict=False):
            self._put(list(batch))

    def _entry(self, event: IntegrationEvent) -> PutEventsRequestEntryTypeDef:
        # The envelope fields go in the detail rather than only on the
        # transport: a subscriber reading from an SQS target sees the payload,
        # and must not have to reach into EventBridge's own wrapper to
        # deduplicate a replay.
        detail = dict(event.payload)
        detail[VERSION_FIELD] = event.version
        detail[EVENT_ID_FIELD] = str(event.event_id)
        detail[OCCURRED_AT_FIELD] = event.occurred_at.as_epoch_seconds()

        return {
            "EventBusName": self._event_bus_name,
            "Source": event.source,
            "DetailType": event.detail_type,
            "Detail": json.dumps(detail),
            "Time": event.occurred_at.to_datetime(),
        }

    def _put(self, entries: list[PutEventsRequestEntryTypeDef]) -> None:
        response = self._client.put_events(Entries=entries)
        failed = response.get("FailedEntryCount", 0)

        if not failed:
            return

        # The call itself succeeds even when individual entries are rejected,
        # so the count is the only place a partial failure shows up.
        reasons = sorted(
            {
                message
                for result in response.get("Entries", [])
                if (message := result.get("ErrorCode")) is not None
            },
        )

        raise IntegrationEventPublishError(
            f"EventBridge rejected {failed} of {len(entries)} entries: "
            f"{', '.join(reasons) or 'no error code returned'}",
        )
