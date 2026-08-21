import pytest

from personal_finance.contexts.identity.application.commands import LoginCommand
from personal_finance.contexts.identity.application.handlers import LoginUseCase
from personal_finance.contexts.identity.application.ports import AccessToken
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import InvalidCredentialsError
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


EMAIL = "person@example.com"
PASSWORD = "correct horse battery staple"


class InMemoryUserRepository:
    def __init__(self, *users: User) -> None:
        self.by_email: dict[Email, User] = {user.email: user for user in users}

    def add_if_new(self, user: User) -> User | None:
        raise NotImplementedError

    def find_by_email(self, email: Email) -> User | None:
        return self.by_email.get(email)


class FakeHasher:
    def __init__(self) -> None:
        self.verify_calls: list[tuple[str, PasswordHash]] = []

    def hash(self, password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{password}")

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        self.verify_calls.append((password, hashed))

        return hashed.value == f"hashed:{password}"


class FakeTokenIssuer:
    def issue(self, user_id: UserId) -> AccessToken:
        return AccessToken(
            value=f"token-for-{user_id.value}", expires_at=PosixTime.now()
        )

    def verify(self, token: str) -> UserId:
        raise NotImplementedError


def _registered_user() -> User:
    return User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash(f"hashed:{PASSWORD}"),
        registered_at=PosixTime.now(),
    )


def test_correct_credentials_issue_a_token_for_the_right_user() -> None:
    user = _registered_user()
    use_case = LoginUseCase(
        repository=InMemoryUserRepository(user),
        hasher=FakeHasher(),
        token_issuer=FakeTokenIssuer(),
    )

    result = use_case.execute(LoginCommand(email=EMAIL, password=PASSWORD))

    assert result.user_id == user.id
    assert result.access_token.value == f"token-for-{user.id.value}"


def test_wrong_password_is_rejected() -> None:
    use_case = LoginUseCase(
        repository=InMemoryUserRepository(_registered_user()),
        hasher=FakeHasher(),
        token_issuer=FakeTokenIssuer(),
    )

    with pytest.raises(InvalidCredentialsError):
        use_case.execute(LoginCommand(email=EMAIL, password="wrong password"))


def test_unknown_email_is_rejected_with_the_exact_same_error() -> None:
    use_case = LoginUseCase(
        repository=InMemoryUserRepository(),
        hasher=FakeHasher(),
        token_issuer=FakeTokenIssuer(),
    )

    with pytest.raises(InvalidCredentialsError, match="Invalid email or password"):
        use_case.execute(LoginCommand(email="nobody@example.com", password=PASSWORD))


def test_unknown_email_still_runs_a_full_hash_comparison() -> None:
    # A login attempt for an account that does not exist must cost the same
    # as one for an account that does, or the response time alone would tell
    # a caller which emails are registered.
    hasher = FakeHasher()
    use_case = LoginUseCase(
        repository=InMemoryUserRepository(),
        hasher=hasher,
        token_issuer=FakeTokenIssuer(),
    )

    with pytest.raises(InvalidCredentialsError):
        use_case.execute(LoginCommand(email="nobody@example.com", password=PASSWORD))

    assert len(hasher.verify_calls) == 1
