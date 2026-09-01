from __future__ import annotations

import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from personal_finance.contexts.identity.application.commands import (
    ChangePasswordCommand,
    ConfirmEmailVerificationCommand,
    LoginCommand,
    RegisterUserCommand,
    RequestEmailVerificationCommand,
    RequestPasswordResetCommand,
    ResetPasswordCommand,
    UpdateApprovedSendersCommand,
    UpdateProfileCommand,
)
from personal_finance.contexts.identity.application.credential_handlers import (
    ChangePasswordUseCase,
    ConfirmEmailVerificationUseCase,
    RequestEmailVerificationUseCase,
    RequestPasswordResetUseCase,
    ResetPasswordUseCase,
)
from personal_finance.contexts.identity.application.handlers import (
    AuthenticateUseCase,
    GetProfileUseCase,
    LoginUseCase,
    RegisterUserUseCase,
    UpdateProfileUseCase,
    UserProfile,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    GetInboxUseCase,
    UpdateApprovedSendersUseCase,
)
from personal_finance.contexts.identity.application.integration_events import (
    IdentityIntegrationEventTranslator,
)
from personal_finance.contexts.identity.application.ports import (
    AuthenticatedUser,
    CredentialNotifier,
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    InvalidPasswordResetTokenError,
    InvalidVerificationCodeError,
    PasswordUnchangedError,
    TooManyVerificationAttemptsError,
    UserNotFoundError,
    VerificationExpiredError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.domain.value_objects import (
    PersonName,
    VerificationCode,
)
from personal_finance.contexts.identity.infrastructure.email.smtp import (
    LoggingCredentialNotifier,
    MailDeliveryError,
    SmtpCredentialNotifier,
)
from personal_finance.contexts.identity.infrastructure.inbox.ingestion_inbox_registrar import (  # noqa: E501
    IngestionInboxRegistrar,
)
from personal_finance.contexts.identity.infrastructure.persistence.credentials_dynamodb import (  # noqa: E501
    DynamoDBEmailVerificationRepository,
    DynamoDBPasswordResetRepository,
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
from personal_finance.contexts.identity.infrastructure.security.secret_generator import (  # noqa: E501
    SecretsSecretGenerator,
)
from personal_finance.contexts.identity.infrastructure.security.secret_hashing import (
    BcryptSecretHasher,
    Sha256SecretHasher,
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
    get_aws_settings,
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
    # What `POST /identity/verification/confirm` handed back for this same
    # address. Required: an account is never built on an address that has not
    # answered.
    verification_token: str = Field(min_length=16, max_length=256)
    # Optional: an account is identified by its email. Skipping it here leaves
    # the account nameless until `PATCH /identity/me` sets one.
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=PersonName.MAX_LENGTH,
    )


class LoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class AccessTokenResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class UpdateProfilePayload(BaseModel):
    """Only the name: the email is the account's identity, not a field."""

    name: str = Field(min_length=1, max_length=PersonName.MAX_LENGTH)


class CurrentUserResponse(BaseModel):
    user_id: str
    email: str
    name: str | None = None


class RegisteredInboxResponse(BaseModel):
    address: str
    allowed_domains: list[str]
    allowed_addresses: list[str]


class EmailPayload(BaseModel):
    """An address, and nothing else. Used by both unauthenticated flows."""

    email: str = Field(min_length=3, max_length=320)


class VerificationCodePayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    # A little longer than the code itself, so a paste with a space or a dash
    # in it is normalized rather than refused.
    code: str = Field(min_length=VerificationCode.LENGTH, max_length=16)


class VerificationRequestedResponse(BaseModel):
    """Deliberately says nothing about the address it was given.

    Same body whether the address is new, already registered, or a typo, so
    the endpoint cannot be used to ask which. `code` is filled in only on a
    developer's own machine, where no mail is sent at all.
    """

    expires_in_minutes: int
    code: str | None = None


class VerificationConfirmedResponse(BaseModel):
    verification_token: str
    expires_at: int


class ResetPasswordPayload(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ChangePasswordPayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


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
def _build_verification_repository() -> DynamoDBEmailVerificationRepository:
    return DynamoDBEmailVerificationRepository(
        client=get_dynamodb_client(),
        table_name=get_identity_settings().challenges_table,
    )


@functools.lru_cache(maxsize=1)
def _build_password_reset_repository() -> DynamoDBPasswordResetRepository:
    return DynamoDBPasswordResetRepository(
        client=get_dynamodb_client(),
        table_name=get_identity_settings().challenges_table,
    )


@functools.lru_cache(maxsize=1)
def _build_code_hasher() -> BcryptSecretHasher:
    return BcryptSecretHasher()


@functools.lru_cache(maxsize=1)
def _build_token_hasher() -> Sha256SecretHasher:
    return Sha256SecretHasher()


@functools.lru_cache(maxsize=1)
def _build_secret_generator() -> SecretsSecretGenerator:
    return SecretsSecretGenerator()


@functools.lru_cache(maxsize=1)
def _build_notifier() -> CredentialNotifier:
    """The mailbox this context sends from, or a refusal to start.

    An unconfigured mailbox is a startup failure everywhere but a developer's
    own machine, and deliberately so: the degraded alternative is a deployment
    that accepts registrations it can never complete, and the *other* degraded
    alternative — falling back to the logging notifier — would write live
    codes and reset links into CloudWatch.
    """
    settings = get_identity_settings()

    if settings.mail_configured:
        return SmtpCredentialNotifier(
            host=settings.mail_host,
            port=settings.mail_port,
            username=settings.mail_login,
            password=settings.mail_app_password.get_secret_value(),
            from_address=settings.mail_from_address,
            from_name=settings.mail_from_name,
        )

    if get_aws_settings().is_local:
        return LoggingCredentialNotifier()

    raise ValueError(
        "IDENTITY_MAIL_FROM_ADDRESS and IDENTITY_MAIL_APP_PASSWORD are not "
        "set: this deployment could not mail a verification code or a "
        "password-reset link, so nobody could register or recover an "
        "account. See docs/deploy.md.",
    )


def _local_echo() -> bool:
    """Whether the code may be returned in the response.

    Only where this deployment is a developer's own machine, and derived from
    nothing but that: a separate switch would be a switch somebody could set
    in production, which would publish every code to whoever asked for it.
    """
    return get_aws_settings().is_local


@functools.lru_cache(maxsize=1)
def _build_request_verification_use_case() -> RequestEmailVerificationUseCase:
    settings = get_identity_settings()

    return RequestEmailVerificationUseCase(
        verifications=_build_verification_repository(),
        users=_build_user_repository(),
        generator=_build_secret_generator(),
        code_hasher=_build_code_hasher(),
        notifier=_build_notifier(),
        code_ttl_minutes=settings.verification_code_ttl_minutes,
        window_minutes=settings.delivery_window_minutes,
        local_echo=_local_echo(),
    )


@functools.lru_cache(maxsize=1)
def _build_confirm_verification_use_case() -> ConfirmEmailVerificationUseCase:
    return ConfirmEmailVerificationUseCase(
        verifications=_build_verification_repository(),
        generator=_build_secret_generator(),
        code_hasher=_build_code_hasher(),
        ticket_hasher=_build_token_hasher(),
        ticket_ttl_minutes=get_identity_settings().registration_ticket_ttl_minutes,
    )


@functools.lru_cache(maxsize=1)
def _build_request_password_reset_use_case() -> RequestPasswordResetUseCase:
    settings = get_identity_settings()

    return RequestPasswordResetUseCase(
        resets=_build_password_reset_repository(),
        users=_build_user_repository(),
        generator=_build_secret_generator(),
        token_hasher=_build_token_hasher(),
        notifier=_build_notifier(),
        reset_url=settings.password_reset_url,
        ttl_minutes=settings.password_reset_ttl_minutes,
        window_minutes=settings.delivery_window_minutes,
    )


@functools.lru_cache(maxsize=1)
def _build_reset_password_use_case() -> ResetPasswordUseCase:
    return ResetPasswordUseCase(
        resets=_build_password_reset_repository(),
        users=_build_user_repository(),
        hasher=_build_hasher(),
        token_hasher=_build_token_hasher(),
        event_publisher=_build_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_change_password_use_case() -> ChangePasswordUseCase:
    return ChangePasswordUseCase(
        users=_build_user_repository(),
        hasher=_build_hasher(),
        token_issuer=_build_token_issuer(),
        event_publisher=_build_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_authenticator() -> AuthenticateUseCase:
    return AuthenticateUseCase(
        token_issuer=_build_token_issuer(),
        repository=_build_user_repository(),
    )


@functools.lru_cache(maxsize=1)
def _build_register_use_case() -> RegisterUserUseCase:
    return RegisterUserUseCase(
        repository=_build_user_repository(),
        hasher=_build_hasher(),
        token_issuer=_build_token_issuer(),
        inbox_registrar=_build_inbox_registrar(),
        event_publisher=_build_event_publisher(),
        verifications=_build_verification_repository(),
        ticket_hasher=_build_token_hasher(),
    )


@functools.lru_cache(maxsize=1)
def _build_login_use_case() -> LoginUseCase:
    return LoginUseCase(
        repository=_build_user_repository(),
        hasher=_build_hasher(),
        token_issuer=_build_token_issuer(),
    )


@functools.lru_cache(maxsize=1)
def _build_get_profile_use_case() -> GetProfileUseCase:
    return GetProfileUseCase(repository=_build_user_repository())


@functools.lru_cache(maxsize=1)
def _build_update_profile_use_case() -> UpdateProfileUseCase:
    return UpdateProfileUseCase(repository=_build_user_repository())


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


def get_profile_use_case() -> GetProfileUseCase:
    return _build_get_profile_use_case()


def get_update_profile_use_case() -> UpdateProfileUseCase:
    return _build_update_profile_use_case()


def get_update_approved_senders_use_case() -> UpdateApprovedSendersUseCase:
    return _build_update_approved_senders_use_case()


def get_inbox_use_case() -> GetInboxUseCase:
    return _build_get_inbox_use_case()


def get_token_issuer() -> JWTTokenIssuer:
    return _build_token_issuer()


def get_authenticator() -> AuthenticateUseCase:
    return _build_authenticator()


def get_request_verification_use_case() -> RequestEmailVerificationUseCase:
    return _build_request_verification_use_case()


def get_confirm_verification_use_case() -> ConfirmEmailVerificationUseCase:
    return _build_confirm_verification_use_case()


def get_request_password_reset_use_case() -> RequestPasswordResetUseCase:
    return _build_request_password_reset_use_case()


def get_reset_password_use_case() -> ResetPasswordUseCase:
    return _build_reset_password_use_case()


def get_change_password_use_case() -> ChangePasswordUseCase:
    return _build_change_password_use_case()


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_bearer_scheme),
    ],
    authenticator: Annotated[AuthenticateUseCase, Depends(get_authenticator)],
) -> AuthenticatedUser:
    """Who is calling, according to a token this deployment signed.

    More than a signature check: the account behind the token is read and its
    credential generation compared, so a password change ends the sessions
    opened before it. See `AuthenticateUseCase` for what that costs and why it
    is worth it.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid access token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        return authenticator.execute(credentials.credentials)
    except InvalidAccessTokenError as error:
        raise unauthorized from error


def get_current_user_id(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> UserId:
    """The caller's id alone — what every other context asks for, so a router
    that only scopes data by user never has to hold the rest of the claims.
    """
    return user.user_id


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

    The last step of three: `POST /identity/verification/request` mails a
    code, `POST /identity/verification/confirm` trades it for the token this
    endpoint spends. Without that token there is no account, which is what
    keeps this from being a way to fill the deployment with addresses nobody
    can reach.

    The forwarding address never appears in this response — it depends only
    on the new account's id, so `GET /identity/inbox` right after this call
    already has it.
    """
    try:
        result = use_case.execute(
            RegisterUserCommand(
                email=payload.email,
                password=payload.password,
                verification_token=payload.verification_token,
                name=payload.name,
                inbox=payload.to_registration(),
            ),
        )
    except WeakPasswordError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except EmailNotVerifiedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
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
    caller: Annotated[AuthenticatedUser, Depends(get_current_user)],
    use_case: Annotated[GetProfileUseCase, Depends(get_profile_use_case)],
) -> CurrentUserResponse:
    """The caller's own account, read from storage rather than from the token
    it arrived with: after a rename the token still carries the old name until
    the next login, and this endpoint is what the client trusts instead.
    """
    try:
        return _profile_response(use_case.execute(caller=caller))
    except UserNotFoundError as error:
        raise _account_is_gone() from error


@router.patch("/me", response_model=CurrentUserResponse)
def update_profile(
    payload: UpdateProfilePayload,
    caller: Annotated[AuthenticatedUser, Depends(get_current_user)],
    use_case: Annotated[UpdateProfileUseCase, Depends(get_update_profile_use_case)],
) -> CurrentUserResponse:
    """Change the caller's name. Whose account is edited comes from the
    verified token, never from the request, so there is no id to pass and no
    one else's account to reach.

    The access token in hand keeps its old `name` claim until it expires —
    it is a snapshot of issuing time, and this response is the current truth.
    """
    try:
        profile = use_case.execute(
            caller=caller,
            command=UpdateProfileCommand(name=payload.name),
        )
    except UserNotFoundError as error:
        raise _account_is_gone() from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    return _profile_response(profile)


def _profile_response(profile: UserProfile) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=str(profile.user_id.value),
        email=profile.email.value,
        name=profile.name.value if profile.name is not None else None,
    )


# ----------------------------------------------------------------------
# Proving an address, before an account is built on it
# ----------------------------------------------------------------------
#
# Both of these are unauthenticated, so both are written to give nothing away.
# `request` answers the same way for an address that is free, one that is
# already taken and one that does not exist, and it sends mail in every case —
# a silent branch would itself be the answer. `confirm` gives one error for a
# wrong code, an expired one and an address with no challenge at all.


@router.post(
    "/verification/request",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=VerificationRequestedResponse,
)
def request_email_verification(
    payload: EmailPayload,
    use_case: Annotated[
        RequestEmailVerificationUseCase,
        Depends(get_request_verification_use_case),
    ],
) -> VerificationRequestedResponse:
    """Mail a one-time code to an address that wants an account.

    202 rather than 200: what this promises is that a message was handed to
    the mail server, not that anybody read it.
    """
    try:
        requested = use_case.execute(
            RequestEmailVerificationCommand(email=payload.email),
        )
    except DeliveryThrottledError as error:
        raise _throttled(error) from error
    except MailDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send the verification email. Try again shortly.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    return VerificationRequestedResponse(
        expires_in_minutes=requested.expires_in_minutes,
        code=requested.code,
    )


@router.post("/verification/confirm", response_model=VerificationConfirmedResponse)
def confirm_email_verification(
    payload: VerificationCodePayload,
    use_case: Annotated[
        ConfirmEmailVerificationUseCase,
        Depends(get_confirm_verification_use_case),
    ],
) -> VerificationConfirmedResponse:
    """Trade a code for the token `POST /identity/register` spends.

    The token is what reserves the address: verifying is not registering, and
    somebody who merely knows that an address was just verified must not be
    able to race its owner to the account.
    """
    try:
        ticket = use_case.execute(
            ConfirmEmailVerificationCommand(email=payload.email, code=payload.code),
        )
    except TooManyVerificationAttemptsError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Ask for a new code.",
        ) from error
    except VerificationExpiredError as error:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="That code expired. Ask for a new one.",
        ) from error
    except (InvalidVerificationCodeError, ValueError) as error:
        # One answer for a wrong code, a malformed address and an address with
        # no challenge behind it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code is not valid",
        ) from error

    return VerificationConfirmedResponse(
        verification_token=ticket.token,
        expires_at=ticket.expires_at.as_epoch_seconds(),
    )


# ----------------------------------------------------------------------
# Passwords: forgotten, reset, changed
# ----------------------------------------------------------------------


@router.post("/password/forgot", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    payload: EmailPayload,
    use_case: Annotated[
        RequestPasswordResetUseCase,
        Depends(get_request_password_reset_use_case),
    ],
) -> Response:
    """Mail a link that lets somebody who cannot log in set a new password.

    Answers 202 whether or not the address has an account, and sends mail
    either way, so this is not a way to find out who is registered here.
    """
    try:
        use_case.execute(RequestPasswordResetCommand(email=payload.email))
    except DeliveryThrottledError as error:
        raise _throttled(error) from error
    except MailDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not send the email. Try again shortly.",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetPasswordPayload,
    use_case: Annotated[ResetPasswordUseCase, Depends(get_reset_password_use_case)],
) -> Response:
    """Spend an emailed link and set the password behind it.

    No token comes back: the account this just handed over is reached by
    logging in with the new password, which is also the proof that it worked.
    Every session opened with the old password stops working here.
    """
    try:
        use_case.execute(
            ResetPasswordCommand(
                token=payload.token,
                new_password=payload.new_password,
            ),
        )
    except WeakPasswordError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except InvalidPasswordResetTokenError as error:
        # Unknown, expired and already spent are one answer: confirming that a
        # token was ever real is already more than a stranger should learn.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That link is no longer valid. Ask for a new one.",
        ) from error

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/change", response_model=AccessTokenResponse)
def change_password(
    payload: ChangePasswordPayload,
    caller: Annotated[AuthenticatedUser, Depends(get_current_user)],
    use_case: Annotated[ChangePasswordUseCase, Depends(get_change_password_use_case)],
) -> AccessTokenResponse:
    """Change the password of somebody who can still log in.

    The current password is required even though the caller already holds a
    token: a session left open on a shared machine must not be enough to take
    an account over.

    A token comes back because the change invalidates the one that made this
    request, along with every other session. The client is expected to replace
    what it holds with this.
    """
    try:
        token = use_case.execute(
            caller=caller,
            command=ChangePasswordCommand(
                current_password=payload.current_password,
                new_password=payload.new_password,
            ),
        )
    except InvalidCredentialsError as error:
        # 403, not 401. The caller *is* authenticated — their token is fine and
        # their session is not over; they failed a second challenge for this
        # one action. Answering 401 would overload the code the client uses to
        # mean "your session died", and a wrong password typed here would log
        # the person out instead of telling them what happened.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is not correct",
        ) from error
    except (WeakPasswordError, PasswordUnchangedError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except UserNotFoundError as error:
        raise _account_is_gone() from error

    return _as_response(
        user_id=caller.user_id,
        access_token_value=token.value,
        expires_at_epoch_seconds=token.expires_at.as_epoch_seconds(),
    )


def _throttled(error: DeliveryThrottledError) -> HTTPException:
    """A refusal to send more mail to this address just yet.

    `Retry-After` is the standard way to say how long, and it is the only
    detail given: how many messages have already gone out, and to what, stays
    between the deployment and the address.
    """
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many messages requested for this address. Try again later.",
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


def _account_is_gone() -> HTTPException:
    """A token that verifies for an account that no longer exists. Not a 401:
    the credential is genuine, the account behind it is not there.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No account found for this token",
    )
