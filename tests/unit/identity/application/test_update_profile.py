import pytest

from personal_finance.contexts.identity.application.commands import UpdateProfileCommand
from personal_finance.contexts.identity.application.handlers import (
    GetProfileUseCase,
    UpdateProfileUseCase,
)
from personal_finance.contexts.identity.application.ports import AuthenticatedUser
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import UserNotFoundError
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
)
from personal_finance.shared.domain.value_objects import PosixTime


EMAIL = "person@example.com"


class InMemoryUserRepository:
    def __init__(self, *users: User) -> None:
        self.by_email: dict[Email, User] = {user.email: user for user in users}

    def add_if_new(self, user: User) -> User | None:
        raise NotImplementedError

    def find_by_email(self, email: Email) -> User | None:
        return self.by_email.get(email)

    def rename(self, user: User) -> bool:
        stored = self.by_email.get(user.email)

        if stored is None or stored.id != user.id:
            return False

        stored.name = user.name

        return True


def _user(*, email: str = EMAIL, name: str | None = None) -> User:
    return User.register(
        email=Email(email),
        password_hash=PasswordHash("$2b$12$abcdefg"),
        registered_at=PosixTime.now(),
        name=PersonName(name) if name is not None else None,
    )


def _caller(user: User) -> AuthenticatedUser:
    return AuthenticatedUser(user_id=user.id, email=user.email, name=user.name)


def test_the_name_is_written_and_reported_back() -> None:
    user = _user()
    repository = InMemoryUserRepository(user)
    use_case = UpdateProfileUseCase(repository=repository)

    profile = use_case.execute(
        caller=_caller(user),
        command=UpdateProfileCommand(name="Ada Lovelace"),
    )

    assert profile.name == PersonName("Ada Lovelace")
    assert repository.by_email[Email(EMAIL)].name == PersonName("Ada Lovelace")


def test_updating_the_name_leaves_the_email_and_password_alone() -> None:
    user = _user(name="Ada")
    repository = InMemoryUserRepository(user)

    UpdateProfileUseCase(repository=repository).execute(
        caller=_caller(user),
        command=UpdateProfileCommand(name="Ada Lovelace"),
    )

    stored = repository.by_email[Email(EMAIL)]
    assert stored.email == Email(EMAIL)
    assert stored.password_hash == PasswordHash("$2b$12$abcdefg")


def test_a_name_that_is_only_whitespace_is_rejected() -> None:
    user = _user()
    use_case = UpdateProfileUseCase(repository=InMemoryUserRepository(user))

    with pytest.raises(ValueError, match="cannot be empty"):
        use_case.execute(
            caller=_caller(user),
            command=UpdateProfileCommand(name="   "),
        )


def test_a_token_for_an_account_that_no_longer_exists_edits_nothing() -> None:
    user = _user()
    use_case = UpdateProfileUseCase(repository=InMemoryUserRepository())

    with pytest.raises(UserNotFoundError):
        use_case.execute(
            caller=_caller(user),
            command=UpdateProfileCommand(name="Ada Lovelace"),
        )


def test_a_token_whose_id_does_not_match_the_stored_account_is_refused() -> None:
    # The address was taken over by another account after the token was
    # issued: that token must not reach the one holding it now.
    current = _user()
    repository = InMemoryUserRepository(current)
    stale = _user()

    with pytest.raises(UserNotFoundError):
        UpdateProfileUseCase(repository=repository).execute(
            caller=_caller(stale),
            command=UpdateProfileCommand(name="Ada Lovelace"),
        )

    assert repository.by_email[Email(EMAIL)].name is None


def test_a_profile_is_read_from_storage_not_from_the_token() -> None:
    # The token still says what the account was called when it was issued;
    # the answer must be what the account is called now.
    user = _user(name="Ada")
    caller = _caller(user)
    user.rename(PersonName("Ada Lovelace"))

    profile = GetProfileUseCase(repository=InMemoryUserRepository(user)).execute(
        caller=caller,
    )

    assert profile.name == PersonName("Ada Lovelace")
    assert profile.email == Email(EMAIL)
    assert profile.user_id == user.id


def test_reading_a_profile_for_a_deleted_account_fails_rather_than_inventing_one() -> (
    None
):
    user = _user()

    with pytest.raises(UserNotFoundError):
        GetProfileUseCase(repository=InMemoryUserRepository()).execute(
            caller=_caller(user),
        )
