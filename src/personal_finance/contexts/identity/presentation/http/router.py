from __future__ import annotations

import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from personal_finance.contexts.identity.application.commands import (
    LoginCommand,
    RegisterUserCommand,
    UpdateApprovedSendersCommand,
)
from personal_finance.contexts.identity.application.handlers import (
    LoginUseCase,
    RegisterUserUseCase,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    GetInboxUseCase,
    UpdateApprovedSendersUseCase,
)
from personal_finance.contexts.identity.application.integration_events import (
    IdentityIntegrationEventTranslator,
)
from personal_finance.contexts.identity.application.ports import (
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.infrastructure.inbox.ingestion_inbox_registrar import (  # noqa: E501
    IngestionInboxRegistrar,
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
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    ListUserInboxesUseCase,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
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


class InboxSendersPayload(BaseModel):
    """The senders approved for the caller's one forwarding address.

    No address here — it is assigned automatically and never chosen by a
    caller. Both lists may be empty, which approves nothing: a forwarding
    address with no approved sender reads nothing, the safe default rather
    than a broken one.
    """

    allowed_domains: list[str] = Field(default_factory=lambda: list[str]())
    allowed_addresses: list[str] = Field(default_factory=lambda: list[str]())

    @field_validator("allowed_addresses")
    @classmethod
    def _validate_allowed_addresses(cls, value: list[str]) -> list[str]:
        return [_looks_like_an_email(address) for address in value]

    def to_registration(self) -> InboxRegistration:
        return InboxRegistration(
            allowed_domains=frozenset(self.allowed_domains),
            allowed_addresses=frozenset(self.allowed_addresses),
        )


class RegisterPayload(InboxSendersPayload):
    """Registration, plus the same optional sender approval `PATCH
    /identity/inbox` accepts — set here to skip a second call, or leave
    empty and approve senders later. The forwarding address itself is
    assigned regardless of whether any sender is named here.
    """

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class LoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


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


@functools.lru_cache(maxsize=1)
def _build_inbox_registrar() -> IngestionInboxRegistrar:
    ingestion_settings = get_ingestion_settings()

    if not ingestion_settings.ingest_mailbox_address:
        # Every registration assigns a forwarding address derived from this,
        # so there is no degraded mode to fall back to the way an optional
        # integration would have — without it, nobody could sign up at all.
        raise ValueError(
            "INGESTION_INGEST_MAILBOX_ADDRESS is not set: there is no "
            "address to derive a forwarding alias from, so no account could "
            "ever be registered. See docs/email-forwarding.md.",
        )

    repository = DynamoDBUserInboxRepository(
        client=get_dynamodb_client(),
        table_name=ingestion_settings.user_inboxes_table,
    )

    return IngestionInboxRegistrar(
        use_case=RegisterUserInboxUseCase(
            inbox_repository=repository,
            base_address=EmailAddress(ingestion_settings.ingest_mailbox_address),
        ),
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
def _build_update_approved_senders_use_case() -> UpdateApprovedSendersUseCase:
    return UpdateApprovedSendersUseCase(inbox_registrar=_build_inbox_registrar())


@functools.lru_cache(maxsize=1)
def _build_get_inbox_use_case() -> GetInboxUseCase:
    return GetInboxUseCase(inbox_reader=_build_inbox_registrar())


def get_register_use_case() -> RegisterUserUseCase:
    return _build_register_use_case()


def get_login_use_case() -> LoginUseCase:
    return _build_login_use_case()


def get_update_approved_senders_use_case() -> UpdateApprovedSendersUseCase:
    return _build_update_approved_senders_use_case()


def get_inbox_use_case() -> GetInboxUseCase:
    return _build_get_inbox_use_case()


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
    """Create an account and assign it a forwarding address.

    The address itself never appears in this response — it depends only on
    the new account's id, so `GET /identity/inbox` right after this call
    already has it.
    """
    try:
        result = use_case.execute(
            RegisterUserCommand(
                email=payload.email,
                password=payload.password,
                inbox=payload.to_registration(),
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


@router.get("/inbox", response_model=RegisteredInboxResponse)
def get_inbox(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[GetInboxUseCase, Depends(get_inbox_use_case)],
) -> RegisteredInboxResponse:
    """The caller's forwarding address, and who is currently approved to send
    to it. Whose inbox to read comes from the verified token, never from the
    request.
    """
    inbox = use_case.execute(user_id=user_id)

    if inbox is None:
        # Every account gets one at registration; reaching this means the
        # token verified for a user whose inbox record is somehow gone.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No inbox found for this account",
        )

    return _inbox_response(inbox)


@router.patch("/inbox", response_model=RegisteredInboxResponse)
def update_approved_senders(
    payload: InboxSendersPayload,
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    update_use_case: Annotated[
        UpdateApprovedSendersUseCase,
        Depends(get_update_approved_senders_use_case),
    ],
) -> RegisteredInboxResponse:
    """Replace who may send to the caller's forwarding address.

    A full replacement, not a merge: the lists sent here become the whole
    approved set. Leave both empty and the address is approved for nobody —
    connected in the sense of existing, but reading nothing.
    """
    inbox = update_use_case.execute(
        user_id=user_id,
        command=UpdateApprovedSendersCommand(inbox=payload.to_registration()),
    )

    return _inbox_response(inbox)


def _inbox_response(inbox: RegisteredInbox) -> RegisteredInboxResponse:
    return RegisteredInboxResponse(
        address=inbox.address,
        allowed_domains=sorted(inbox.allowed_domains),
        allowed_addresses=sorted(inbox.allowed_addresses),
    )


@router.get("/me", response_model=CurrentUserResponse)
def me(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
) -> CurrentUserResponse:
    return CurrentUserResponse(user_id=str(user_id.value))
