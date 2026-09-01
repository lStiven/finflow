from __future__ import annotations

import dataclasses
import uuid

from personal_finance.contexts.identity.application.commands import (
    LoginCommand,
    RegisterUserCommand,
    UpdateProfileCommand,
)
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
    EmailVerificationRepository,
    InboxRegistrar,
    PasswordHasher,
    SecretHasher,
    TokenIssuer,
    UserRepository,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
    EmailNotVerifiedError,
    InvalidAccessTokenError,
    InvalidCredentialsError,
    UserNotFoundError,
)
from personal_finance.contexts.identity.domain.policies import (
    validate_password_strength,
)
from personal_finance.contexts.identity.domain.value_objects import Email, PersonName
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UserProfile:
    """What an account looks like to its own owner. No password material of
    any kind, hashed or otherwise.
    """

    user_id: UserId
    email: Email
    name: PersonName | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserResult:
    user_id: UserId
    email: Email
    name: PersonName | None
    access_token: AccessToken


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LoginResult:
    user_id: UserId
    access_token: AccessToken


class RegisterUserUseCase:
    """Creates an account, assigns its forwarding address, and logs the new
    user in.

    Registration and login are kept as one round trip on purpose: the whole
    point of this context is to get a usable account — and the address to
    start forwarding bank email to — fast enough to act on immediately.

    What has to happen *before* this is the address proving it can be read.
    The ticket that proves it is spent here, first and atomically, and that
    ordering is what keeps the duplicate-email answer from being an oracle:
    learning that an address is taken now costs reading that address.
    """

    def __init__(
        self,
        *,
        repository: UserRepository,
        hasher: PasswordHasher,
        token_issuer: TokenIssuer,
        inbox_registrar: InboxRegistrar,
        event_publisher: EventPublisher,
        verifications: EmailVerificationRepository,
        ticket_hasher: SecretHasher,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._token_issuer = token_issuer
        self._inbox_registrar = inbox_registrar
        self._event_publisher = event_publisher
        self._verifications = verifications
        self._ticket_hasher = ticket_hasher

    def execute(self, command: RegisterUserCommand) -> RegisterUserResult:
        validate_password_strength(command.password)

        email = Email(command.email)

        if not self._verifications.consume_ticket(
            email=email,
            ticket_hash=self._ticket_hasher.hash(command.verification_token),
            now=PosixTime.now(),
        ):
            raise EmailNotVerifiedError(
                "This address has not been verified, or the verification "
                "expired. Ask for a new code.",
            )

        user = User.register(
            email=email,
            password_hash=self._hasher.hash(command.password),
            registered_at=PosixTime.now(),
            name=PersonName(command.name) if command.name is not None else None,
        )
        stored = self._repository.add_if_new(user)

        if stored is not None:
            user.pull_events()  # Discard: this attempt never actually happened.
            raise EmailAlreadyRegisteredError(
                f"{command.email} is already registered",
            )

        self._event_publisher.publish(user.pull_events())

        # Unconditional: every account gets its forwarding address the moment
        # it exists, whether or not the caller named any senders yet.
        self._inbox_registrar.register(user_id=user.id, inbox=command.inbox)

        return RegisterUserResult(
            user_id=user.id,
            email=user.email,
            name=user.name,
            access_token=self._token_issuer.issue(_as_authenticated(user)),
        )


class LoginUseCase:
    """Verifies a password and issues an access token for it.

    Never reveals which half of the credentials was wrong: an unknown email
    still runs a full hash comparison against a decoy, so a failed login for
    an existing account and one for a nonexistent account take the same time
    and return the exact same error.
    """

    def __init__(
        self,
        *,
        repository: UserRepository,
        hasher: PasswordHasher,
        token_issuer: TokenIssuer,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._token_issuer = token_issuer
        self._decoy_hash = hasher.hash(uuid.uuid4().hex)

    def execute(self, command: LoginCommand) -> LoginResult:
        user = self._repository.find_by_email(Email(command.email))
        hashed = user.password_hash if user is not None else self._decoy_hash
        password_matches = self._hasher.verify(command.password, hashed)

        if user is None or not password_matches:
            raise InvalidCredentialsError("Invalid email or password")

        return LoginResult(
            user_id=user.id,
            access_token=self._token_issuer.issue(_as_authenticated(user)),
        )


class GetProfileUseCase:
    """Reads back the caller's own account.

    Answers from storage rather than from the access token: the token's own
    claims are a snapshot of the moment it was issued, so after a rename only
    a stored record still tells the truth.
    """

    def __init__(self, *, repository: UserRepository) -> None:
        self._repository = repository

    def execute(self, *, caller: AuthenticatedUser) -> UserProfile:
        user = _load_own_account(self._repository, caller)

        return UserProfile(user_id=user.id, email=user.email, name=user.name)


class UpdateProfileUseCase:
    """Changes what the caller is called, and nothing else.

    Only the name is editable: the email is the account's identity and the
    key its record is stored under, so moving it is a migration rather than
    an edit and is deliberately not offered here.
    """

    def __init__(self, *, repository: UserRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        caller: AuthenticatedUser,
        command: UpdateProfileCommand,
    ) -> UserProfile:
        user = _load_own_account(self._repository, caller)
        user.rename(PersonName(command.name))

        if not self._repository.rename(user):
            # Lost a race with the account being removed between the read and
            # the write; the conditional write is what makes that detectable.
            raise UserNotFoundError("No account found for this token")

        return UserProfile(user_id=user.id, email=user.email, name=user.name)


class AuthenticateUseCase:
    """Turns a bearer token into who is calling, or refuses it.

    A signature check alone is not enough, which is the whole reason this is a
    use case and not a call to `TokenIssuer.verify`. A JWT is valid until it
    expires no matter what happens to the account behind it, so a token stolen
    before a password reset would keep working through the reset that was
    supposed to stop it — for as long as the token lives, a day by default.

    So the token's `credential_version` is compared against the account's, and
    the account is read to get it. That is one strongly-consistent read per
    authenticated request. At this deployment's volume it is far below the
    cost of any of the endpoints behind it, and it buys two things worth more:
    a password change ends other sessions immediately, and a token for an
    account that no longer exists stops authenticating.
    """

    def __init__(
        self,
        *,
        token_issuer: TokenIssuer,
        repository: UserRepository,
    ) -> None:
        self._token_issuer = token_issuer
        self._repository = repository

    def execute(self, token: str) -> AuthenticatedUser:
        claims = self._token_issuer.verify(token)
        user = self._repository.find_by_email(claims.email)

        if (
            user is None
            or user.id != claims.user_id
            or user.credential_version != claims.credential_version
        ):
            # One answer for all of them: the account is gone, the address was
            # reassigned, or the password moved on. The caller is simply not
            # authenticated, and which it was is not their business.
            raise InvalidAccessTokenError("Invalid or expired access token")

        # From storage rather than from the claims: a renamed account should
        # not carry its old name around until the token expires.
        return _as_authenticated(user)


def _as_authenticated(user: User) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        name=user.name,
        credential_version=user.credential_version,
    )


def _load_own_account(
    repository: UserRepository,
    caller: AuthenticatedUser,
) -> User:
    """Fetch the account a verified token names, refusing anything else.

    The token carries both the id and the email, and the record is stored
    under the email — so the id is checked against what came back. They can
    only disagree if the address was reassigned to another account after the
    token was issued, and that token must not reach the new one.
    """
    user = repository.find_by_email(caller.email)

    if user is None or user.id != caller.user_id:
        raise UserNotFoundError("No account found for this token")

    return user
