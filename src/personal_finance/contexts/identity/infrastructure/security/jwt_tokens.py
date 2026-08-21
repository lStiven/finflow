from __future__ import annotations

from datetime import timedelta

import jwt

from personal_finance.contexts.identity.application.ports import AccessToken
from personal_finance.contexts.identity.domain.exceptions import InvalidAccessTokenError
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_SUBJECT_CLAIM = "sub"


class JWTTokenIssuer:
    """`TokenIssuer` backed by a signed, self-contained JWT.

    Stateless on purpose: verifying a token never touches DynamoDB, so an
    authenticated request costs one signature check, not a lookup. The cost is
    that a token cannot be revoked before it expires, which is an acceptable
    trade-off for a personal-finance backend with a short-lived access token.
    """

    def __init__(self, *, secret: str, algorithm: str, ttl_minutes: int) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl = timedelta(minutes=ttl_minutes)

    def issue(self, user_id: UserId) -> AccessToken:
        expires_at = PosixTime.from_datetime(PosixTime.now().to_datetime() + self._ttl)
        token = jwt.encode(
            {_SUBJECT_CLAIM: str(user_id.value), "exp": expires_at.to_datetime()},
            self._secret,
            algorithm=self._algorithm,
        )

        return AccessToken(value=token, expires_at=expires_at)

    def verify(self, token: str) -> UserId:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except jwt.InvalidTokenError as error:
            raise InvalidAccessTokenError("Invalid or expired access token") from error

        subject = payload.get(_SUBJECT_CLAIM)

        if not isinstance(subject, str):
            raise InvalidAccessTokenError("Access token is missing its subject")

        try:
            return UserId.from_string(subject)
        except ValueError as error:
            raise InvalidAccessTokenError(
                "Access token subject is not a user id",
            ) from error
