"""Proving an address, and getting back into an account through it.

Everything here is reachable without a token, which is what shapes it. Three
rules run through all of it:

* **Nothing answers "does this account exist".** Both branches of every
  endpoint send mail and return the same shape, so the only way to learn
  whether an address is registered is to read that address.
* **Every unauthenticated path that sends mail is rate limited**, because
  otherwise it is a way to aim this deployment's mailbox at somebody else and
  to burn its daily sending quota.
* **Every one-time secret is spent by a conditional write**, never by a read
  followed by a write, so a retry or a race cannot use one twice.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import quote, urlsplit

from personal_finance.contexts.identity.application.commands import (
    ChangePasswordCommand,
    ConfirmEmailVerificationCommand,
    RequestEmailVerificationCommand,
    RequestPasswordResetCommand,
    ResetPasswordCommand,
)
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
    CredentialNotifier,
    EmailVerificationRepository,
    PasswordHasher,
    PasswordResetRepository,
    SecretGenerator,
    SecretHasher,
    TokenIssuer,
    UserRepository,
)
from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    PasswordResetTicket,
    PasswordResetWindow,
    SendWindow,
)
from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    InvalidCredentialsError,
    InvalidPasswordResetTokenError,
    InvalidVerificationCodeError,
    PasswordUnchangedError,
    UserNotFoundError,
)
from personal_finance.contexts.identity.domain.policies import (
    validate_password_strength,
)
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    VerificationCode,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EmailVerificationRequested:
    """What the caller is told after a code was sent.

    `code` is filled in only where this deployment is a developer's own
    machine, so a script can finish the flow without a mailbox. It is `None`
    everywhere else, and the router decides which — see `local_echo`.
    """

    email: Email
    expires_in_minutes: int
    code: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegistrationTicket:
    """Proof that an address answered, spent once by `POST /identity/register`."""

    token: str
    expires_at: PosixTime


class RequestEmailVerificationUseCase:
    """Mails a one-time code to an address that wants an account.

    Writes the challenge before it knows whether the address is already
    registered, and sends mail either way: the send window is what caps abuse,
    and it has to apply to a known address as much as an unknown one, or the
    endpoint becomes both an oracle and a way to mail somebody at will.
    """

    def __init__(
        self,
        *,
        verifications: EmailVerificationRepository,
        users: UserRepository,
        generator: SecretGenerator,
        code_hasher: SecretHasher,
        notifier: CredentialNotifier,
        code_ttl_minutes: int,
        window_minutes: int,
        local_echo: bool = False,
    ) -> None:
        self._verifications = verifications
        self._users = users
        self._generator = generator
        self._code_hasher = code_hasher
        self._notifier = notifier
        self._code_ttl_minutes = code_ttl_minutes
        self._window_minutes = window_minutes
        self._local_echo = local_echo

    def execute(
        self,
        command: RequestEmailVerificationCommand,
    ) -> EmailVerificationRequested:
        email = Email(command.email)
        now = PosixTime.now()
        code = VerificationCode(self._generator.verification_code())
        code_hash = self._code_hasher.hash(code.value)

        existing = self._verifications.find(email)
        expected = existing.state if existing is not None else None

        if existing is None or existing.window.is_over(now):
            verification = EmailVerification.issue(
                email=email,
                code_hash=code_hash,
                now=now,
                code_ttl_minutes=self._code_ttl_minutes,
                window_minutes=self._window_minutes,
            )
        else:
            # Raises when this address has already caused enough mail.
            existing.reissue(
                code_hash=code_hash,
                now=now,
                code_ttl_minutes=self._code_ttl_minutes,
            )
            verification = existing

        if not self._verifications.save(verification, expected=expected):
            # Somebody else moved this record between the read and the write.
            # Refusing is the safe reading: the other request just sent a mail
            # to this address, which is exactly what the window caps.
            raise DeliveryThrottledError(
                "Another message was sent to this address a moment ago",
                retry_after_seconds=60,
            )

        if self._users.find_by_email(email) is not None:
            # No code for an address that already has an account — but mail
            # all the same, so the answer and the timing are the same as for
            # an address that does not.
            self._notifier.send_registration_notice_for_existing_account(email=email)
        else:
            self._notifier.send_verification_code(
                email=email,
                code=code.value,
                expires_in_minutes=self._code_ttl_minutes,
            )

        return EmailVerificationRequested(
            email=email,
            expires_in_minutes=self._code_ttl_minutes,
            code=code.value if self._local_echo else None,
        )


class ConfirmEmailVerificationUseCase:
    """Checks a code and hands back the ticket registration spends.

    One write per attempt, conditional on the counters the record was read
    with: that is what makes the attempt cap real rather than advisory, since
    two requests racing the same code cannot both be counted as one.
    """

    def __init__(
        self,
        *,
        verifications: EmailVerificationRepository,
        generator: SecretGenerator,
        code_hasher: SecretHasher,
        ticket_hasher: SecretHasher,
        ticket_ttl_minutes: int,
    ) -> None:
        self._verifications = verifications
        self._generator = generator
        self._code_hasher = code_hasher
        self._ticket_hasher = ticket_hasher
        self._ticket_ttl_minutes = ticket_ttl_minutes

    def execute(self, command: ConfirmEmailVerificationCommand) -> RegistrationTicket:
        email = Email(command.email)
        now = PosixTime.now()
        verification = self._verifications.find(email)

        if verification is None:
            # Same answer as a wrong code: "no challenge for this address"
            # would otherwise say which addresses are mid-registration.
            raise InvalidVerificationCodeError("That code is not valid")

        expected = verification.state
        # Raises on an expired code or one guessed at its limit, before the
        # digits are compared at all.
        verification.record_attempt(now)

        matches = self._code_hasher.verify(
            _normalized(command.code),
            verification.code_hash,
        )
        token = self._generator.opaque_token()

        if matches:
            verification.accept(
                ticket_hash=self._ticket_hasher.hash(token),
                now=now,
                ticket_ttl_minutes=self._ticket_ttl_minutes,
            )

        if not self._verifications.save(verification, expected=expected):
            # The attempt could not be counted, so it does not get to happen.
            raise InvalidVerificationCodeError("That code is not valid")

        if not matches:
            raise InvalidVerificationCodeError("That code is not valid")

        ticket_expires_at = verification.ticket_expires_at

        if ticket_expires_at is None:  # pragma: no cover - `accept` sets it
            raise InvalidVerificationCodeError("That code is not valid")

        return RegistrationTicket(token=token, expires_at=ticket_expires_at)


class RequestPasswordResetUseCase:
    """Mails a link that lets somebody who cannot log in set a new password.

    The link's secret is 256 random bits and is never stored: only its hash
    is, and that hash is the key the ticket is found by. A leak of the table
    therefore hands out no working links.
    """

    def __init__(
        self,
        *,
        resets: PasswordResetRepository,
        users: UserRepository,
        generator: SecretGenerator,
        token_hasher: SecretHasher,
        notifier: CredentialNotifier,
        reset_url: str,
        ttl_minutes: int,
        window_minutes: int,
    ) -> None:
        self._resets = resets
        self._users = users
        self._generator = generator
        self._token_hasher = token_hasher
        self._notifier = notifier
        self._reset_url = reset_url
        self._ttl_minutes = ttl_minutes
        self._window_minutes = window_minutes

    def execute(self, command: RequestPasswordResetCommand) -> None:
        email = Email(command.email)
        now = PosixTime.now()
        existing = self._resets.find_window(email)
        expected = existing.window.sends if existing is not None else None

        if existing is None or existing.window.is_over(now):
            window = PasswordResetWindow(
                email=email,
                window=SendWindow.opened(now=now, window_minutes=self._window_minutes),
            )
        else:
            # Raises when this address has already caused enough reset mail.
            window = PasswordResetWindow(
                email=email,
                window=existing.window.extended(now),
                issued_token_hash=existing.issued_token_hash,
            )

        superseded = existing.issued_token_hash if existing is not None else None
        user = self._users.find_by_email(email)
        token = self._generator.opaque_token()

        if user is not None:
            window.issued_token_hash = self._token_hasher.hash(token)

        if not self._resets.save_window(window, expected_sends=expected):
            raise DeliveryThrottledError(
                "Another message was sent to this address a moment ago",
                retry_after_seconds=60,
            )

        if user is None:
            # Still mail, so that an address with no account and one with an
            # account are indistinguishable from outside.
            self._notifier.send_password_reset_for_unknown_account(email=email)

            return

        token_hash = window.issued_token_hash

        if token_hash is None:  # pragma: no cover - set just above
            raise InvalidPasswordResetTokenError("Could not issue a reset link")

        self._resets.save_ticket(
            PasswordResetTicket(
                token_hash=token_hash,
                user_id=user.id,
                email=user.email,
                expires_at=PosixTime.from_epoch_seconds(
                    now.as_epoch_seconds() + self._ttl_minutes * 60,
                ),
            ),
        )

        if superseded is not None:
            # One live link per address: asking again retires the previous
            # one rather than leaving five of them working at once.
            self._resets.delete_ticket(superseded)

        self._notifier.send_password_reset(
            email=email,
            link=_reset_link(self._reset_url, token),
            expires_in_minutes=self._ttl_minutes,
        )


class ResetPasswordUseCase:
    """Spends a reset link and sets the new password behind it.

    The link is spent before anything else is attempted, and by a conditional
    delete rather than a read: a link that fails to apply is gone, which costs
    somebody one more mail and is the only ordering in which a retry cannot be
    a second use.
    """

    def __init__(
        self,
        *,
        resets: PasswordResetRepository,
        users: UserRepository,
        hasher: PasswordHasher,
        token_hasher: SecretHasher,
        event_publisher: EventPublisher,
    ) -> None:
        self._resets = resets
        self._users = users
        self._hasher = hasher
        self._token_hasher = token_hasher
        self._event_publisher = event_publisher

    def execute(self, command: ResetPasswordCommand) -> None:
        # Checked before the link is spent: a password the policy would refuse
        # is the caller's own typo, and burning their one link over it would
        # send them back to their inbox for nothing.
        validate_password_strength(command.new_password)

        now = PosixTime.now()
        ticket = self._resets.consume_ticket(
            token_hash=self._token_hasher.hash(command.token),
            now=now,
        )

        if ticket is None:
            raise InvalidPasswordResetTokenError(
                "That link is no longer valid",
            )

        user = self._users.find_by_email(ticket.email)

        if user is None or user.id != ticket.user_id:
            # The account moved or went away since the link was issued. Same
            # answer as an unknown link: nothing about it is the caller's
            # business.
            raise InvalidPasswordResetTokenError("That link is no longer valid")

        user.change_password(self._hasher.hash(command.new_password))

        if not self._users.change_password(user):
            raise InvalidPasswordResetTokenError("That link is no longer valid")

        self._event_publisher.publish(user.pull_events())


class ChangePasswordUseCase:
    """Replaces the password of somebody who can still log in.

    Returns a fresh token because it has to: the change ends every session
    opened with the old password, including the one that asked for it.
    """

    def __init__(
        self,
        *,
        users: UserRepository,
        hasher: PasswordHasher,
        token_issuer: TokenIssuer,
        event_publisher: EventPublisher,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._token_issuer = token_issuer
        self._event_publisher = event_publisher

    def execute(
        self,
        *,
        caller: AuthenticatedUser,
        command: ChangePasswordCommand,
    ) -> AccessToken:
        user = self._users.find_by_email(caller.email)

        if user is None or user.id != caller.user_id:
            raise UserNotFoundError("No account found for this token")

        if not self._hasher.verify(command.current_password, user.password_hash):
            # A token alone is not enough to take an account over: whoever is
            # asking has to still know the password they are replacing.
            raise InvalidCredentialsError("Current password is not correct")

        validate_password_strength(command.new_password)

        if self._hasher.verify(command.new_password, user.password_hash):
            raise PasswordUnchangedError(
                "The new password is the one already on this account",
            )

        user.change_password(self._hasher.hash(command.new_password))

        if not self._users.change_password(user):
            raise UserNotFoundError("No account found for this token")

        self._event_publisher.publish(user.pull_events())

        return self._token_issuer.issue(
            AuthenticatedUser(
                user_id=user.id,
                email=user.email,
                name=user.name,
                credential_version=user.credential_version,
            ),
        )


def _normalized(code: str) -> str:
    """What somebody actually typed, once the mail client's spacing is gone."""
    return code.strip().replace(" ", "").replace("-", "")


def _reset_link(base_url: str, token: str) -> str:
    # The base is configuration, not input, but it is still checked here: a
    # trailing query on it would turn the token into a second value of the
    # same name, and a bare host would send the mail somewhere unusable.
    separator = "&" if urlsplit(base_url).query else "?"

    return f"{base_url}{separator}token={quote(token, safe='')}"
