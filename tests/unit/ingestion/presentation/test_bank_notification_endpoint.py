from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import IdempotencyKey
from personal_finance.contexts.ingestion.presentation.http.router import (
    get_use_case,
    router,
)
from personal_finance.shared.domain.events import Event


ENDPOINT = "/ingestion/bank-notifications"


class FakeRepository:
    def __init__(self) -> None:
        self.saved: dict[IdempotencyKey, BankNotification] = {}

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        existing = self.saved.get(notification.idempotency_key)

        if existing is not None:
            return existing

        self.saved[notification.idempotency_key] = notification

        return None

    def save(self, notification: BankNotification) -> None:
        self.saved[notification.idempotency_key] = notification


class FakeQueuePublisher:
    def __init__(self) -> None:
        self.enqueued: list[ParseNotificationMessage] = []

    def enqueue(self, message: ParseNotificationMessage) -> None:
        self.enqueued.append(message)


class FakeEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def queue_publisher() -> FakeQueuePublisher:
    return FakeQueuePublisher()


@pytest.fixture
def client(queue_publisher: FakeQueuePublisher) -> TestClient:
    use_case = ReceiveBankNotificationUseCase(
        repository=FakeRepository(),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=frozenset({"bank.com"})),
        queue_publisher=queue_publisher,
        event_publisher=FakeEventPublisher(),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_use_case] = lambda: use_case

    # No context manager: the app lifespan builds the real AWS-backed graph.
    return TestClient(app)


def test_authorized_notification_is_accepted_and_queued(
    client: TestClient,
    queue_publisher: FakeQueuePublisher,
) -> None:
    response = client.post(
        ENDPOINT,
        json={
            "message_id": "message-1",
            "sender": "alerts@bank.com",
            "subject": "Purchase notification",
            "raw_content": "Purchase for COP 50,000",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["is_duplicate"] is False
    assert len(queue_publisher.enqueued) == 1


def test_unauthorized_sender_is_accepted_but_never_queued(
    client: TestClient,
    queue_publisher: FakeQueuePublisher,
) -> None:
    response = client.post(
        ENDPOINT,
        json={
            "message_id": "message-1",
            "sender": "phisher@evil.com",
            "raw_content": "Click here",
        },
    )

    # The webhook caller is not told whether the sender passed the filter.
    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert queue_publisher.enqueued == []


def test_redelivery_is_reported_as_duplicate_and_queued_once(
    client: TestClient,
    queue_publisher: FakeQueuePublisher,
) -> None:
    payload = {
        "message_id": "message-1",
        "sender": "alerts@bank.com",
        "raw_content": "Purchase for COP 50,000",
    }

    first = client.post(ENDPOINT, json=payload)
    second = client.post(ENDPOINT, json=payload)

    assert first.json()["is_duplicate"] is False
    assert second.json()["is_duplicate"] is True
    assert second.json()["notification_id"] == first.json()["notification_id"]
    assert len(queue_publisher.enqueued) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"message_id": "m1", "raw_content": "x"},
        {"message_id": "m1", "sender": "not-an-email", "raw_content": "x"},
        {"message_id": "", "sender": "alerts@bank.com", "raw_content": "x"},
        {"message_id": "m1", "sender": "alerts@bank.com", "raw_content": ""},
    ],
    ids=["missing-sender", "malformed-sender", "empty-message-id", "empty-body"],
)
def test_malformed_payloads_are_rejected(
    client: TestClient,
    payload: dict[str, str],
) -> None:
    assert client.post(ENDPOINT, json=payload).status_code == 422
