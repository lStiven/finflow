from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
import pytest

from personal_finance.contexts.identity.application.credential_handlers import (
    ChangePasswordUseCase,
    ConfirmEmailVerificationUseCase,
    RequestEmailVerificationUseCase,
    RequestPasswordResetUseCase,
    ResetPasswordUseCase,
)
from personal_finance.contexts.identity.application.handlers import (
    AuthenticateUseCase,
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
from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    PasswordResetTicket,
    PasswordResetWindow,
    VerificationState,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    SecretHash,
)
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_authenticator,
    get_change_password_use_case,
    get_confirm_verification_use_case,
    get_inbox_use_case,
    get_login_use_case,
    get_profile_use_case,
    get_register_use_case,
    get_request_password_reset_use_case,
    get_request_verification_use_case,
    get_reset_password_use_case,
    get_token_issuer,
    get_update_approved_senders_use_case,
    get_update_profile_use_case,
    router,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.throttling import build_guard
from personal_finance.shared.presentation.throttling import Guard


PASSWORD = "correct horse battery staple"
SECRET = "test-secret"
CODE = "123456"
RESET_URL = "http://localhost:5173/restablecer"


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

    def change_password(self, user: User) -> bool:
        stored = self.by_email.get(user.email)

        if stored is None or stored.id != user.id:
            return False

        stored.password_hash = user.password_hash
        stored.credential_version = user.credential_version

        return True


class BcryptLikeHasher:
    """Real bcrypt is slow enough to notice across a whole test module, so the
    endpoint tests use a stand-in with the same contract instead.
    """

    def hash(self, password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{password}")

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        return hashed.value == f"hashed:{password}"


class FakeSecretHasher:
    """Stands in for both real hashers. Reversible on purpose, so a test can
    assert which secret a record was written with.
    """

    def hash(self, secret: str) -> SecretHash:
        return SecretHash(f"hashed:{secret}")

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        return hashed.value == f"hashed:{secret}"


class FixedSecretGenerator:
    """A known code and a counted sequence of tokens, so the test can act as
    the person reading the mail.
    """

    def __init__(self) -> None:
        self.issued_tokens: list[str] = []

    def verification_code(self) -> str:
        return CODE

    def opaque_token(self) -> str:
        # Padded to the length the payload models require: the real generator
        # returns 43 URL-safe characters, and a router that accepted a short
        # one would not be testing what it ships.
        token = f"token-{len(self.issued_tokens)}".ljust(32, "x")
        self.issued_tokens.append(token)

        return token


class InMemoryEmailVerificationRepository:
    """The same optimistic-write contract the DynamoDB one has: a save lands
    only if the counters have not moved, and a ticket is spent once.
    """

    def __init__(self) -> None:
        self.stored: dict[Email, EmailVerification] = {}

    def find(self, email: Email) -> EmailVerification | None:
        return self.stored.get(email)

    def save(
        self,
        verification: EmailVerification,
        *,
        expected: VerificationState | None,
    ) -> bool:
        current = self.stored.get(verification.email)
        seen = current.state if current is not None else None

        if seen != expected and current is not verification:
            return False

        self.stored[verification.email] = verification

        return True

    def consume_ticket(
        self,
        *,
        email: Email,
        ticket_hash: SecretHash,
        now: PosixTime,
    ) -> bool:
        stored = self.stored.get(email)

        if stored is None or not stored.ticket_matches(
            ticket_hash=ticket_hash,
            now=now,
        ):
            return False

        del self.stored[email]

        return True


class InMemoryPasswordResetRepository:
    def __init__(self) -> None:
        self.windows: dict[Email, PasswordResetWindow] = {}
        self.tickets: dict[str, PasswordResetTicket] = {}

    def find_window(self, email: Email) -> PasswordResetWindow | None:
        return self.windows.get(email)

    def save_window(
        self,
        window: PasswordResetWindow,
        *,
        expected_sends: int | None,
    ) -> bool:
        current = self.windows.get(window.email)
        seen = current.window.sends if current is not None else None

        if seen != expected_sends:
            return False

        self.windows[window.email] = window

        return True

    def save_ticket(self, ticket: PasswordResetTicket) -> None:
        self.tickets[ticket.token_hash.value] = ticket

    def delete_ticket(self, token_hash: SecretHash) -> None:
        self.tickets.pop(token_hash.value, None)

    def consume_ticket(
        self,
        *,
        token_hash: SecretHash,
        now: PosixTime,
    ) -> PasswordResetTicket | None:
        ticket = self.tickets.pop(token_hash.value, None)

        if ticket is None or ticket.is_expired(now):
            return None

        return ticket


class RecordingNotifier:
    """Every message this context would have sent, in order."""

    def __init__(self) -> None:
        self.codes: list[tuple[str, str]] = []
        self.existing_account_notices: list[str] = []
        self.reset_links: list[tuple[str, str]] = []
        self.unknown_account_notices: list[str] = []

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        del expires_in_minutes
        self.codes.append((email.value, code))

    def send_registration_notice_for_existing_account(self, *, email: Email) -> None:
        self.existing_account_notices.append(email.value)

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None:
        del expires_in_minutes
        self.reset_links.append((email.value, link))

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None:
        self.unknown_account_notices.append(email.value)


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
def verifications() -> InMemoryEmailVerificationRepository:
    return InMemoryEmailVerificationRepository()


@pytest.fixture
def resets() -> InMemoryPasswordResetRepository:
    return InMemoryPasswordResetRepository()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def generator() -> FixedSecretGenerator:
    return FixedSecretGenerator()


class InMemoryAttemptCounter:
    """`AttemptCounter` en un diccionario.

    Guarda `expires_at` aunque nada lo lea: es lo que deja comprobar que la
    ventana se fija al crear el balde y no se empuja con cada intento, que es
    la diferencia entre una ventana y una cuenta que nunca termina.
    """

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    def spent(self, bucket: str) -> int:
        return self.hits.get(bucket, 0)

    def record(self, *, bucket: str, expires_at: PosixTime) -> int:
        self.hits[bucket] = self.hits.get(bucket, 0) + 1
        self.expiries.setdefault(bucket, expires_at.as_epoch_seconds())

        return self.hits[bucket]

    def clear(self, bucket: str) -> None:
        self.hits.pop(bucket, None)
        self.expiries.pop(bucket, None)


@pytest.fixture
def counter() -> InMemoryAttemptCounter:
    return InMemoryAttemptCounter()


@pytest.fixture
def authenticated_client(
    token_issuer: JWTTokenIssuer,
    user_repository: InMemoryUserRepository,
    inbox_registrar: RecordingInboxRegistrar,
    verifications: InMemoryEmailVerificationRepository,
    resets: InMemoryPasswordResetRepository,
    notifier: RecordingNotifier,
    generator: FixedSecretGenerator,
    counter: InMemoryAttemptCounter,
) -> TestClient:
    hasher = BcryptLikeHasher()
    secret_hasher = FakeSecretHasher()
    register_use_case = RegisterUserUseCase(
        repository=user_repository,
        hasher=hasher,
        token_issuer=token_issuer,
        inbox_registrar=inbox_registrar,
        event_publisher=NullEventPublisher(),
        verifications=verifications,
        ticket_hasher=secret_hasher,
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
    # The limiter, over a counter in a dict rather than DynamoDB. Overridden
    # rather than disabled: every case below goes through the real `Guard`,
    # so the doors are exercised by the same code the deployment runs.
    app.dependency_overrides[build_guard] = lambda: Guard(
        counter,
        trust_proxy=False,
    )
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
    app.dependency_overrides[get_authenticator] = lambda: AuthenticateUseCase(
        token_issuer=token_issuer,
        repository=user_repository,
    )
    app.dependency_overrides[get_request_verification_use_case] = lambda: (
        RequestEmailVerificationUseCase(
            verifications=verifications,
            users=user_repository,
            generator=generator,
            code_hasher=secret_hasher,
            notifier=notifier,
            code_ttl_minutes=15,
            window_minutes=60,
        )
    )
    app.dependency_overrides[get_confirm_verification_use_case] = lambda: (
        ConfirmEmailVerificationUseCase(
            verifications=verifications,
            generator=generator,
            code_hasher=secret_hasher,
            ticket_hasher=secret_hasher,
            ticket_ttl_minutes=30,
        )
    )
    app.dependency_overrides[get_request_password_reset_use_case] = lambda: (
        RequestPasswordResetUseCase(
            resets=resets,
            users=user_repository,
            generator=generator,
            token_hasher=secret_hasher,
            notifier=notifier,
            reset_url=RESET_URL,
            ttl_minutes=30,
            window_minutes=60,
        )
    )
    app.dependency_overrides[get_reset_password_use_case] = lambda: (
        ResetPasswordUseCase(
            resets=resets,
            users=user_repository,
            hasher=hasher,
            token_hasher=secret_hasher,
            event_publisher=NullEventPublisher(),
        )
    )
    app.dependency_overrides[get_change_password_use_case] = lambda: (
        ChangePasswordUseCase(
            users=user_repository,
            hasher=hasher,
            token_issuer=token_issuer,
            event_publisher=NullEventPublisher(),
        )
    )

    return TestClient(app)


def _verified(client: TestClient, email: str) -> str:
    """Go through the code exchange the way the frontend has to, and hand back
    the ticket registration spends.
    """
    requested = client.post("/identity/verification/request", json={"email": email})
    assert requested.status_code == 202

    confirmed = client.post(
        "/identity/verification/confirm",
        json={"email": email, "code": CODE},
    )
    assert confirmed.status_code == 200

    return str(confirmed.json()["verification_token"])


def _register(
    client: TestClient,
    *,
    email: str = "person@example.com",
    password: str = PASSWORD,
    **extra: object,
) -> Response:
    """Register the way a client must: verify the address, then spend the
    ticket. Every registration test goes through this, so the precondition is
    exercised on every one of them rather than mocked away.
    """
    return client.post(
        "/identity/register",
        json={
            "email": email,
            "password": password,
            "verification_token": _verified(client, email),
            **extra,
        },
    )


def test_register_creates_an_account_and_returns_a_usable_token(
    authenticated_client: TestClient,
) -> None:
    response = _register(authenticated_client)

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
    response = _register(authenticated_client, password="short")

    assert response.status_code == 422


def test_registering_the_same_email_twice_is_rejected(
    authenticated_client: TestClient,
) -> None:
    _register(authenticated_client)

    # A second, freshly verified attempt on the same address: the ticket is
    # good and the address is the problem.
    response = _register(authenticated_client)

    assert response.status_code == 409


def test_registering_assigns_a_forwarding_address_even_with_no_senders_named(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    response = _register(authenticated_client)

    assert response.status_code == 201
    assert len(inbox_registrar.calls) == 1
    _, inbox = inbox_registrar.calls[0]
    assert inbox == InboxRegistration()


def test_register_can_approve_senders_in_the_same_call(
    authenticated_client: TestClient,
    inbox_registrar: RecordingInboxRegistrar,
) -> None:
    response = _register(authenticated_client, allowed_domains=["bank.com"])

    assert response.status_code == 201
    _, inbox = inbox_registrar.calls[0]
    assert inbox.allowed_domains == frozenset({"bank.com"})


def test_login_with_correct_credentials_returns_a_token(
    authenticated_client: TestClient,
) -> None:
    _register(authenticated_client)

    response = authenticated_client.post(
        "/identity/login",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_with_the_wrong_password_is_rejected(
    authenticated_client: TestClient,
) -> None:
    _register(authenticated_client)

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
    response = _register(
        authenticated_client,
        allowed_addresses=["not-an-email"],
    )

    assert response.status_code == 422


def _register_token(client: TestClient, *, email: str) -> str:
    """Register an account and return its access token."""
    return str(
        client.post(
            "/identity/register",
            json={
                "email": email,
                "password": PASSWORD,
                "verification_token": _verified(client, email),
            },
        ).json()["access_token"]
    )


def test_getting_the_inbox_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    assert authenticated_client.get("/identity/inbox").status_code == 401


def test_a_new_account_already_has_a_forwarding_address(
    authenticated_client: TestClient,
) -> None:
    token = _register_token(authenticated_client, email="person@example.com")

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
    registered = _register(authenticated_client)
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
    token = _register_token(authenticated_client, email="person@example.com")
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
    mine = _register_token(authenticated_client, email="mine@example.com")
    theirs = _register_token(authenticated_client, email="theirs@example.com")

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
    registered = _register(authenticated_client, name="  Ada   Lovelace  ")

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
    token = _register_token(authenticated_client, email="person@example.com")

    me = authenticated_client.get(
        "/identity/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me.json()["name"] is None


def test_the_token_carries_the_email_and_name_for_the_client_to_read(
    authenticated_client: TestClient,
    token_issuer: JWTTokenIssuer,
) -> None:
    registered = _register(authenticated_client, name="Ada Lovelace")

    caller = token_issuer.verify(registered.json()["access_token"])

    assert caller.email == Email("person@example.com")
    assert caller.name is not None
    assert caller.name.value == "Ada Lovelace"


def test_updating_the_name_is_reflected_immediately(
    authenticated_client: TestClient,
) -> None:
    token = _register_token(authenticated_client, email="person@example.com")
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
    registered = _register(authenticated_client, name="Ada")
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
    first = _register_token(authenticated_client, email="one@example.com")
    _register_token(authenticated_client, email="two@example.com")

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
    token = _register_token(authenticated_client, email="person@example.com")

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


# ----------------------------------------------------------------------
# Proving an address
# ----------------------------------------------------------------------


def test_asking_for_a_code_answers_202_and_says_nothing_about_the_address(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    response = authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    assert response.status_code == 202
    # Never the code itself: this deployment is not a developer's laptop.
    assert response.json()["code"] is None
    assert notifier.codes == [("person@example.com", CODE)]


def test_an_address_that_already_has_an_account_answers_exactly_the_same(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    _register(authenticated_client)
    notifier.codes.clear()

    response = authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    assert response.status_code == 202
    # No code — but mail all the same, so the answer gives nothing away.
    assert notifier.codes == []
    assert notifier.existing_account_notices == ["person@example.com"]


def test_asking_twice_in_a_row_is_refused_with_a_retry_after(
    authenticated_client: TestClient,
) -> None:
    authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    response = authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_a_malformed_address_is_refused(authenticated_client: TestClient) -> None:
    response = authenticated_client.post(
        "/identity/verification/request",
        json={"email": "not-an-address"},
    )

    assert response.status_code == 422


def test_the_wrong_code_is_a_400_that_says_nothing_else(
    authenticated_client: TestClient,
) -> None:
    authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    response = authenticated_client.post(
        "/identity/verification/confirm",
        json={"email": "person@example.com", "code": "000000"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "That code is not valid"


def test_a_code_for_an_address_with_no_challenge_answers_the_same(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/verification/confirm",
        json={"email": "nobody@example.com", "code": CODE},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "That code is not valid"


def test_running_out_of_attempts_answers_429(
    authenticated_client: TestClient,
) -> None:
    authenticated_client.post(
        "/identity/verification/request",
        json={"email": "person@example.com"},
    )

    for _ in range(5):
        authenticated_client.post(
            "/identity/verification/confirm",
            json={"email": "person@example.com", "code": "000000"},
        )

    response = authenticated_client.post(
        "/identity/verification/confirm",
        json={"email": "person@example.com", "code": CODE},
    )

    assert response.status_code == 429


def test_a_ticket_is_spent_by_the_registration_that_uses_it(
    authenticated_client: TestClient,
) -> None:
    token = _verified(authenticated_client, "person@example.com")
    payload = {
        "email": "person@example.com",
        "password": PASSWORD,
        "verification_token": token,
    }
    assert authenticated_client.post(
        "/identity/register", json=payload
    ).status_code == (201)

    # Replaying the request cannot make a second account.
    replayed = authenticated_client.post("/identity/register", json=payload)

    assert replayed.status_code == 403


def test_registering_without_a_ticket_is_refused(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/register",
        json={"email": "person@example.com", "password": PASSWORD},
    )

    assert response.status_code == 422


def test_registering_with_someone_elses_ticket_is_refused(
    authenticated_client: TestClient,
) -> None:
    # A ticket names the address it was issued for; it is not a general
    # licence to register.
    token = _verified(authenticated_client, "person@example.com")

    response = authenticated_client.post(
        "/identity/register",
        json={
            "email": "someone.else@example.com",
            "password": PASSWORD,
            "verification_token": token,
        },
    )

    assert response.status_code == 403


# ----------------------------------------------------------------------
# Passwords
# ----------------------------------------------------------------------


def _reset_token(client: TestClient, notifier: RecordingNotifier, email: str) -> str:
    assert (
        client.post(
            "/identity/password/forgot",
            json={"email": email},
        ).status_code
        == 202
    )
    [(_, link)] = notifier.reset_links

    return link.split("token=", 1)[1]


def test_forgetting_a_password_answers_202_for_an_unknown_address_too(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    response = authenticated_client.post(
        "/identity/password/forgot",
        json={"email": "nobody@example.com"},
    )

    assert response.status_code == 202
    assert response.content == b""
    assert notifier.reset_links == []
    # Mail either way, so the answer is not "this address is not registered".
    assert notifier.unknown_account_notices == ["nobody@example.com"]


def test_a_reset_link_sets_a_new_password_and_the_old_one_stops_working(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    _register(authenticated_client)
    token = _reset_token(authenticated_client, notifier, "person@example.com")

    reset = authenticated_client.post(
        "/identity/password/reset",
        json={"token": token, "new_password": "a whole different password"},
    )

    assert reset.status_code == 204
    assert (
        authenticated_client.post(
            "/identity/login",
            json={"email": "person@example.com", "password": PASSWORD},
        ).status_code
        == 401
    )
    assert (
        authenticated_client.post(
            "/identity/login",
            json={
                "email": "person@example.com",
                "password": "a whole different password",
            },
        ).status_code
        == 200
    )


def test_a_reset_ends_the_sessions_opened_with_the_old_password(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    """The whole point of a reset: whoever had the old password is out.

    A token that outlived the reset it provoked would make the reset a
    formality.
    """
    registered = _register(authenticated_client)
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    assert authenticated_client.get("/identity/me", headers=headers).status_code == 200

    token = _reset_token(authenticated_client, notifier, "person@example.com")
    authenticated_client.post(
        "/identity/password/reset",
        json={"token": token, "new_password": "a whole different password"},
    )

    assert authenticated_client.get("/identity/me", headers=headers).status_code == 401


def test_a_reset_link_works_once(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    _register(authenticated_client)
    token = _reset_token(authenticated_client, notifier, "person@example.com")
    authenticated_client.post(
        "/identity/password/reset",
        json={"token": token, "new_password": "a whole different password"},
    )

    replayed = authenticated_client.post(
        "/identity/password/reset",
        json={"token": token, "new_password": "and another one entirely"},
    )

    assert replayed.status_code == 400


def test_an_unknown_reset_link_is_a_400_that_confirms_nothing(
    authenticated_client: TestClient,
) -> None:
    response = authenticated_client.post(
        "/identity/password/reset",
        json={"token": "never-issued-by-anybody", "new_password": "long enough here"},
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"] == "That link is no longer valid. Ask for a new one."
    )


def test_a_weak_new_password_is_refused_by_the_reset(
    authenticated_client: TestClient,
    notifier: RecordingNotifier,
) -> None:
    _register(authenticated_client)
    token = _reset_token(authenticated_client, notifier, "person@example.com")

    response = authenticated_client.post(
        "/identity/password/reset",
        json={"token": token, "new_password": "short"},
    )

    assert response.status_code == 422


def test_changing_a_password_returns_a_token_that_still_works(
    authenticated_client: TestClient,
) -> None:
    registered = _register(authenticated_client)
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    response = authenticated_client.post(
        "/identity/password/change",
        json={
            "current_password": PASSWORD,
            "new_password": "a whole different password",
        },
        headers=headers,
    )

    assert response.status_code == 200
    replacement = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert (
        authenticated_client.get(
            "/identity/me",
            headers=replacement,
        ).status_code
        == 200
    )
    # The token that asked is spent along with every other session.
    assert authenticated_client.get("/identity/me", headers=headers).status_code == 401


def test_changing_a_password_without_the_current_one_is_refused(
    authenticated_client: TestClient,
) -> None:
    registered = _register(authenticated_client)
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    response = authenticated_client.post(
        "/identity/password/change",
        json={"current_password": "not it", "new_password": "a different password"},
        headers=headers,
    )

    # 403 rather than 401: the token is fine and the session is not over, so a
    # client that reads 401 as "you have been signed out" must not see one.
    assert response.status_code == 403
    assert (
        authenticated_client.post(
            "/identity/login",
            json={"email": "person@example.com", "password": PASSWORD},
        ).status_code
        == 200
    )


def test_changing_a_password_without_a_token_is_unauthorized(
    authenticated_client: TestClient,
) -> None:
    # And *this* is the 401: no token at all. The pair is what lets a client
    # tell "sign in again" from "you mistyped your current password".
    response = authenticated_client.post(
        "/identity/password/change",
        json={
            "current_password": PASSWORD,
            "new_password": "a whole different password",
        },
    )

    assert response.status_code == 401


# ----------------------------------------------------------------------
# Fuerza bruta, y la gente que no la está haciendo
#
# La mitad interesante de estos casos no es que un ataque se corte: es que
# usar la aplicación normalmente nunca lo dispare. Un límite que encierra a
# quien no hizo nada no es una protección, es la caída que el atacante quería.
# ----------------------------------------------------------------------


def _wrong_login(client: TestClient, email: str = "person@example.com") -> int:
    return client.post(
        "/identity/login",
        json={"email": email, "password": "not the password"},
    ).status_code


def _right_login(client: TestClient, email: str = "person@example.com") -> int:
    return client.post(
        "/identity/login",
        json={"email": email, "password": PASSWORD},
    ).status_code


def test_una_direccion_no_admite_mas_de_cinco_claves_equivocadas(
    authenticated_client: TestClient,
) -> None:
    """Cinco por cuarto de hora: 480 al día contra una contraseña de verdad
    no es un ataque viable, y cinco fallos seguidos es alguien que ya debería
    estar pidiendo el enlace de recuperación."""
    _register(authenticated_client)

    for _ in range(5):
        assert _wrong_login(authenticated_client) == 401

    refused = authenticated_client.post(
        "/identity/login",
        json={"email": "person@example.com", "password": "not the password"},
    )

    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 0


def test_entrar_bien_no_gasta_presupuesto(authenticated_client: TestClient) -> None:
    """El falso positivo más caro sería este: que usar la app la cerrara."""
    _register(authenticated_client)

    for _ in range(50):
        assert _right_login(authenticated_client) == 200


def test_acertar_perdona_los_fallos_anteriores(
    authenticated_client: TestClient,
) -> None:
    """Quien se equivoca cuatro veces y a la quinta entra no puede quedarse
    con cuatro fallos encima el resto del cuarto de hora."""
    _register(authenticated_client)

    for _ in range(4):
        assert _wrong_login(authenticated_client) == 401

    assert _right_login(authenticated_client) == 200

    for _ in range(5):
        assert _wrong_login(authenticated_client) == 401


def test_una_cuenta_agotada_no_encierra_a_quien_comparte_la_conexion(
    authenticated_client: TestClient,
) -> None:
    """La casa, la oficina, el operador que mete una ciudad detrás de una IP:
    agotar el presupuesto de una cuenta no puede tocar la de al lado."""
    _register(authenticated_client)
    _register(authenticated_client, email="vecina@example.com")

    for _ in range(6):
        _wrong_login(authenticated_client)

    assert _wrong_login(authenticated_client) == 429
    assert _right_login(authenticated_client, email="vecina@example.com") == 200


def test_la_direccion_es_un_freno_y_no_una_cerradura(
    authenticated_client: TestClient,
) -> None:
    """Lo que la dimensión por cuenta no puede ver: una contraseña probada
    contra veinte direcciones distintas, cada una una sola vez.

    La ventana es de un minuto a propósito. La primera versión eran treinta
    fallos por cuarto de hora, y el e2e enseñó lo que eso significaba: tras
    una ráfaga de claves malas, una **correcta** desde la misma dirección
    quedaba rechazada quince minutos. Un atacante sigue sin poder probar más
    de veinte por minuto; un vecino espera menos de uno.
    """
    for index in range(20):
        assert (
            _wrong_login(authenticated_client, email=f"quien{index}@example.com") == 401
        )

    assert _wrong_login(authenticated_client, email="una-mas@example.com") == 429


def test_el_freno_por_direccion_se_suelta_solo(
    authenticated_client: TestClient,
    counter: InMemoryAttemptCounter,
) -> None:
    """La otra mitad de la misma decisión: el presupuesto por dirección vive
    en una ventana de un minuto, así que la siguiente es otro balde y nadie
    arrastra los fallos de un desconocido más allá de eso."""
    _register(authenticated_client)

    for index in range(20):
        _wrong_login(authenticated_client, email=f"quien{index}@example.com")

    assert _wrong_login(authenticated_client, email="otra@example.com") == 429

    # Pasado el minuto el intento pertenece a otra ventana, y la ventana va
    # en la clave del balde: vaciar los de esta dirección es exactamente lo
    # que hace el reloj.
    counter.hits = {
        bucket: hits
        for bucket, hits in counter.hits.items()
        if not bucket.startswith("login|ip|")
    }

    assert _right_login(authenticated_client) == 200


def test_registrarse_tiene_techo_por_direccion(
    authenticated_client: TestClient,
) -> None:
    """Aquí la cuenta creada *es* el costo, así que se cuenta todo intento y
    no solo los fallos."""
    codes = [
        _register(authenticated_client, email=f"nueva{index}@example.com").status_code
        for index in range(12)
    ]

    assert codes[:10] == [201] * 10
    assert codes[10:] == [429, 429]


def test_pedir_correo_tiene_techo_compartido(
    authenticated_client: TestClient,
) -> None:
    """El código de verificación y el enlace de recuperación comparten puerta
    porque comparten buzón: importa cuánto correo puede provocar un sitio, no
    por cuál de los dos endpoints lo pidió."""
    for index in range(10):
        authenticated_client.post(
            "/identity/verification/request",
            json={"email": f"quien{index}@example.com"},
        )

    for index in range(10):
        authenticated_client.post(
            "/identity/password/forgot",
            json={"email": f"otra{index}@example.com"},
        )

    refused = authenticated_client.post(
        "/identity/verification/request",
        json={"email": "una-mas@example.com"},
    )

    assert refused.status_code == 429


def test_adivinar_codigos_tiene_techo(authenticated_client: TestClient) -> None:
    """Cada reto ya se gasta a los cinco fallos; esto impide probar esos
    cinco contra todas las direcciones que a uno se le ocurran."""
    for index in range(30):
        authenticated_client.post(
            "/identity/verification/confirm",
            json={"email": f"quien{index}@example.com", "code": "000000"},
        )

    refused = authenticated_client.post(
        "/identity/verification/confirm",
        json={"email": "una-mas@example.com", "code": "000000"},
    )

    assert refused.status_code == 429
