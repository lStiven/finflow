"""Where a mailbox's refresh token lives.

Not in the connections table. A refresh token is a standing permission to
read someone's mail, so it goes in a secret store — encrypted at rest, with
its own access policy — and the rest of the application only ever sees a
short-lived access token derived from it.
"""

from __future__ import annotations

import json
from typing import Protocol

from mypy_boto3_secretsmanager.client import SecretsManagerClient

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.infrastructure.serialization import (
    as_json_object,
    read_string,
)


class MailboxTokenStore(Protocol):
    """Keeps the standing permission to read one mailbox."""

    def save_refresh_token(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
        refresh_token: str,
    ) -> None: ...

    def get_refresh_token(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> str | None: ...

    def delete(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> None:
        """Forget the permission. Called when a user disconnects — the cursor
        stays, so a later reconnect resumes, but the standing permission does
        not outlive their consent.
        """
        ...


def secret_name(*, provider: MailboxProvider, address: EmailAddress) -> str:
    return f"finflow/mailbox/{provider.value}/{address.value}"


class SecretsManagerTokenStore:
    """`MailboxTokenStore` backed by AWS Secrets Manager."""

    def __init__(self, *, client: SecretsManagerClient) -> None:
        self._client = client

    def save_refresh_token(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
        refresh_token: str,
    ) -> None:
        name = secret_name(provider=provider, address=address)
        payload = json.dumps({"refresh_token": refresh_token})

        try:
            self._client.create_secret(Name=name, SecretString=payload)
        except self._client.exceptions.ResourceExistsException:
            # Re-authorizing replaces the permission rather than adding one.
            self._client.put_secret_value(SecretId=name, SecretString=payload)

    def get_refresh_token(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> str | None:
        try:
            response = self._client.get_secret_value(
                SecretId=secret_name(provider=provider, address=address),
            )
        except self._client.exceptions.ResourceNotFoundException:
            return None

        raw = response.get("SecretString")

        if not raw:
            return None

        return read_string(as_json_object(json.loads(raw)), "refresh_token")

    def delete(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> None:
        try:
            self._client.delete_secret(
                SecretId=secret_name(provider=provider, address=address),
                ForceDeleteWithoutRecovery=True,
            )
        except self._client.exceptions.ResourceNotFoundException:
            return


class GmailAccessTokenProvider:
    """Turns the stored permission into a usable access token.

    Access tokens last about an hour, so this refreshes on every use rather
    than caching one: the alternative is a cache that occasionally hands out
    an expired token, and the refresh is one request.
    """

    def __init__(
        self,
        *,
        token_store: MailboxTokenStore,
        oauth_client: GmailOAuthClientProtocol,
    ) -> None:
        self._token_store = token_store
        self._oauth_client = oauth_client

    def access_token(self, address: EmailAddress) -> str:
        refresh_token = self._token_store.get_refresh_token(
            provider=MailboxProvider.GMAIL,
            address=address,
        )

        if refresh_token is None:
            # No stored permission is the same situation as a rejected one:
            # the user has to authorize again.
            raise MailboxAccessRevokedError(
                f"No stored authorization for {address.value}",
            )

        return self._oauth_client.refresh(refresh_token).access_token


class GmailOAuthClientProtocol(Protocol):
    """Just the part of the OAuth client this needs, so the token provider
    can be exercised without one.
    """

    def refresh(self, refresh_token: str) -> RefreshedTokens: ...


class RefreshedTokens(Protocol):
    @property
    def access_token(self) -> str: ...
