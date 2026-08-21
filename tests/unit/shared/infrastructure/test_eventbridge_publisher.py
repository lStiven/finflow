import dataclasses
import json
from typing import Any
import uuid

import pytest

from personal_finance.shared.application.integration import IntegrationEvent
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.eventbridge import (
    MAX_ENTRIES_PER_CALL,
    EventBridgeEventPublisher,
    IntegrationEventPublishError,
)


BUS = "finflow"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PublicEvent(Event):
    note: str = "public"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PrivateEvent(Event):
    note: str = "private"


class OnlyPublicTranslator:
    """Publishes `PublicEvent`; everything else stays inside its context."""

    def translate(self, event: Event) -> IntegrationEvent | None:
        if not isinstance(event, PublicEvent):
            return None

        return IntegrationEvent(
            source="finflow.testing",
            detail_type="PublicEvent",
            version=3,
            event_id=event.event_id,
            payload={"note": event.note},
            occurred_at=event.occurred_at,
        )


class FakeEventBridgeClient:
    def __init__(self, *, failed: int = 0, error_code: str | None = None) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self._failed = failed
        self._error_code = error_code

    def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: N803
        self.calls.append(Entries)

        if not self._failed:
            return {"FailedEntryCount": 0, "Entries": []}

        return {
            "FailedEntryCount": self._failed,
            "Entries": [{"ErrorCode": self._error_code}] * self._failed,
        }

    @property
    def all_entries(self) -> list[dict[str, Any]]:
        return [entry for call in self.calls for entry in call]


def _publisher(client: FakeEventBridgeClient) -> EventBridgeEventPublisher:
    return EventBridgeEventPublisher(
        client=client,  # pyright: ignore[reportArgumentType]
        event_bus_name=BUS,
        translator=OnlyPublicTranslator(),
    )


def test_a_recognised_event_is_put_on_the_bus() -> None:
    client = FakeEventBridgeClient()

    _publisher(client).publish([PublicEvent()])

    assert len(client.all_entries) == 1
    entry = client.all_entries[0]
    assert entry["EventBusName"] == BUS
    assert entry["Source"] == "finflow.testing"
    assert entry["DetailType"] == "PublicEvent"


def test_an_unrecognised_event_never_leaves_its_context() -> None:
    client = FakeEventBridgeClient()

    _publisher(client).publish([PrivateEvent()])

    # Not merely filtered from the payload — no call is made at all.
    assert client.calls == []


def test_only_the_recognised_events_of_a_mixed_batch_are_published() -> None:
    client = FakeEventBridgeClient()

    _publisher(client).publish([PrivateEvent(), PublicEvent(), PrivateEvent()])

    assert len(client.all_entries) == 1


def test_publishing_nothing_makes_no_call() -> None:
    client = FakeEventBridgeClient()

    _publisher(client).publish([])

    assert client.calls == []


def test_the_detail_carries_version_and_event_id_for_deduplication() -> None:
    client = FakeEventBridgeClient()
    event = PublicEvent()

    _publisher(client).publish([event])

    detail = json.loads(client.all_entries[0]["Detail"])
    assert detail["note"] == "public"
    assert detail["version"] == 3
    # Delivery is at-least-once, so a subscriber needs a stable identity to
    # recognise a replay by.
    assert detail["event_id"] == str(event.event_id)
    assert detail["occurred_at"] == event.occurred_at.as_epoch_seconds()


def test_a_batch_larger_than_the_api_limit_is_split() -> None:
    client = FakeEventBridgeClient()
    events = [PublicEvent() for _ in range(MAX_ENTRIES_PER_CALL * 2 + 3)]

    _publisher(client).publish(events)

    assert [len(call) for call in client.calls] == [
        MAX_ENTRIES_PER_CALL,
        MAX_ENTRIES_PER_CALL,
        3,
    ]
    assert len(client.all_entries) == len(events)


def test_every_event_keeps_its_own_identity_across_a_split_batch() -> None:
    client = FakeEventBridgeClient()
    events = [PublicEvent() for _ in range(MAX_ENTRIES_PER_CALL + 1)]

    _publisher(client).publish(events)

    published = {
        json.loads(entry["Detail"])["event_id"] for entry in client.all_entries
    }
    assert published == {str(event.event_id) for event in events}


def test_a_rejected_entry_raises_rather_than_being_swallowed() -> None:
    # A dropped integration event is a downstream context that silently never
    # learns what happened, so this must never pass quietly.
    client = FakeEventBridgeClient(failed=1, error_code="ThrottlingException")

    with pytest.raises(IntegrationEventPublishError, match="ThrottlingException"):
        _publisher(client).publish([PublicEvent()])


def test_a_rejection_without_an_error_code_still_raises() -> None:
    client = FakeEventBridgeClient(failed=1, error_code=None)

    with pytest.raises(IntegrationEventPublishError, match="rejected 1"):
        _publisher(client).publish([PublicEvent()])


def test_the_event_time_comes_from_the_domain_event() -> None:
    client = FakeEventBridgeClient()
    occurred_at = PosixTime.from_epoch_seconds(1_700_000_000)
    event = PublicEvent(event_id=uuid.uuid4(), occurred_at=occurred_at)

    _publisher(client).publish([event])

    assert client.all_entries[0]["Time"] == occurred_at.to_datetime()
