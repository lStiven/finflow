from collections.abc import Sequence

import pytest

from personal_finance.contexts.identity.application.commands import RegisterUserCommand
from personal_finance.contexts.identity.application.handlers import RegisterUserUseCase
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


EMAIL = "person@example.com"
PASSWORD = "correct horse battery staple"


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


class FakeHasher:
    def hash(self, password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{password}")

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        return hashed.value == f"hashed:{password}"


class FakeTokenIssuer:
    def __init__(self) -> None:
        self.issued_for: list[AuthenticatedUser] = []

    def issue(self, user: AuthenticatedUser) -> AccessToken:
        self.issued_for.append(user)

        return AccessToken(value="a-token", expires_at=PosixTime.now())

    def verify(self, token: str) -> AuthenticatedUser:
        raise NotImplementedError


class RecordingInboxRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[UserId, InboxRegistration]] = []

    def register(
        self,
        *,
        user_id: UserId,
        inbox: InboxRegistration,
    ) -> RegisteredInbox:
        self.calls.append((user_id, inbox))

        return RegisteredInbox(
            address=f"inbox+{user_id.value}@test",
            allowed_domains=inbox.allowed_domains,
            allowed_addresses=inbox.allowed_addresses,
        )


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _use_case(
    *,
    repository: InMemoryUserRepository | None = None,
    inbox_registrar: RecordingInboxRegistrar | None = None,
    event_publisher: RecordingEventPublisher | None = None,
) -> tuple[
    RegisterUserUseCase,
    InMemoryUserRepository,
    RecordingInboxRegistrar,
    RecordingEventPublisher,
]:
    repository = repository or InMemoryUserRepository()
    inbox_registrar = inbox_registrar or RecordingInboxRegistrar()
    event_publisher = event_publisher or RecordingEventPublisher()
    use_case = RegisterUserUseCase(
        repository=repository,
        hasher=FakeHasher(),
        token_issuer=FakeTokenIssuer(),
        inbox_registrar=inbox_registrar,
        event_publisher=event_publisher,
    )

    return use_case, repository, inbox_registrar, event_publisher


def test_registering_a_new_email_creates_an_account_and_logs_it_in() -> None:
    use_case, repository, _, event_publisher = _use_case()

    result = use_case.execute(RegisterUserCommand(email=EMAIL, password=PASSWORD))

    assert result.email == Email(EMAIL)
    assert result.access_token.value == "a-token"
    assert repository.by_email[Email(EMAIL)].id == result.user_id
    assert [type(event).__name__ for event in event_publisher.published] == [
        "UserRegistered",
    ]
    assert isinstance(event_publisher.published[0], UserRegistered)


def test_password_is_never_stored_in_plaintext() -> None:
    use_case, repository, _, _ = _use_case()

    use_case.execute(RegisterUserCommand(email=EMAIL, password=PASSWORD))

    stored_hash = repository.by_email[Email(EMAIL)].password_hash
    assert stored_hash.value != PASSWORD
    assert stored_hash == PasswordHash(f"hashed:{PASSWORD}")


def test_a_weak_password_is_rejected_before_anything_is_stored() -> None:
    use_case, repository, _, event_publisher = _use_case()

    with pytest.raises(WeakPasswordError):
        use_case.execute(RegisterUserCommand(email=EMAIL, password="short"))

    assert repository.by_email == {}
    assert event_publisher.published == []


def test_registering_an_email_that_already_exists_is_rejected() -> None:
    use_case, _, _, event_publisher = _use_case()
    use_case.execute(RegisterUserCommand(email=EMAIL, password=PASSWORD))

    with pytest.raises(EmailAlreadyRegisteredError):
        use_case.execute(RegisterUserCommand(email=EMAIL, password="another password"))

    # Only the first, successful registration published an event.
    assert len(event_publisher.published) == 1


def test_the_forwarding_address_is_assigned_even_with_no_senders_named() -> None:
    use_case, _, inbox_registrar, _ = _use_case()

    result = use_case.execute(RegisterUserCommand(email=EMAIL, password=PASSWORD))

    assert inbox_registrar.calls == [(result.user_id, InboxRegistration())]


def test_senders_named_at_registration_are_attached_to_the_new_user() -> None:
    use_case, _, inbox_registrar, _ = _use_case()
    inbox = InboxRegistration(allowed_domains=frozenset({"bank.com"}))

    result = use_case.execute(
        RegisterUserCommand(email=EMAIL, password=PASSWORD, inbox=inbox),
    )

    assert inbox_registrar.calls == [(result.user_id, inbox)]
