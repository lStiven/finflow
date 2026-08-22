"""Connecting a mailbox by authorizing the provider.

Two steps from the user's side: they ask for a link while logged in, and they
come back from the provider. Everything between is a browser redirect.

The route lives under `/identity` because starting it requires knowing who
the user is, which is identity's job; the work it triggers is ingestion's,
reached through the same adapter boundary as everything else.
"""

from __future__ import annotations

import functools
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.connection_handlers import (
    ConnectMailboxUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxProvider,
    MailboxTemporarilyUnavailableError,
)
from personal_finance.contexts.ingestion.application.oauth_handlers import (
    CompleteAuthorizationCommand,
    CompleteMailboxAuthorizationUseCase,
    MailboxAuthorizationError,
    OAuthExchange,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.exchange import (
    GmailOAuthExchange,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.oauth_state import (
    InvalidOAuthStateError,
    OAuthStateSigner,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.registry import (
    get_gmail_oauth_client,
    get_token_store,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    DynamoDBMailboxConnectionRepository,
)
from personal_finance.contexts.ingestion.presentation.http.mailbox_router import (
    get_keep_alive_use_case,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/identity/mailboxes", tags=["identity"])

GMAIL = "gmail"


class AuthorizationLinkResponse(BaseModel):
    provider: str
    authorization_url: str


class AuthorizationCompleteResponse(BaseModel):
    address: str
    provider: str
    status: str
    # False means connected but not yet receiving notifications — the sweep
    # will pick it up, so it is worth surfacing rather than hiding.
    subscribed: bool


@functools.lru_cache(maxsize=1)
def _build_state_signer() -> OAuthStateSigner:
    secret = get_ingestion_settings().oauth_state_secret.get_secret_value()

    if not secret:
        raise ValueError(
            "INGESTION_OAUTH_STATE_SECRET is not set: an unsigned state would "
            "let a callback attach a mailbox to any user.",
        )

    return OAuthStateSigner(secret=secret)


def get_state_signer() -> OAuthStateSigner:
    return _build_state_signer()


@functools.lru_cache(maxsize=1)
def _build_exchanges() -> dict[MailboxProvider, OAuthExchange]:
    settings = get_ingestion_settings()

    if not settings.gmail_configured:
        return {}

    return {
        MailboxProvider.GMAIL: GmailOAuthExchange(
            oauth_client=get_gmail_oauth_client(),
        ),
    }


@functools.lru_cache(maxsize=1)
def _build_complete_use_case() -> CompleteMailboxAuthorizationUseCase:
    settings = get_ingestion_settings()

    return CompleteMailboxAuthorizationUseCase(
        exchanges=_build_exchanges(),
        connect_use_case=ConnectMailboxUseCase(
            connection_repository=DynamoDBMailboxConnectionRepository(
                client=get_dynamodb_client(),
                table_name=settings.mailbox_connections_table,
            ),
        ),
        keep_alive=get_keep_alive_use_case(),
        token_store=get_token_store(),
    )


def get_complete_use_case() -> CompleteMailboxAuthorizationUseCase:
    return _build_complete_use_case()


@router.get("/gmail/authorize", response_model=AuthorizationLinkResponse)
def start_gmail_authorization(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    signer: Annotated[OAuthStateSigner, Depends(get_state_signer)],
) -> AuthorizationLinkResponse:
    """Where to send the user to grant read access to their Gmail.

    Returns the URL rather than redirecting: the caller is a web front end
    that may want to explain what is about to be asked for before handing
    someone to Google.
    """
    settings = get_ingestion_settings()

    if not settings.gmail_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail is not configured in this deployment",
        )

    return AuthorizationLinkResponse(
        provider=GMAIL,
        authorization_url=get_gmail_oauth_client().authorization_url(
            state=signer.issue(user_id=user_id, provider=GMAIL),
        ),
    )


@router.get("/gmail/callback", response_model=AuthorizationCompleteResponse)
def complete_gmail_authorization(
    state: Annotated[str, Query(max_length=4_096)],
    signer: Annotated[OAuthStateSigner, Depends(get_state_signer)],
    use_case: Annotated[
        CompleteMailboxAuthorizationUseCase,
        Depends(get_complete_use_case),
    ],
    code: Annotated[str | None, Query(max_length=4_096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> AuthorizationCompleteResponse:
    """Where Google sends the user back.

    Deliberately not authenticated by a bearer token: the browser arriving
    here is following a redirect and carries no header. The signed `state` is
    what proves which user started this, which is why it is verified before
    anything else happens.
    """
    try:
        owner = signer.verify(state, provider=GMAIL)
    except InvalidOAuthStateError as state_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This authorization link is invalid or has expired",
        ) from state_error

    if error is not None or code is None:
        # The user said no, or Google refused. Nothing to store.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authorization was not granted ({error or 'no code returned'})",
        )

    try:
        result = use_case.execute(
            CompleteAuthorizationCommand(
                user_id=owner,
                provider=MailboxProvider.GMAIL,
                code=code,
            ),
        )
    except (MailboxAuthorizationError, MailboxAccessRevokedError) as auth_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(auth_error),
        ) from auth_error
    except MailboxTemporarilyUnavailableError as unavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google could not be reached; please try again",
        ) from unavailable

    return AuthorizationCompleteResponse(
        address=result.connection.address.value,
        provider=GMAIL,
        status=result.connection.status.value,
        subscribed=result.subscribed,
    )
