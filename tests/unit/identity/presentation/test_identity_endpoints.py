from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.application.handlers import (
    LoginUseCase,
    RegisterUserUseCase,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    AddInboxesUseCase,
)
from personal_finance.contexts.identity.application.ports import InboxRegistration
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_add_inboxes_use_case,
    get_login_use_case,
    get_register_use_case,
    get_token_issuer,
    router,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import UserId


PASSWORD = "correct horse battery staple"
SECRET = "test-secret"


class InMemoryUserRepository:
    def __init__(self, *users: User) -> None:
        self.by_email: dict[Email, User] = {user.email: user for user in users}

    def add_if_new(self, user: User) -> User | None:
        existing = self.by_email.get(user.email)

        if existing is not None:
            return existing

        self.by_email[user.email] = user

        return None

    def find_by_email(self, email: Email) -> User | None:
        return self.by_email.get(email)


class BcryptLikeHasher:
    """Real bcrypt is slow enough to notice across a whole test module, so the
    endpoint tests use a stand-in with the same contract instead.
    """

    def hash(self, password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{password}")

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        return hashed.value == f"hashed:{password}"


class RecordingInboxRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[UserId, tuple[InboxRegistration, ...]]] = []

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        self.calls.append((user_id, tuple(inboxes)))


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def token_issuer() -> JWTTokenIssuer:
    return JWTTokenIssuer(secret=SECRET, algorithm="HS256", ttl_minutes=60)


@pytest.fixture
def user_repository() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@pytest.fixture
def inbox_registrar() -> RecordingInboxRegistrar:
    return RecordingInboxRegistrar()


@pytest.fixture
def authenticated_client(
    token_issuer: JWTTokenIssuer,
    user_repository: InMemoryUserRepository,
    inbox_registrar: RecordingInboxRegistrar,
) -> TestClient:
    hasher = BcryptLikeHasher()
    register_use_case = RegisterUserUseCase(
        repository=user_repository,
        hasher=hasher,
        token_issuer=token_issuer,
        inbox_registrar=inbox_registrar,
        event_publisher=NullEventPublisher(),
    )
    login_use_case = LoginUseCase(
        repository=user_repository,
        hasher=hasher,
        token_issuer=token_issuer,
    )
    add_inboxes_use_case = AddInboxesUseCase(inbox_registrar=inbox_registrar)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_register_use_case] = lambda: register_use_case
    app.dependency_overrides[get_login_use_case] = lambda: login_use_case
    app.dependency_overrides[get_add_inboxes_use_case] = lambda: add_inboxes_use_case
    app.dependency_overrides[get_token_issuer] = lambda: token_issuer

    return TestClient(app)


def test_register_creates_an_account_and_returns_a_usable_token(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"

    me = authenticated_client.get(
        "/identity/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["user_id"] == body["user_id"]


def test_register_with_a_weak_password_is_rejected(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": "short"},
    )

    assert response.status_code == 422


def test_registering_the_same_email_twice_is_rejected(
    authenticated_client: TestClient,
) -> None:
    payload = {"email": "person@example.com", "password": PASSWORD}
    authenticated_client.post("/identity/register", json=payload)

    response = authenticated_client.post("/identity/register", json=payload)

    assert response.status_code == 409


def test_register_can_attach_inboxes_in_the_same_call(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "inboxes": [
                {
                    "address": "u-1@inbound.test",
                    "allowed_domains": ["bank.com"],
                },
            ],
        },
    )

    assert response.status_code == 201
    assert len(inbox_registrar.calls) == 1
    _, inboxes = inbox_registrar.calls[0]
    assert inboxes[0].address == "u-1@inbound.test"


def test_login_with_correct_credentials_returns_a_token(
    authenticated_client: TestClient,
) -> None:
    authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    response = authenticated_client.post(
        "/identity/login",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_with_the_wrong_password_is_rejected(
    authenticated_client: TestClient,
) -> None:
    authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    response = authenticated_client.post(
        "/identity/login",
        json={"email": "person@example.com", "password": "wrong password"},
    )

    assert response.status_code == 401


def test_login_with_an_unknown_email_is_rejected(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/login",
        json={"email": "nobody@example.com", "password": PASSWORD},
    )

    assert response.status_code == 401


def test_me_without_a_token_is_unauthorized(authenticated_client: TestClient) -> None:
    assert authenticated_client.get("/identity/me").status_code == 401


def test_me_with_a_malformed_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.get(
        "/identity/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_add_inboxes_registers_them_for_the_authenticated_user_only(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    registered = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )
    token = registered.json()["access_token"]
    user_id = registered.json()["user_id"]
    inbox_registrar.calls.clear()

    response = authenticated_client.post(
        "/identity/inboxes",
        json={
            "inboxes": [
                {
                    "address": "u-2@inbound.test",
                    "allowed_addresses": ["alerts@bank.com"],
                },
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 204
    assert len(inbox_registrar.calls) == 1
    called_user_id, inboxes = inbox_registrar.calls[0]
    assert str(called_user_id.value) == user_id
    assert inboxes[0].allowed_addresses == frozenset({"alerts@bank.com"})


def test_add_inboxes_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/inboxes",
        json={"inboxes": [{"address": "u-2@inbound.test"}]},
    )

    assert response.status_code == 401


def test_malformed_inbox_address_is_rejected(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "inboxes": [{"address": "not-an-email"}],
        },
    )

    assert response.status_code == 422
