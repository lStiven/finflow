"""Tying an OAuth callback back to the user who started it.

Google hands the callback a `state` value it received at the start and
otherwise ignores. That value is the only thing linking the returning
browser to an authenticated user, so it is signed: without a signature the
callback would happily attach a mailbox to whatever user id someone typed.

Signed rather than stored, so no table has to be provisioned, cleaned up, or
kept consistent for a value that lives for a few minutes.
"""

from __future__ import annotations

from datetime import timedelta

import jwt

from personal_finance.shared.domain.value_objects import PosixTime, UserId


ALGORITHM = "HS256"
DEFAULT_TTL_MINUTES = 15

_SUBJECT_CLAIM = "sub"
_PROVIDER_CLAIM = "provider"


class InvalidOAuthStateError(Exception):
    """The callback's state was missing, altered, or too old."""


class OAuthStateSigner:
    def __init__(
        self,
        *,
        secret: str,
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
    ) -> None:
        self._secret = secret
        self._ttl = timedelta(minutes=ttl_minutes)

    def issue(self, *, user_id: UserId, provider: str) -> str:
        expires_at = PosixTime.from_datetime(PosixTime.now().to_datetime() + self._ttl)

        return jwt.encode(
            {
                _SUBJECT_CLAIM: str(user_id.value),
                _PROVIDER_CLAIM: provider,
                "exp": expires_at.to_datetime(),
            },
            self._secret,
            algorithm=ALGORITHM,
        )

    def verify(self, state: str, *, provider: str) -> UserId:
        try:
            payload = jwt.decode(state, self._secret, algorithms=[ALGORITHM])
        except jwt.InvalidTokenError as error:
            raise InvalidOAuthStateError("Invalid or expired state") from error

        if payload.get(_PROVIDER_CLAIM) != provider:
            # A state minted for one provider must not authorize a connection
            # at another.
            raise InvalidOAuthStateError("State was issued for another provider")

        subject = payload.get(_SUBJECT_CLAIM)

        if not isinstance(subject, str):
            raise InvalidOAuthStateError("State carries no user")

        try:
            return UserId.from_string(subject)
        except ValueError as error:
            raise InvalidOAuthStateError("State subject is not a user id") from error
