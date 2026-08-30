from collections.abc import Sequence
import dataclasses

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    IdempotencyKey,
)
from personal_finance.contexts.ingestion.presentation.http.router import (
    get_use_case,
    router,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


ENDPOINT = "/ingestion/bank-notifications"

INBOX_ADDRESS = "u-7f3a9c@inbound.finflow.test"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        # Like the real one: the milestones belong to another writer, and
        # this must not carry a stale copy of them back over the record.
        stored = self.inboxes.get(inbox.address)
        self.inboxes[inbox.address] = dataclasses.replace(
            inbox,
            forwarding_confirmed_at=(
                stored.forwarding_confirmed_at if stored else None
            ),
            first_accepted_at=stored.first_accepted_at if stored else None,
        )

    def mark_forwarding_confirmed(
        self,
        *,
        address: EmailAddress,
        confirmed_at: PosixTime,
    ) -> bool:
        return self._mark(address, forwarding_confirmed_at=confirmed_at)

    def mark_first_accepted(
        self,
        *,
        address: EmailAddress,
        accepted_at: PosixTime,
    ) -> bool:
        return self._mark(address, first_accepted_at=accepted_at)

    def _mark(self, address: EmailAddress, **milestone: PosixTime) -> bool:
        inbox = self.inboxes.get(address)

        if inbox is None:
            return False

        # First write wins, as `if_not_exists` does in DynamoDB.
        already_set = {
            field: value
            for field, value in milestone.items()
            if getattr(inbox, field) is not None
        }
        self.inboxes[address] = dataclasses.replace(
            inbox,
            **{k: v for k, v in milestone.items() if k not in already_set},
        )

        return True


def _inbox(
    *,
    address: str = INBOX_ADDRESS,
    domains: frozenset[str] = frozenset({"bank.com"}),
) -> UserInbox:
    return UserInbox(
        user_id=USER_ID,
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


class FakeRepository:
    def __init__(self) -> None:
        self.saved: dict[IdempotencyKey, BankNotification] = {}

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        existing = self.saved.get(notification.idempotency_key)

        if existing is not None:
            return existing

        self.saved[notification.idempotency_key] = notification

        return None

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        return self.saved.get(idempotency_key)

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
        inbox_repository=InMemoryUserInboxRepository(_inbox()),
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
            "recipient": INBOX_ADDRESS,
            "message_id": "message-1",
            "sender": "alerts@bank.com",
            "subject": "Purchase notification",
            "raw_content": "Purchase for COP 50,000",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["outcome"] == "accepted"
    assert body["status"] == "queued"
    assert len(queue_publisher.enqueued) == 1


def test_unauthorized_sender_is_accepted_but_never_queued(
    client: TestClient,
    queue_publisher: FakeQueuePublisher,
) -> None:
    response = client.post(
        ENDPOINT,
        json={
            "recipient": INBOX_ADDRESS,
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
        "recipient": INBOX_ADDRESS,
        "message_id": "message-1",
        "sender": "alerts@bank.com",
        "raw_content": "Purchase for COP 50,000",
    }

    first = client.post(ENDPOINT, json=payload)
    second = client.post(ENDPOINT, json=payload)

    assert first.json()["outcome"] == "accepted"
    assert second.json()["outcome"] == "duplicate"
    assert second.json()["notification_id"] == first.json()["notification_id"]
    assert len(queue_publisher.enqueued) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"recipient": INBOX_ADDRESS, "message_id": "m1", "raw_content": "x"},
        {
            "recipient": INBOX_ADDRESS,
            "message_id": "m1",
            "sender": "not-an-email",
            "raw_content": "x",
        },
        {
            "recipient": INBOX_ADDRESS,
            "message_id": "",
            "sender": "alerts@bank.com",
            "raw_content": "x",
        },
        {"message_id": "m1", "sender": "alerts@bank.com", "raw_content": "x"},
    ],
    ids=[
        "missing-sender",
        "malformed-sender",
        "empty-message-id",
        "missing-recipient",
    ],
)
def test_malformed_payloads_are_rejected(
    client: TestClient,
    payload: dict[str, str],
) -> None:
    assert client.post(ENDPOINT, json=payload).status_code == 422


def test_unknown_recipient_is_accepted_without_revealing_anything(
    client: TestClient,
    queue_publisher: FakeQueuePublisher,
) -> None:
    response = client.post(
        ENDPOINT,
        json={
            "recipient": "nobody@inbound.finflow.test",
            "message_id": "message-1",
            "sender": "alerts@bank.com",
            "raw_content": "Purchase for COP 50,000",
        },
    )

    # Still 202: a different status code would let a caller enumerate which
    # inbound addresses exist.
    assert response.status_code == 202
    body = response.json()
    assert body["outcome"] == "unknown_recipient"
    assert body["notification_id"] is None
    assert queue_publisher.enqueued == []
