"""The read behind the screen that walks a new user through forwarding.

Two of its four steps close somewhere the user cannot see, so this endpoint
is what turns "we'll let you know" into a checkmark that appears on its own.
"""

from collections.abc import Iterator, Sequence
import dataclasses

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    GetInboxSetupUseCase,
)
from personal_finance.contexts.ingestion.application.ports import NotificationSummary
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.contexts.ingestion.presentation.http.setup import (
    get_setup_use_case,
    router,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
BASE_ADDRESS = EmailAddress("finflowingest@gmail.com")
ALIAS = "finflowingest+11111111111111111111111111111111@gmail.com"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
PATH = "/ingestion/setup"
CONFIRMED_AT = 1_756_400_000
FIRST_ALERT_AT = 1_756_500_000


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox

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

        self.inboxes[address] = dataclasses.replace(inbox, **milestone)

        return True


class InMemoryReader:
    def __init__(self, *summaries: NotificationSummary) -> None:
        self._summaries = summaries

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        del user_id

        return self._summaries


def _rejected(sender: str) -> NotificationSummary:
    message_id = EmailMessageId(f"<{sender}>")

    return NotificationSummary(
        id=NotificationId.for_message(user_id=USER, message_id=message_id),
        message_id=message_id,
        sender=EmailAddress(sender),
        subject="Alertas y Notificaciones",
        status=ProcessingStatus.IGNORED,
        deferred_reason=None,
        received_at=PosixTime.from_epoch_seconds(FIRST_ALERT_AT),
    )


def _inbox(
    *,
    domains: frozenset[str] = frozenset(),
    forwarding_confirmed_at: int | None = None,
    first_accepted_at: int | None = None,
) -> UserInbox:
    return UserInbox(
        user_id=USER,
        address=EmailAddress(ALIAS),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
        forwarding_confirmed_at=(
            None
            if forwarding_confirmed_at is None
            else PosixTime.from_epoch_seconds(forwarding_confirmed_at)
        ),
        first_accepted_at=(
            None
            if first_accepted_at is None
            else PosixTime.from_epoch_seconds(first_accepted_at)
        ),
    )


def _client(
    inbox: UserInbox | None,
    *notifications: NotificationSummary,
) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_setup_use_case] = lambda: GetInboxSetupUseCase(
        inbox_repository=(
            InMemoryUserInboxRepository()
            if inbox is None
            else InMemoryUserInboxRepository(inbox)
        ),
        notification_reader=InMemoryReader(*notifications),
        base_address=BASE_ADDRESS,
    )
    app.dependency_overrides[get_current_user_id] = lambda: USER

    yield TestClient(app)

    app.dependency_overrides.clear()


@pytest.fixture
def fresh_client() -> Iterator[TestClient]:
    yield from _client(_inbox())


def test_a_new_account_gets_its_address_and_the_next_step(
    fresh_client: TestClient,
) -> None:
    body = fresh_client.get(PATH).json()

    assert body["address"] == ALIAS
    assert body["current"] == "senders_approved"
    assert body["ready"] is False
    assert [step["key"] for step in body["steps"]] == [
        "address_assigned",
        "senders_approved",
        "forwarding_confirmed",
        "first_alert",
    ]
    assert [step["done"] for step in body["steps"]] == [True, False, False, False]


def test_a_confirmed_forwarding_rule_shows_when_it_happened() -> None:
    client = next(
        _client(
            _inbox(
                domains=frozenset({BANK_DOMAIN}),
                forwarding_confirmed_at=CONFIRMED_AT,
            ),
        ),
    )

    steps = {step["key"]: step for step in client.get(PATH).json()["steps"]}

    assert steps["forwarding_confirmed"] == {
        "key": "forwarding_confirmed",
        "done": True,
        "at": CONFIRMED_AT,
    }


def test_a_finished_setup_reports_ready_and_no_current_step() -> None:
    client = next(
        _client(
            _inbox(
                domains=frozenset({BANK_DOMAIN}),
                forwarding_confirmed_at=CONFIRMED_AT,
                first_accepted_at=FIRST_ALERT_AT,
            ),
        ),
    )

    body = client.get(PATH).json()

    assert body["ready"] is True
    assert body["current"] is None
    assert body["unapproved_senders"] == []


def test_it_names_the_sender_being_turned_away() -> None:
    client = next(
        _client(
            _inbox(
                domains=frozenset({BANK_DOMAIN}),
                forwarding_confirmed_at=CONFIRMED_AT,
            ),
            _rejected("alertas@otrobanco.com"),
        ),
    )

    body = client.get(PATH).json()

    assert body["unapproved_senders"] == ["alertas@otrobanco.com"]
    assert body["ready"] is False


def test_an_account_without_an_inbox_is_a_404() -> None:
    client = next(_client(None))

    assert client.get(PATH).status_code == 404


def test_it_requires_a_token() -> None:
    """No override for the current user here: the dependency is the only
    thing deciding whose setup this is.
    """
    app = FastAPI()
    app.include_router(router)

    assert TestClient(app).get(PATH).status_code == 401
