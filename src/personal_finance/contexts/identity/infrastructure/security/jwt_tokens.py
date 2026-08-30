from __future__ import annotations

from datetime import timedelta

import jwt

from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
)
from personal_finance.contexts.identity.domain.exceptions import InvalidAccessTokenError
from personal_finance.contexts.identity.domain.value_objects import Email, PersonName
from personal_finance.shared.domain.value_objects import PosixTime, UserId


# `sub`, `email` and `name` are the registered/OIDC spellings of exactly what
# they hold here, so a client can read them with any off-the-shelf decoder.
_SUBJECT_CLAIM = "sub"
_EMAIL_CLAIM = "email"
_NAME_CLAIM = "name"


class JWTTokenIssuer:
    """`TokenIssuer` backed by a signed, self-contained JWT.

    Stateless on purpose: verifying a token never touches DynamoDB, so an
    authenticated request costs one signature check, not a lookup. The cost is
    that a token cannot be revoked before it expires, which is an acceptable
    trade-off for a personal-finance backend with a short-lived access token.

    The payload is signed, not encrypted: anyone holding the token can read
    the email and name in it. That is the point — they are the holder's own —
    but it is why nothing else about the account goes in here.
    """

    def __init__(self, *, secret: str, algorithm: str, ttl_minutes: int) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl = timedelta(minutes=ttl_minutes)

    def issue(self, user: AuthenticatedUser) -> AccessToken:
        expires_at = PosixTime.from_datetime(PosixTime.now().to_datetime() + self._ttl)
        claims: dict[str, object] = {
            _SUBJECT_CLAIM: str(user.user_id.value),
            _EMAIL_CLAIM: user.email.value,
            "exp": expires_at.to_datetime(),
        }

        if user.name is not None:
            # Absent rather than null when unset: a claim that is not there
            # says "no name", which is what the account actually holds.
            claims[_NAME_CLAIM] = user.name.value

        token = jwt.encode(claims, self._secret, algorithm=self._algorithm)

        return AccessToken(value=token, expires_at=expires_at)

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.InvalidTokenError as error:
            raise InvalidAccessTokenError("Invalid or expired access token") from error

        return AuthenticatedUser(
            user_id=_read_user_id(payload),
            email=_read_email(payload),
            name=_read_name(payload),
        )


def _read_user_id(payload: dict[str, object]) -> UserId:
    subject = payload.get(_SUBJECT_CLAIM)

    if not isinstance(subject, str):
        raise InvalidAccessTokenError("Access token is missing its subject")

    try:
        return UserId.from_string(subject)
    except ValueError as error:
        raise InvalidAccessTokenError(
            "Access token subject is not a user id",
        ) from error


def _read_email(payload: dict[str, object]) -> Email:
    email = payload.get(_EMAIL_CLAIM)

    if not isinstance(email, str):
        raise InvalidAccessTokenError("Access token is missing its email claim")

    try:
        return Email(email)
    except ValueError as error:
        raise InvalidAccessTokenError(
            "Access token email claim is not an email address",
        ) from error


def _read_name(payload: dict[str, object]) -> PersonName | None:
    """A name that fails validation is dropped rather than rejected: it is a
    display convenience, and no request should stop working over it.
    """
    name = payload.get(_NAME_CLAIM)

    if not isinstance(name, str):
        return None

    try:
        return PersonName(name)
    except ValueError:
        return None
