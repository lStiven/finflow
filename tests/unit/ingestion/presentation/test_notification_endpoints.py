"""The read surface a client uses to answer "did my email arrive?"."""

from collections.abc import Iterator, Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.ports import NotificationSummary
from personal_finance.contexts.ingestion.application.queries import (
    ListNotificationsUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.presentation.http.notifications import (
    get_list_notifications_use_case,
    router,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
PATH = "/ingestion/notifications"


class InMemoryReader:
    def __init__(self, *summaries: NotificationSummary) -> None:
        self._summaries = summaries
        self.asked_for: list[UserId] = []

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        self.asked_for.append(user_id)

        return self._summaries


def _summary(
    *,
    message_id: str = "<1@bank.com>",
    status: ProcessingStatus = ProcessingStatus.PROCESSED,
    deferred_reason: NotificationDeferredReason | None = None,
    received_at: int = 1_787_000_000,
) -> NotificationSummary:
    return NotificationSummary(
        id=NotificationId.for_message(
            user_id=USER,
            message_id=EmailMessageId(message_id),
        ),
        message_id=EmailMessageId(message_id),
        sender=EmailAddress("alertas@bank.com"),
        subject="Notificación",
        status=status,
        deferred_reason=deferred_reason,
        received_at=PosixTime.from_epoch_seconds(received_at),
    )


@pytest.fixture
def reader() -> InMemoryReader:
    return InMemoryReader(
        _summary(message_id="<processed@bank.com>", received_at=1_787_000_200),
        _summary(
            message_id="<ignored@bank.com>",
            status=ProcessingStatus.IGNORED,
            received_at=1_787_000_100,
        ),
        _summary(
            message_id="<deferred@bank.com>",
            status=ProcessingStatus.PENDING_FALLBACK,
            deferred_reason=NotificationDeferredReason.FALLBACK_FOUND_NOTHING,
            received_at=1_787_000_000,
        ),
    )


@pytest.fixture
def client(reader: InMemoryReader) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_list_notifications_use_case] = lambda: (
        ListNotificationsUseCase(reader=reader)
    )
    app.dependency_overrides[get_current_user_id] = lambda: USER

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_it_lists_what_arrived_newest_first(client: TestClient) -> None:
    response = client.get(PATH)

    assert response.status_code == 200
    body = response.json()
    assert [item["message_id"] for item in body["notifications"]] == [
        "<processed@bank.com>",
        "<ignored@bank.com>",
        "<deferred@bank.com>",
    ]
    assert body["total"] == 3


def test_it_never_returns_the_email_body(client: TestClient) -> None:
    """The list answers what happened to a message, not what it said."""
    notification = client.get(PATH).json()["notifications"][0]

    assert "raw_content" not in notification
    assert "body" not in notification


def test_an_ignored_email_is_visible_so_its_sender_can_be_approved(
    client: TestClient,
) -> None:
    body = client.get(PATH, params={"status": "ignored"}).json()

    assert [item["message_id"] for item in body["notifications"]] == [
        "<ignored@bank.com>",
    ]
    assert body["notifications"][0]["sender"] == "alertas@bank.com"


def test_the_counts_are_keyed_by_status_and_survive_a_filter(
    client: TestClient,
) -> None:
    body = client.get(PATH, params={"status": "ignored"}).json()

    assert body["total"] == 1
    assert body["counts"] == {
        "processed": 1,
        "ignored": 1,
        "pending_fallback": 1,
    }


def test_a_deferred_notification_carries_its_reason(client: TestClient) -> None:
    body = client.get(PATH, params={"status": "pending_fallback"}).json()

    assert body["notifications"][0]["deferred_reason"] == "fallback_found_nothing"


def test_a_processed_notification_has_no_reason(client: TestClient) -> None:
    body = client.get(PATH, params={"status": "processed"}).json()

    assert body["notifications"][0]["deferred_reason"] is None


def test_it_reads_the_mail_of_the_user_in_the_token(
    client: TestClient,
    reader: InMemoryReader,
) -> None:
    client.get(PATH)

    assert reader.asked_for == [USER]


def test_an_unknown_status_is_refused(client: TestClient) -> None:
    assert client.get(PATH, params={"status": "whatever"}).status_code == 422


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_a_page_outside_the_allowed_range_is_refused(
    client: TestClient,
    params: dict[str, int],
) -> None:
    assert client.get(PATH, params=params).status_code == 422


def test_paging_is_echoed_back(client: TestClient) -> None:
    body = client.get(PATH, params={"limit": 1, "offset": 1}).json()

    assert body["limit"] == 1
    assert body["offset"] == 1
    assert [item["message_id"] for item in body["notifications"]] == [
        "<ignored@bank.com>",
    ]


def test_it_needs_a_token() -> None:
    """No override here: the real dependency must be the one that refuses."""
    app = FastAPI()
    app.include_router(router)

    assert TestClient(app).get(PATH).status_code == 401
