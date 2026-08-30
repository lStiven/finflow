from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.application.handlers import (
    GetProfileUseCase,
    LoginUseCase,
    RegisterUserUseCase,
    UpdateProfileUseCase,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    GetInboxUseCase,
    UpdateApprovedSendersUseCase,
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
    get_inbox_use_case,
    get_login_use_case,
    get_profile_use_case,
    get_register_use_case,
    get_token_issuer,
    get_update_approved_senders_use_case,
    get_update_profile_use_case,
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

    def rename(self, user: User) -> bool:
        stored = self.by_email.get(user.email)

        if stored is None or stored.id != user.id:
            return False

        stored.name = user.name

        return True


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
    an inbox from registration through to `GET /identity/inbox`.

    Assigns each user a deterministic fake address, standing in for what
    ingestion's own address derivation would produce — this layer's tests
    only need it to be stable and unique per user, not the real scheme.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[UserId, InboxRegistration]] = []
        self.stored: dict[UserId, RegisteredInbox] = {}

    def register(
        self,
        *,
        user_id: UserId,
        inbox: InboxRegistration,
    ) -> RegisteredInbox:
        self.calls.append((user_id, inbox))
        registered = RegisteredInbox(
            address=f"inbox+{user_id.value}@test",
            allowed_domains=inbox.allowed_domains,
            allowed_addresses=inbox.allowed_addresses,
        )
        self.stored[user_id] = registered

        return registered

    def get_for_user(self, user_id: UserId) -> RegisteredInbox | None:
        return self.stored.get(user_id)


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
    update_senders_use_case = UpdateApprovedSendersUseCase(
        inbox_registrar=inbox_registrar,
    )
    get_inbox_use_case_instance = GetInboxUseCase(inbox_reader=inbox_registrar)
    profile_use_case = GetProfileUseCase(repository=user_repository)
    update_profile_use_case = UpdateProfileUseCase(repository=user_repository)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_register_use_case] = lambda: register_use_case
    app.dependency_overrides[get_login_use_case] = lambda: login_use_case
    app.dependency_overrides[get_update_approved_senders_use_case] = lambda: (
        update_senders_use_case
    )
    app.dependency_overrides[get_inbox_use_case] = lambda: get_inbox_use_case_instance
    app.dependency_overrides[get_profile_use_case] = lambda: profile_use_case
    app.dependency_overrides[get_update_profile_use_case] = lambda: (
        update_profile_use_case
    )
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


def test_registering_assigns_a_forwarding_address_even_with_no_senders_named(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    assert response.status_code == 201
    assert len(inbox_registrar.calls) == 1
    _, inbox = inbox_registrar.calls[0]
    assert inbox == InboxRegistration()


def test_register_can_approve_senders_in_the_same_call(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "allowed_domains": ["bank.com"],
        },
    )

    assert response.status_code == 201
    _, inbox = inbox_registrar.calls[0]
    assert inbox.allowed_domains == frozenset({"bank.com"})


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


def test_malformed_sender_address_is_rejected(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "allowed_addresses": ["not-an-email"],
        },
    )

    assert response.status_code == 422


def _register(client: TestClient, *, email: str) -> str:
    """Register an account and return its access token."""
    payload = {"email": email, "password": PASSWORD}

    return client.post("/identity/register", json=payload).json()["access_token"]


def test_getting_the_inbox_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    assert authenticated_client.get("/identity/inbox").status_code == 401


def test_a_new_account_already_has_a_forwarding_address(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")

    response = authenticated_client.get(
        "/identity/inbox",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["address"]
    assert body["allowed_domains"] == []
    assert body["allowed_addresses"] == []


def test_updating_approved_senders_replaces_them_for_the_authenticated_user_only(
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

    response = authenticated_client.patch(
        "/identity/inbox",
        json={"allowed_addresses": ["alerts@bank.com"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["allowed_addresses"] == ["alerts@bank.com"]
    assert len(inbox_registrar.calls) == 1
    called_user_id, inbox = inbox_registrar.calls[0]
    assert str(called_user_id.value) == user_id
    assert inbox.allowed_addresses == frozenset({"alerts@bank.com"})


def test_updating_approved_senders_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.patch(
        "/identity/inbox",
        json={"allowed_addresses": ["alerts@bank.com"]},
    )

    assert response.status_code == 401


def test_the_forwarding_address_does_not_change_when_senders_are_updated(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")
    before = authenticated_client.get(
        "/identity/inbox",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["address"]

    authenticated_client.patch(
        "/identity/inbox",
        json={"allowed_domains": ["bank.com"]},
        headers={"Authorization": f"Bearer {token}"},
    )

    after = authenticated_client.get(
        "/identity/inbox",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["address"]

    assert before == after


def test_a_user_never_sees_another_users_inbox(
    authenticated_client: TestClient,
) -> None:
    mine = _register(authenticated_client, email="mine@example.com")
    theirs = _register(authenticated_client, email="theirs@example.com")

    def _address(token: str) -> str:
        response = authenticated_client.get(
            "/identity/inbox",
            headers={"Authorization": f"Bearer {token}"},
        )

        return str(response.json()["address"])

    # Whose inbox to read comes from the token, so there is no request shape
    # that could ask for someone else's.
    assert _address(mine) != _address(theirs)


def test_a_name_given_at_registration_comes_back_from_me(
    authenticated_client: TestClient,
) -> None:
    registered = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "name": "  Ada   Lovelace  ",
        },
    )

    assert registered.status_code == 201
    me = authenticated_client.get(
        "/identity/me",
        headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
    )

    assert me.status_code == 200
    assert me.json()["name"] == "Ada Lovelace"
    assert me.json()["email"] == "person@example.com"


def test_registering_without_a_name_leaves_it_unset(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")

    me = authenticated_client.get(
        "/identity/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me.json()["name"] is None


def test_the_token_carries_the_email_and_name_for_the_client_to_read(
    authenticated_client: TestClient,
    token_issuer: JWTTokenIssuer,
) -> None:
    registered = authenticated_client.post(
        "/identity/register",
        json={
            "email": "person@example.com",
            "password": PASSWORD,
            "name": "Ada Lovelace",
        },
    )

    caller = token_issuer.verify(registered.json()["access_token"])

    assert caller.email == Email("person@example.com")
    assert caller.name is not None
    assert caller.name.value == "Ada Lovelace"


def test_updating_the_name_is_reflected_immediately(
    authenticated_client: TestClient,
) -> None:
    token = _register(authenticated_client, email="person@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = authenticated_client.patch(
        "/identity/me",
        json={"name": "Ada Lovelace"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    assert authenticated_client.get("/identity/me", headers=headers).json()["name"] == (
        "Ada Lovelace"
    )


def test_me_answers_the_stored_name_rather_than_the_one_in_the_token(
    authenticated_client: TestClient,
) -> None:
    # The token still carries the name it was issued with; the endpoint must
    # not repeat it back once the account has been renamed.
    registered = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD, "name": "Ada"},
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    authenticated_client.patch(
        "/identity/me",
        json={"name": "Ada Lovelace"},
        headers=headers,
    )

    assert authenticated_client.get("/identity/me", headers=headers).json()["name"] == (
        "Ada Lovelace"
    )


def test_updating_the_name_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.patch("/identity/me", json={"name": "Ada"})

    assert response.status_code == 401


def test_one_user_cannot_rename_another(authenticated_client: TestClient) -> None:
    first = _register(authenticated_client, email="one@example.com")
    _register(authenticated_client, email="two@example.com")

    authenticated_client.patch(
        "/identity/me",
        json={"name": "Ada Lovelace"},
        headers={"Authorization": f"Bearer {first}"},
    )

    other = authenticated_client.get(
        "/identity/me",
        headers={
            "Authorization": f"Bearer {_login(authenticated_client, 'two@example.com')}"
        },
    )
    assert other.json()["name"] is None
    assert other.json()["email"] == "two@example.com"


@pytest.mark.parametrize("name", ["", "   ", "a" * 81])
def test_an_unusable_name_is_rejected(
    authenticated_client: TestClient,
    name: str,
) -> None:
    token = _register(authenticated_client, email="person@example.com")

    response = authenticated_client.patch(
        "/identity/me",
        json={"name": name},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/identity/login", json={"email": email, "password": PASSWORD}
    )

    return response.json()["access_token"]
