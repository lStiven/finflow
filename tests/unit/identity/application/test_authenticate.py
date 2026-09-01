"""Turning a bearer token into who is calling.

The signature is the cheap half. The half these tests are about is that the
account behind the token is still there and its credentials have not moved —
which is the only thing that makes a password reset end other sessions.
"""

import pytest

from personal_finance.contexts.identity.application.handlers import AuthenticateUseCase
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import InvalidAccessTokenError
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
        raise NotImplementedError

    def change_password(self, user: User) -> bool:
        raise NotImplementedError


class DictionaryTokenIssuer:
    """Hands out opaque strings and remembers what each one claimed, so a test
    can hold a token from before a change and present it after.
    """

    def __init__(self) -> None:
        self.claims: dict[str, AuthenticatedUser] = {}

    def issue(self, user: AuthenticatedUser) -> AccessToken:
        token = f"token-{len(self.claims)}"
        self.claims[token] = user

        return AccessToken(value=token, expires_at=PosixTime.now())

    def verify(self, token: str) -> AuthenticatedUser:
        claims = self.claims.get(token)

        if claims is None:
            raise InvalidAccessTokenError("Invalid or expired access token")

        return claims


def _account() -> User:
    user = User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash("hashed:whatever"),
        registered_at=PosixTime.from_epoch_seconds(1_700_000_000),
        name=PersonName("Ada"),
    )
    user.pull_events()

    return user


def _claims(user: User) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        name=user.name,
        credential_version=user.credential_version,
    )


def _use_case(
    users: InMemoryUserRepository,
    issuer: DictionaryTokenIssuer,
) -> AuthenticateUseCase:
    return AuthenticateUseCase(token_issuer=issuer, repository=users)


def test_a_current_token_names_its_account() -> None:
    account = _account()
    issuer = DictionaryTokenIssuer()
    token = issuer.issue(_claims(account))

    caller = _use_case(InMemoryUserRepository(account), issuer).execute(token.value)

    assert caller.user_id == account.id
    assert caller.email == account.email


def test_a_token_issued_before_a_password_change_stops_working() -> None:
    account = _account()
    issuer = DictionaryTokenIssuer()
    token = issuer.issue(_claims(account))
    users = InMemoryUserRepository(account)

    account.change_password(PasswordHash("hashed:new"))

    with pytest.raises(InvalidAccessTokenError):
        _use_case(users, issuer).execute(token.value)


def test_a_token_issued_after_the_change_works() -> None:
    account = _account()
    issuer = DictionaryTokenIssuer()
    users = InMemoryUserRepository(account)
    account.change_password(PasswordHash("hashed:new"))
    token = issuer.issue(_claims(account))

    assert _use_case(users, issuer).execute(token.value).user_id == account.id


def test_a_token_for_an_account_that_is_gone_stops_working() -> None:
    account = _account()
    issuer = DictionaryTokenIssuer()
    token = issuer.issue(_claims(account))

    with pytest.raises(InvalidAccessTokenError):
        _use_case(InMemoryUserRepository(), issuer).execute(token.value)


def test_a_token_cannot_reach_an_account_that_took_over_the_address() -> None:
    issuer = DictionaryTokenIssuer()
    token = issuer.issue(_claims(_account()))
    replacement = _account()

    with pytest.raises(InvalidAccessTokenError):
        _use_case(InMemoryUserRepository(replacement), issuer).execute(token.value)


def test_the_name_comes_from_storage_rather_than_from_the_token() -> None:
    # A rename should show up on the next request, not on the next login.
    account = _account()
    issuer = DictionaryTokenIssuer()
    token = issuer.issue(_claims(account))
    account.rename(PersonName("Ada Lovelace"))

    caller = _use_case(InMemoryUserRepository(account), issuer).execute(token.value)

    assert caller.name == PersonName("Ada Lovelace")


def test_a_token_the_issuer_refuses_is_refused_here_too() -> None:
    with pytest.raises(InvalidAccessTokenError):
        _use_case(
            InMemoryUserRepository(_account()),
            DictionaryTokenIssuer(),
        ).execute("not-a-token")
