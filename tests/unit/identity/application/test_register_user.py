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
from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    VerificationState,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    SecretHash,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


EMAIL = "person@example.com"
PASSWORD = "correct horse battery staple"
TICKET = "a-verification-ticket"


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


class FakeSecretHasher:
    """Reversible on purpose: a test wants to see which secret was presented,
    which a real hash is built to make impossible.
    """

    def hash(self, secret: str) -> SecretHash:
        return SecretHash(f"hashed:{secret}")

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        return hashed.value == f"hashed:{secret}"


class StubVerificationRepository:
    """Holds one spent-or-unspent ticket, so a test can register or be
    refused without going through the whole code exchange.
    """

    def __init__(self, *, ticket: str | None = TICKET, reusable: bool = False) -> None:
        self.ticket = ticket
        self.reusable = reusable
        self.consumed: list[Email] = []

    def find(self, email: Email) -> EmailVerification | None:
        del email

        return None

    def save(
        self,
        verification: EmailVerification,
        *,
        expected: VerificationState | None,
    ) -> bool:
        del verification, expected

        return True

    def consume_ticket(
        self,
        *,
        email: Email,
        ticket_hash: SecretHash,
        now: PosixTime,
    ) -> bool:
        del now

        if self.ticket is None or ticket_hash != SecretHash(f"hashed:{self.ticket}"):
            return False

        self.consumed.append(email)

        if not self.reusable:
            # One use only, exactly like the conditional delete behind the
            # real repository. `reusable` stands in for a second attempt that
            # went through the code exchange again.
            self.ticket = None

        return True


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
    verifications: StubVerificationRepository | None = None,
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
        verifications=verifications or StubVerificationRepository(),
        ticket_hasher=FakeSecretHasher(),
    )

    return use_case, repository, inbox_registrar, event_publisher


def test_registering_a_new_email_creates_an_account_and_logs_it_in() -> None:
    use_case, repository, _, event_publisher = _use_case()

    result = use_case.execute(
        RegisterUserCommand(
            email=EMAIL,
            password=PASSWORD,
            verification_token=TICKET,
        )
    )

    assert result.email == Email(EMAIL)
    assert result.access_token.value == "a-token"
    assert repository.by_email[Email(EMAIL)].id == result.user_id
    assert [type(event).__name__ for event in event_publisher.published] == [
        "UserRegistered",
    ]
    assert isinstance(event_publisher.published[0], UserRegistered)


def test_password_is_never_stored_in_plaintext() -> None:
    use_case, repository, _, _ = _use_case()

    use_case.execute(
        RegisterUserCommand(
            email=EMAIL,
            password=PASSWORD,
            verification_token=TICKET,
        )
    )

    stored_hash = repository.by_email[Email(EMAIL)].password_hash
    assert stored_hash.value != PASSWORD
    assert stored_hash == PasswordHash(f"hashed:{PASSWORD}")


def test_a_weak_password_is_rejected_before_anything_is_stored() -> None:
    use_case, repository, _, event_publisher = _use_case()

    with pytest.raises(WeakPasswordError):
        use_case.execute(
            RegisterUserCommand(
                email=EMAIL,
                password="short",
                verification_token=TICKET,
            )
        )

    assert repository.by_email == {}
    assert event_publisher.published == []


def test_registering_an_email_that_already_exists_is_rejected() -> None:
    # Reusable, so the second attempt fails on the taken address rather than
    # on the ticket the first one spent.
    use_case, _, _, event_publisher = _use_case(
        verifications=StubVerificationRepository(reusable=True),
    )
    use_case.execute(
        RegisterUserCommand(
            email=EMAIL,
            password=PASSWORD,
            verification_token=TICKET,
        )
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        use_case.execute(
            RegisterUserCommand(
                email=EMAIL,
                password="another password",
                verification_token=TICKET,
            )
        )

    # Only the first, successful registration published an event.
    assert len(event_publisher.published) == 1


def test_the_forwarding_address_is_assigned_even_with_no_senders_named() -> None:
    use_case, _, inbox_registrar, _ = _use_case()

    result = use_case.execute(
        RegisterUserCommand(
            email=EMAIL,
            password=PASSWORD,
            verification_token=TICKET,
        )
    )

    assert inbox_registrar.calls == [(result.user_id, InboxRegistration())]


def test_senders_named_at_registration_are_attached_to_the_new_user() -> None:
    use_case, _, inbox_registrar, _ = _use_case()
    inbox = InboxRegistration(allowed_domains=frozenset({"bank.com"}))

    result = use_case.execute(
        RegisterUserCommand(
            email=EMAIL,
            password=PASSWORD,
            inbox=inbox,
            verification_token=TICKET,
        ),
    )

    assert inbox_registrar.calls == [(result.user_id, inbox)]


def test_registration_without_a_usable_ticket_is_refused() -> None:
    use_case, repository, inbox_registrar, event_publisher = _use_case()

    with pytest.raises(EmailNotVerifiedError):
        use_case.execute(
            RegisterUserCommand(
                email=EMAIL,
                password=PASSWORD,
                verification_token="not-the-ticket",
            ),
        )

    # Nothing at all happened: no account, no forwarding address, no event.
    assert repository.by_email == {}
    assert inbox_registrar.calls == []
    assert event_publisher.published == []


def test_a_ticket_is_spent_by_the_registration_that_uses_it() -> None:
    verifications = StubVerificationRepository()
    use_case, _, _, _ = _use_case(verifications=verifications)
    command = RegisterUserCommand(
        email=EMAIL,
        password=PASSWORD,
        verification_token=TICKET,
    )

    use_case.execute(command)

    assert verifications.consumed == [Email(EMAIL)]

    # Replaying the same request cannot make a second account: the ticket the
    # first one spent is gone.
    with pytest.raises(EmailNotVerifiedError):
        use_case.execute(command)


def test_the_ticket_is_checked_before_the_address_is_looked_up() -> None:
    """A taken address answers 409, and that answer must cost the mailbox.

    Spending the ticket first is what keeps registration from being a way to
    ask which addresses are registered here: learning it requires having read
    the code that was mailed to the address in question.
    """
    existing = User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash("hashed:whatever"),
        registered_at=PosixTime.now(),
    )
    use_case, _, _, _ = _use_case(
        repository=InMemoryUserRepository(existing),
        verifications=StubVerificationRepository(ticket=None),
    )

    with pytest.raises(EmailNotVerifiedError):
        use_case.execute(
            RegisterUserCommand(
                email=EMAIL,
                password=PASSWORD,
                verification_token=TICKET,
            ),
        )
