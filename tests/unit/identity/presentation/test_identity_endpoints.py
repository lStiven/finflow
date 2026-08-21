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
    ListInboxesUseCase,
)
from personal_finance.contexts.identity.application.ports import (
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_add_inboxes_use_case,
    get_list_inboxes_use_case,
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
    """Records what was registered and can read it back, so a test can follow
    an inbox from `POST /identity/inboxes` through to `GET /identity/inboxes`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[UserId, tuple[InboxRegistration, ...]]] = []
        self.stored: dict[UserId, dict[str, RegisteredInbox]] = {}

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        self.calls.append((user_id, tuple(inboxes)))
        owned = self.stored.setdefault(user_id, {})

        for inbox in inboxes:
            owned[inbox.address] = RegisteredInbox(
                address=inbox.address,
                allowed_domains=inbox.allowed_domains,
                allowed_addresses=inbox.allowed_addresses,
            )

    def list_for_user(self, user_id: UserId) -> Sequence[RegisteredInbox]:
        owned = self.stored.get(user_id, {})

        return [owned[address] for address in sorted(owned)]


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
    list_inboxes_use_case = ListInboxesUseCase(inbox_reader=inbox_registrar)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_register_use_case] = lambda: register_use_case
    app.dependency_overrides[get_login_use_case] = lambda: login_use_case
    app.dependency_overrides[get_add_inboxes_use_case] = lambda: add_inboxes_use_case
    app.dependency_overrides[get_list_inboxes_use_case] = lambda: list_inboxes_use_case
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


def _register(client: TestClient, *, email: str, inboxes: object = None) -> str:
    """Register an account and return its access token."""
    payload: dict[str, object] = {"email": email, "password": PASSWORD}

    if inboxes is not None:
        payload["inboxes"] = inboxes

    return client.post("/identity/register", json=payload).json()["access_token"]


def test_listing_inboxes_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    assert authenticated_client.get("/identity/inboxes").status_code == 401


def test_a_new_account_without_inboxes_lists_none(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")

    response = authenticated_client.get(
        "/identity/inboxes",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"inboxes": []}


def test_listing_returns_the_inboxes_and_their_trusted_senders(
    authenticated_client: TestClient,
) -> None:
    token = _register(
        authenticated_client,
        email="person@example.com",
        inboxes=[
            {
                "address": "u-1@inbound.test",
                "allowed_domains": ["bancolombia.com.co"],
                "allowed_addresses": ["alertas@nequi.com.co"],
            },
        ],
    )

    response = authenticated_client.get(
        "/identity/inboxes",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "inboxes": [
            {
                "address": "u-1@inbound.test",
                "allowed_domains": ["bancolombia.com.co"],
                "allowed_addresses": ["alertas@nequi.com.co"],
            },
        ],
    }


def test_an_inbox_added_after_registration_shows_up_in_the_listing(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")
    authenticated_client.post(
        "/identity/inboxes",
        json={"inboxes": [{"address": "u-2@inbound.test"}]},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = authenticated_client.get(
        "/identity/inboxes",
        headers={"Authorization": f"Bearer {token}"},
    )

    addresses = [inbox["address"] for inbox in response.json()["inboxes"]]
    assert addresses == ["u-2@inbound.test"]


def test_a_user_never_sees_another_users_inboxes(
    authenticated_client: TestClient,
) -> None:
    mine = _register(
        authenticated_client,
        email="mine@example.com",
        inboxes=[{"address": "mine@inbound.test"}],
    )
    _register(
        authenticated_client,
        email="theirs@example.com",
        inboxes=[{"address": "theirs@inbound.test"}],
    )

    response = authenticated_client.get(
        "/identity/inboxes",
        headers={"Authorization": f"Bearer {mine}"},
    )

    # Whose inboxes to read comes from the token, so there is no request shape
    # that could ask for someone else's.
    addresses = [inbox["address"] for inbox in response.json()["inboxes"]]
    assert addresses == ["mine@inbound.test"]
