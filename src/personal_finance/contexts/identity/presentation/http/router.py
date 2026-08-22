from __future__ import annotations

import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from personal_finance.contexts.identity.application.commands import (
    AddInboxesCommand,
    LoginCommand,
    RegisterUserCommand,
)
from personal_finance.contexts.identity.application.handlers import (
    LoginUseCase,
    RegisterUserUseCase,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    AddInboxesUseCase,
    ListInboxesUseCase,
)
from personal_finance.contexts.identity.application.integration_events import (
    IdentityIntegrationEventTranslator,
)
from personal_finance.contexts.identity.application.mailbox_handlers import (
    BackfillMailboxesUseCase,
    ConnectMailboxCommand,
    ConnectMailboxUseCase,
    DisconnectMailboxCommand,
    DisconnectMailboxUseCase,
    ListMailboxesUseCase,
    RefreshMailboxesUseCase,
)
from personal_finance.contexts.identity.application.ports import InboxRegistration
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.infrastructure.inbox.ingestion_inbox_registrar import (  # noqa: E501
    IngestionInboxRegistrar,
)
from personal_finance.contexts.identity.infrastructure.inbox.ingestion_mailbox_connector import (  # noqa: E501
    IngestionMailboxConnector,
)
from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    DynamoDBUserRepository,
)
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.contexts.identity.infrastructure.security.password_hashing import (  # noqa: E501
    BcryptPasswordHasher,
)
from personal_finance.contexts.ingestion.application.connection_handlers import (
    ConnectMailboxUseCase as IngestionConnectMailboxUseCase,
    DisconnectMailboxUseCase as IngestionDisconnectMailboxUseCase,
    ListMailboxConnectionsUseCase,
    MailboxAlreadyConnectedError,
    MailboxNotConnectedError,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    ListUserInboxesUseCase,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.application.subscription_handlers import (
    RefreshUserMailboxesUseCase,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.contexts.ingestion.presentation.http.mailbox_router import (
    build_backfill_use_case,
    build_connection_repository,
    build_sync_use_case,
    get_keep_alive_use_case,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.eventbridge import (
    EventBridgeEventPublisher,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_eventbridge_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_identity_settings,
    get_ingestion_settings,
)
from personal_finance.shared.infrastructure.observability.composite_event_publisher import (  # noqa: E501
    CompositeEventPublisher,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


router = APIRouter(prefix="/identity", tags=["identity"])

_bearer_scheme = HTTPBearer(auto_error=False)


def _looks_like_an_email(value: str) -> str:
    if "@" not in value.strip():
        raise ValueError(f"Invalid email address: {value!r}")

    return value


class InboxRegistrationPayload(BaseModel):
    """One inbound address to listen on, plus the senders trusted for it.
    Every field beyond the address is optional — an inbox can be registered
    bare and approved later.
    """

    address: str = Field(min_length=3, max_length=320)
    allowed_domains: list[str] = Field(default_factory=list)
    allowed_addresses: list[str] = Field(default_factory=list)

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        return _looks_like_an_email(value)

    @field_validator("allowed_addresses")
    @classmethod
    def _validate_allowed_addresses(cls, value: list[str]) -> list[str]:
        return [_looks_like_an_email(address) for address in value]

    def to_registration(self) -> InboxRegistration:
        return InboxRegistration(
            address=self.address,
            allowed_domains=frozenset(self.allowed_domains),
            allowed_addresses=frozenset(self.allowed_addresses),
        )


class RegisterPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)
    # Not required: an account can be created bare, with inboxes attached
    # later through `POST /identity/inboxes`.
    inboxes: list[InboxRegistrationPayload] = Field(
        default_factory=lambda: list[InboxRegistrationPayload](),
    )


class LoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AddInboxesPayload(BaseModel):
    inboxes: list[InboxRegistrationPayload] = Field(min_length=1)


class MailboxPayload(BaseModel):
    """A mailbox the user authorizes us to read.

    No token here, and there never will be: the real flow finishes an OAuth
    exchange with the provider and only then calls this, so the secret stays
    in the provider adapter's store.
    """

    address: str = Field(min_length=3, max_length=320)
    provider: str = Field(min_length=1, max_length=32)

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        return _looks_like_an_email(value)


class ConnectMailboxPayload(MailboxPayload):
    """Connecting also says who we may read the mailbox for.

    Both lists may be empty — the mailbox is then connected but read for
    nobody, which is the safe default rather than a broken one.
    """

    allowed_domains: list[str] = Field(
        default_factory=lambda: list[str](),
    )
    allowed_addresses: list[str] = Field(
        default_factory=lambda: list[str](),
    )

    @field_validator("allowed_addresses")
    @classmethod
    def _validate_allowed_addresses(cls, value: list[str]) -> list[str]:
        return [_looks_like_an_email(address) for address in value]


class ConnectedMailboxResponse(BaseModel):
    address: str
    provider: str
    status: str
    needs_attention: bool


class MailboxListResponse(BaseModel):
    mailboxes: list[ConnectedMailboxResponse]


class MailboxRefreshResponse(BaseModel):
    mailboxes: int
    fetched: int
    accepted: int
    needs_reauth: int


class MailboxBackfillResponse(BaseModel):
    mailboxes: int
    fetched: int
    accepted: int
    duplicates: int
    needs_reauth: int
    since: str


class AccessTokenResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class CurrentUserResponse(BaseModel):
    user_id: str


class RegisteredInboxResponse(BaseModel):
    address: str
    allowed_domains: list[str]
    allowed_addresses: list[str]


class InboxListResponse(BaseModel):
    inboxes: list[RegisteredInboxResponse]


@functools.lru_cache(maxsize=1)
def _build_inbox_registrar() -> IngestionInboxRegistrar:
    ingestion_settings = get_ingestion_settings()
    repository = DynamoDBUserInboxRepository(
        client=get_dynamodb_client(),
        table_name=ingestion_settings.user_inboxes_table,
    )

    return IngestionInboxRegistrar(
        use_case=RegisterUserInboxUseCase(inbox_repository=repository),
        list_use_case=ListUserInboxesUseCase(inbox_repository=repository),
    )


@functools.lru_cache(maxsize=1)
def _build_event_publisher() -> EventPublisher:
    """Logging first, so the local audit trail is already written if the bus
    rejects the publish.
    """
    return CompositeEventPublisher(
        LoggingEventPublisher(),
        EventBridgeEventPublisher(
            client=get_eventbridge_client(),
            event_bus_name=get_ingestion_settings().event_bus_name,
            translator=IdentityIntegrationEventTranslator(),
        ),
    )


@functools.lru_cache(maxsize=1)
def _build_hasher() -> BcryptPasswordHasher:
    return BcryptPasswordHasher()


@functools.lru_cache(maxsize=1)
def _build_token_issuer() -> JWTTokenIssuer:
    settings = get_identity_settings()
    secret = settings.jwt_secret.get_secret_value()

    if not secret:
        raise ValueError(
            "IDENTITY_JWT_SECRET is not set: every access token would be "
            "forgeable. Set it to a long random value before starting the API.",
        )

    return JWTTokenIssuer(
        secret=secret,
        algorithm=settings.jwt_algorithm,
        ttl_minutes=settings.access_token_ttl_minutes,
    )


@functools.lru_cache(maxsize=1)
def _build_user_repository() -> DynamoDBUserRepository:
    settings = get_identity_settings()

    return DynamoDBUserRepository(
        client=get_dynamodb_client(),
        table_name=settings.users_table,
    )


@functools.lru_cache(maxsize=1)
def _build_register_use_case() -> RegisterUserUseCase:
    return RegisterUserUseCase(
        repository=_build_user_repository(),
        hasher=_build_hasher(),
        token_issuer=_build_token_issuer(),
        inbox_registrar=_build_inbox_registrar(),
        event_publisher=_build_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_login_use_case() -> LoginUseCase:
    return LoginUseCase(
        repository=_build_user_repository(),
        hasher=_build_hasher(),
        token_issuer=_build_token_issuer(),
    )


@functools.lru_cache(maxsize=1)
def _build_add_inboxes_use_case() -> AddInboxesUseCase:
    return AddInboxesUseCase(inbox_registrar=_build_inbox_registrar())


@functools.lru_cache(maxsize=1)
def _build_list_inboxes_use_case() -> ListInboxesUseCase:
    return ListInboxesUseCase(inbox_reader=_build_inbox_registrar())


@functools.lru_cache(maxsize=1)
def _build_mailbox_connector() -> IngestionMailboxConnector:
    repository = build_connection_repository()

    return IngestionMailboxConnector(
        connect_use_case=IngestionConnectMailboxUseCase(
            connection_repository=repository,
        ),
        disconnect_use_case=IngestionDisconnectMailboxUseCase(
            connection_repository=repository,
        ),
        list_use_case=ListMailboxConnectionsUseCase(connection_repository=repository),
        refresh_use_case=RefreshUserMailboxesUseCase(
            connection_repository=repository,
            sync_use_case=build_sync_use_case(),
            keep_alive=get_keep_alive_use_case(),
        ),
        backfill_use_case=build_backfill_use_case(),
    )


@functools.lru_cache(maxsize=1)
def _build_connect_mailbox_use_case() -> ConnectMailboxUseCase:
    return ConnectMailboxUseCase(
        connector=_build_mailbox_connector(),
        inbox_registrar=_build_inbox_registrar(),
    )


@functools.lru_cache(maxsize=1)
def _build_disconnect_mailbox_use_case() -> DisconnectMailboxUseCase:
    return DisconnectMailboxUseCase(connector=_build_mailbox_connector())


@functools.lru_cache(maxsize=1)
def _build_list_mailboxes_use_case() -> ListMailboxesUseCase:
    return ListMailboxesUseCase(connector=_build_mailbox_connector())


def get_register_use_case() -> RegisterUserUseCase:
    return _build_register_use_case()


def get_login_use_case() -> LoginUseCase:
    return _build_login_use_case()


def get_add_inboxes_use_case() -> AddInboxesUseCase:
    return _build_add_inboxes_use_case()


def get_list_inboxes_use_case() -> ListInboxesUseCase:
    return _build_list_inboxes_use_case()


def get_connect_mailbox_use_case() -> ConnectMailboxUseCase:
    return _build_connect_mailbox_use_case()


def get_disconnect_mailbox_use_case() -> DisconnectMailboxUseCase:
    return _build_disconnect_mailbox_use_case()


def get_list_mailboxes_use_case() -> ListMailboxesUseCase:
    return _build_list_mailboxes_use_case()


@functools.lru_cache(maxsize=1)
def _build_refresh_mailboxes_use_case() -> RefreshMailboxesUseCase:
    return RefreshMailboxesUseCase(connector=_build_mailbox_connector())


def get_refresh_mailboxes_use_case() -> RefreshMailboxesUseCase:
    return _build_refresh_mailboxes_use_case()


@functools.lru_cache(maxsize=1)
def _build_backfill_mailboxes_use_case() -> BackfillMailboxesUseCase:
    return BackfillMailboxesUseCase(connector=_build_mailbox_connector())


def get_backfill_mailboxes_use_case() -> BackfillMailboxesUseCase:
    return _build_backfill_mailboxes_use_case()


def get_token_issuer() -> JWTTokenIssuer:
    return _build_token_issuer()


def get_current_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    token_issuer: Annotated[JWTTokenIssuer, Depends(get_token_issuer)],
) -> UserId:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid access token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        return token_issuer.verify(credentials.credentials)
    except InvalidAccessTokenError as error:
        raise unauthorized from error


def _as_response(
    *,
    user_id: UserId,
    access_token_value: str,
    expires_at_epoch_seconds: int,
) -> AccessTokenResponse:
    return AccessTokenResponse(
        user_id=str(user_id.value),
        access_token=access_token_value,
        expires_at=expires_at_epoch_seconds,
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AccessTokenResponse,
)
def register(
    payload: RegisterPayload,
    use_case: Annotated[RegisterUserUseCase, Depends(get_register_use_case)],
) -> AccessTokenResponse:
    try:
        result = use_case.execute(
            RegisterUserCommand(
                email=payload.email,
                password=payload.password,
                inboxes=tuple(inbox.to_registration() for inbox in payload.inboxes),
            ),
        )
    except WeakPasswordError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except EmailAlreadyRegisteredError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    return _as_response(
        user_id=result.user_id,
        access_token_value=result.access_token.value,
        expires_at_epoch_seconds=result.access_token.expires_at.as_epoch_seconds(),
    )


@router.post("/login", response_model=AccessTokenResponse)
def login(
    payload: LoginPayload,
    use_case: Annotated[LoginUseCase, Depends(get_login_use_case)],
) -> AccessTokenResponse:
    try:
        result = use_case.execute(
            LoginCommand(email=payload.email, password=payload.password),
        )
    except (InvalidCredentialsError, ValueError) as error:
        # Same response for "unknown email" and "wrong password" — and for a
        # malformed email — so a caller learns nothing about which it was.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from error

    return _as_response(
        user_id=result.user_id,
        access_token_value=result.access_token.value,
        expires_at_epoch_seconds=result.access_token.expires_at.as_epoch_seconds(),
    )


@router.post("/inboxes", status_code=status.HTTP_204_NO_CONTENT)
def add_inboxes(
    payload: AddInboxesPayload,
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[AddInboxesUseCase, Depends(get_add_inboxes_use_case)],
) -> None:
    use_case.execute(
        user_id=user_id,
        command=AddInboxesCommand(
            inboxes=tuple(inbox.to_registration() for inbox in payload.inboxes),
        ),
    )


@router.get("/inboxes", response_model=InboxListResponse)
def list_inboxes(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[ListInboxesUseCase, Depends(get_list_inboxes_use_case)],
) -> InboxListResponse:
    """The caller's own inboxes. Whose inboxes to read comes from the verified
    token, never from the request, so there is nothing to authorize beyond
    being authenticated.
    """
    inboxes = use_case.execute(user_id=user_id)

    return InboxListResponse(
        inboxes=[
            RegisteredInboxResponse(
                address=inbox.address,
                allowed_domains=sorted(inbox.allowed_domains),
                allowed_addresses=sorted(inbox.allowed_addresses),
            )
            for inbox in inboxes
        ],
    )


@router.post("/mailboxes", status_code=status.HTTP_204_NO_CONTENT)
def connect_mailbox(
    payload: ConnectMailboxPayload,
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[ConnectMailboxUseCase, Depends(get_connect_mailbox_use_case)],
) -> None:
    """Authorize us to read one of your mailboxes, and say who for.

    From here on the provider notifies us when that mailbox changes, and we
    read only the senders listed here — nothing else in it is ever fetched.
    Leave the lists empty and the mailbox is connected but read for nobody.
    """
    try:
        use_case.execute(
            user_id=user_id,
            command=ConnectMailboxCommand(
                address=payload.address,
                provider=payload.provider,
                allowed_domains=frozenset(payload.allowed_domains),
                allowed_addresses=frozenset(payload.allowed_addresses),
            ),
        )
    except MailboxAlreadyConnectedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That mailbox is already connected",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.delete("/mailboxes", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_mailbox(
    payload: MailboxPayload,
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[
        DisconnectMailboxUseCase,
        Depends(get_disconnect_mailbox_use_case),
    ],
) -> None:
    """Stop reading a mailbox. We keep the record so a later reconnect resumes
    instead of re-reading everything, but nothing is read while it is off.
    """
    try:
        use_case.execute(
            user_id=user_id,
            command=DisconnectMailboxCommand(
                address=payload.address,
                provider=payload.provider,
            ),
        )
    except MailboxNotConnectedError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That mailbox is not connected",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.get("/mailboxes", response_model=MailboxListResponse)
def list_mailboxes(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[ListMailboxesUseCase, Depends(get_list_mailboxes_use_case)],
) -> MailboxListResponse:
    return MailboxListResponse(
        mailboxes=[
            ConnectedMailboxResponse(
                address=mailbox.address,
                provider=mailbox.provider,
                status=mailbox.status,
                needs_attention=mailbox.needs_attention,
            )
            for mailbox in use_case.execute(user_id=user_id)
        ],
    )


@router.post("/mailboxes/refresh", response_model=MailboxRefreshResponse)
def refresh_mailboxes(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[
        RefreshMailboxesUseCase,
        Depends(get_refresh_mailboxes_use_case),
    ],
) -> MailboxRefreshResponse:
    """Catch this user's mailboxes up right now.

    Meant to be called when someone opens the application. It renews
    subscriptions on the way past, so a user simply using the product keeps
    their own mailboxes alive even if nothing scheduled is running.
    """
    summary = use_case.execute(user_id=user_id)

    return MailboxRefreshResponse(
        mailboxes=summary.mailboxes,
        fetched=summary.fetched,
        accepted=summary.accepted,
        needs_reauth=summary.needs_reauth,
    )


@router.post("/mailboxes/backfill", response_model=MailboxBackfillResponse)
def backfill_mailboxes(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[
        BackfillMailboxesUseCase,
        Depends(get_backfill_mailboxes_use_case),
    ],
) -> MailboxBackfillResponse:
    """Read this month's mail from the first of the month onward, once.

    Meant to be offered right after connecting a mailbox: someone who signs up
    on any day but the first would otherwise have a first month of history
    missing whatever arrived before they connected — the ordinary sync only
    ever reads forward. Safe to call more than once; anything already
    ingested comes back as a duplicate, never twice.
    """
    summary = use_case.execute(user_id=user_id)

    return MailboxBackfillResponse(
        mailboxes=summary.mailboxes,
        fetched=summary.fetched,
        accepted=summary.accepted,
        duplicates=summary.duplicates,
        needs_reauth=summary.needs_reauth,
        since=summary.since,
    )


@router.get("/me", response_model=CurrentUserResponse)
def me(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
) -> CurrentUserResponse:
    return CurrentUserResponse(user_id=str(user_id.value))
