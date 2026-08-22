from __future__ import annotations

import dataclasses
import uuid

from personal_finance.contexts.identity.application.commands import (
    LoginCommand,
    RegisterUserCommand,
)
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    InboxRegistrar,
    PasswordHasher,
    TokenIssuer,
    UserRepository,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
)
from personal_finance.contexts.identity.domain.policies import (
    validate_password_strength,
)
from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserResult:
    user_id: UserId
    email: Email
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
    """

    def __init__(
        self,
        *,
        repository: UserRepository,
        hasher: PasswordHasher,
        token_issuer: TokenIssuer,
        inbox_registrar: InboxRegistrar,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._hasher = hasher
        self._token_issuer = token_issuer
        self._inbox_registrar = inbox_registrar
        self._event_publisher = event_publisher

    def execute(self, command: RegisterUserCommand) -> RegisterUserResult:
        validate_password_strength(command.password)

        user = User.register(
            email=Email(command.email),
            password_hash=self._hasher.hash(command.password),
            registered_at=PosixTime.now(),
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
            access_token=self._token_issuer.issue(user.id),
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
            access_token=self._token_issuer.issue(user.id),
        )
