"""The read surface a client uses to answer "did my email arrive?"."""

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.ports import (
    NotificationPage,
    NotificationPageRequest,
    NotificationSummary,
)
from personal_finance.contexts.ingestion.application.queries import (
    ListNotificationsUseCase,
)
from personal_finance.contexts.ingestion.domain.parsing.registry import BANK_DOMAINS
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
    """Stands in for the index: newest first, filtered, windowed."""

    def __init__(self, *summaries: NotificationSummary) -> None:
        self._summaries = summaries
        self.asked_for: list[UserId] = []
        self.counted: list[UserId] = []

    def page_by_user(self, request: NotificationPageRequest) -> NotificationPage:
        self.asked_for.append(request.user_id)
        found = [
            summary
            for summary in self._newest_first()
            if request.status is None or summary.status is request.status
        ]
        wanted = request.offset + request.limit

        return NotificationPage(
            notifications=found[request.offset : wanted],
            has_more=len(found) > wanted,
        )

    def count_by_status(self, user_id: UserId) -> Mapping[ProcessingStatus, int]:
        self.counted.append(user_id)

        return Counter(summary.status for summary in self._summaries)

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        self.asked_for.append(user_id)

        return self._summaries

    def _newest_first(self) -> list[NotificationSummary]:
        return sorted(
            self._summaries,
            key=lambda summary: summary.received_at.as_epoch_seconds(),
            reverse=True,
        )


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
    assert body["has_more"] is False


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
    body = client.get(
        PATH,
        params={"status": "ignored", "with_counts": "true"},
    ).json()

    assert body["total"] == 1
    assert body["counts"] == {
        "processed": 1,
        "ignored": 1,
        "pending_fallback": 1,
    }


def test_a_list_does_not_count_the_whole_history_unless_asked(
    client: TestClient,
    reader: InMemoryReader,
) -> None:
    """The counts are the only part of this answer that reads everything the
    account ever received, and this list polls.
    """
    body = client.get(PATH).json()

    assert reader.counted == []
    assert body["counts"] is None
    assert body["total"] is None


def test_a_further_page_is_announced_without_a_total(client: TestClient) -> None:
    body = client.get(PATH, params={"limit": 1}).json()

    assert body["has_more"] is True
    assert len(body["notifications"]) == 1


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
    assert body["has_more"] is True
    assert [item["message_id"] for item in body["notifications"]] == [
        "<ignored@bank.com>",
    ]


def test_it_needs_a_token() -> None:
    """No override here: the real dependency must be the one that refuses."""
    app = FastAPI()
    app.include_router(router)

    assert TestClient(app).get(PATH).status_code == 401


def test_the_catalog_publishes_the_states_and_the_instrument_vocabulary(
    client: TestClient,
) -> None:
    response = client.get("/ingestion/catalog")

    assert response.status_code == 200

    catalog = response.json()
    statuses = [option["value"] for option in catalog["processing_statuses"]]
    assert "processed" in statuses
    assert "pending_fallback" in statuses

    assert "unauthorized_sender" in [
        option["value"] for option in catalog["ignored_reasons"]
    ]
    assert catalog["deferred_reasons"]

    # The words an alert arrives with. An account declared with any other
    # spelling is accepted and then never matches one.
    kinds = [option["value"] for option in catalog["instrument_kinds"]]
    assert "credit_card" in kinds
    assert "savings_account" in kinds


def test_the_catalog_lists_the_banks_with_a_parser_and_all_their_domains(
    client: TestClient,
) -> None:
    banks = {
        bank["id"]: bank
        for bank in client.get("/ingestion/catalog").json()["known_banks"]
    }

    assert banks["bancolombia"]["name"] == "Bancolombia"
    assert set(banks["bancolombia"]["domains"]) == {
        domain for domain, bank in BANK_DOMAINS.items() if bank == "bancolombia"
    }
    assert banks["lulo bank"]["domains"] == ["lulobank.com"]


def test_every_published_status_is_one_the_filter_accepts(
    client: TestClient,
) -> None:
    """The catalogue is worth nothing if a value in it is rejected on use."""
    catalog = client.get("/ingestion/catalog").json()

    for option in catalog["processing_statuses"]:
        response = client.get(
            "/ingestion/notifications",
            params={"status": option["value"]},
        )

        assert response.status_code == 200, (option["value"], response.text)
