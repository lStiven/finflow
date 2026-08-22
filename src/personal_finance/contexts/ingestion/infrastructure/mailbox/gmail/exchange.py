from __future__ import annotations

from personal_finance.contexts.ingestion.application.oauth_handlers import (
    AuthorizedMailbox,
    MailboxAuthorizationError,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.oauth import (
    GmailOAuthClient,
)


class GmailOAuthExchange:
    """Swaps a callback code for a lasting permission to read one mailbox."""

    def __init__(self, *, oauth_client: GmailOAuthClient) -> None:
        self._oauth_client = oauth_client

    def complete(self, code: str) -> AuthorizedMailbox:
        tokens = self._oauth_client.exchange_code(code)

        if tokens.refresh_token is None:
            # Without one there is no lasting permission, only an hour of
            # access — and an hour later the mailbox would silently stop
            # working. Google omits it when the user has consented before and
            # the request did not force the prompt.
            raise MailboxAuthorizationError(
                "Google returned no refresh token; the consent prompt must be "
                "forced so a lasting authorization is issued",
            )

        address = self._oauth_client.fetch_email_address(tokens.access_token)

        if address is None:
            raise MailboxAuthorizationError(
                "Google did not say which mailbox was authorized",
            )

        return AuthorizedMailbox(
            address=EmailAddress(address),
            refresh_token=tokens.refresh_token,
        )
