"""Google's OAuth 2.0 authorization-code flow, by hand.

Written against the HTTP endpoints rather than `google-auth-oauthlib` on
purpose: the flow is three requests, the library is a large dependency whose
types this project would have to work around, and doing it here keeps every
failure mode visible — which matters, because telling "try again later" apart
from "the user has to authorize again" is the whole point of this module.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from urllib.parse import urlencode

import httpx

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxTemporarilyUnavailableError,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.serialization import (
    as_json_object,
    read_string,
)


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

# Read-only, and the narrowest scope that still allows reading a message. The
# mailbox is someone's private correspondence; nothing here should ever be
# able to change one.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
# Identifies which account was actually authorized, so a user cannot connect
# a mailbox by typing an address they do not own.
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
SCOPES = (GMAIL_READONLY_SCOPE, EMAIL_SCOPE)

USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

# Google reports these when a grant is gone for good. Anything else is
# treated as temporary, because guessing wrong in that direction only costs a
# retry, while guessing wrong the other way silently disconnects a user.
_PERMANENT_ERRORS = frozenset(
    {"invalid_grant", "unauthorized_client", "invalid_client", "access_denied"},
)

DEFAULT_TIMEOUT_SECONDS = 20.0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class OAuthTokens:
    access_token: str
    expires_at: PosixTime
    # Only returned the first time a user consents, unless consent is forced.
    # A refresh that omits it means "keep the one you have".
    refresh_token: str | None = None


class GmailOAuthClient:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._transport = transport

    def authorization_url(self, *, state: str) -> str:
        """Where to send the user to grant access.

        `access_type=offline` with `prompt=consent` is what makes Google
        return a refresh token. Forcing the prompt looks redundant once the
        user has already consented, but without it a re-authorization comes
        back with no refresh token at all — and re-authorization is precisely
        when we need a new one.
        """
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                # Ties the callback back to the request that started it.
                # Without it the callback is an open invitation to attach
                # somebody else's mailbox to your account.
                "state": state,
            },
        )

        return f"{AUTHORIZATION_ENDPOINT}?{query}"

    def exchange_code(self, code: str) -> OAuthTokens:
        return self._token_request(
            {
                "code": code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    def refresh(self, refresh_token: str) -> OAuthTokens:
        tokens = self._token_request(
            {
                "refresh_token": refresh_token,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
            },
        )

        # A refresh response carries no refresh token; the caller must keep
        # using the one it already holds.
        return dataclasses.replace(tokens, refresh_token=None)

    def fetch_email_address(self, access_token: str) -> str | None:
        """Which mailbox the user actually authorized."""
        with self._client() as client:
            try:
                response = client.get(
                    USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            except httpx.HTTPError as error:
                raise MailboxTemporarilyUnavailableError(str(error)) from error

        if response.status_code in {401, 403}:
            raise MailboxAccessRevokedError("Google rejected the access token")

        if response.status_code >= 400:
            raise MailboxTemporarilyUnavailableError(
                f"userinfo failed with {response.status_code}",
            )

        return read_string(as_json_object(response.json()), "email")

    def _client(self) -> httpx.Client:
        if self._transport is not None:
            return self._transport

        return httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)

    def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        try:
            with self._client() as client:
                response = client.post(TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError as error:
            raise MailboxTemporarilyUnavailableError(str(error)) from error

        body = as_json_object(response.json() if response.content else {})

        if response.status_code >= 400:
            raise _token_error(response.status_code, body)

        access_token = body.get("access_token")

        if not isinstance(access_token, str):
            raise MailboxTemporarilyUnavailableError(
                "Google returned no access token",
            )

        expires_in = body.get("expires_in")
        seconds = expires_in if isinstance(expires_in, int) else 3_600
        refresh_token = body.get("refresh_token")

        return OAuthTokens(
            access_token=access_token,
            expires_at=PosixTime.from_epoch_seconds(
                PosixTime.now().as_epoch_seconds() + seconds,
            ),
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
        )


def _token_error(status_code: int, body: dict[str, Any]) -> Exception:
    error = read_string(body, "error")
    description = read_string(body, "error_description") or error or str(status_code)

    if error is not None and error in _PERMANENT_ERRORS:
        return MailboxAccessRevokedError(f"Google rejected the grant: {description}")

    if status_code in {400, 401} and error is not None:
        # A 4xx naming an error we do not recognise is still the grant being
        # refused, not a blip.
        return MailboxAccessRevokedError(f"Google rejected the grant: {description}")

    return MailboxTemporarilyUnavailableError(f"Google token endpoint: {description}")
