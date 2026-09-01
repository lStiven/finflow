"""Short-lived proofs about an email address.

Two of them, and they exist for opposite reasons. An `EmailVerification` is
how an address proves it is real before an account is created behind it —
without it, anything that can POST can fill this deployment with accounts
nobody can reach. A `PasswordResetTicket` is how somebody who cannot log in
proves they still own the address the account was built on.

Both are credentials while they live, so neither holds the secret it is about:
the code and the token are emailed once and only their hashes are kept, the
same way a password is. And both are rate limited by a `SendWindow`, because
the endpoints behind them are unauthenticated and make this deployment send
mail to an address a stranger chose.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Self

from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    TooManyVerificationAttemptsError,
    VerificationExpiredError,
)
from personal_finance.contexts.identity.domain.value_objects import Email, SecretHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId, ValueObject


# How many times one code may be guessed before the challenge is spent. Six
# digits is a million possibilities; five attempts is what makes that number
# mean something.
MAX_CODE_ATTEMPTS = 5
# How many mails one address may cause inside a window, and how far apart.
MAX_SENDS_PER_WINDOW = 5
MIN_SECONDS_BETWEEN_SENDS = 60


def _plus_minutes(moment: PosixTime, minutes: int) -> PosixTime:
    return PosixTime.from_datetime(moment.to_datetime() + timedelta(minutes=minutes))


def _has_passed(deadline: PosixTime, *, now: PosixTime) -> bool:
    return now.to_datetime() >= deadline.to_datetime()


@dataclasses.dataclass(frozen=True, slots=True)
class SendWindow(ValueObject):
    """How much mail one address has already caused, and when.

    A fixed window rather than a sliding one: it opens on the first send and
    is not extended by later ones, so five mails buy an hour of quiet rather
    than an hour from the last attempt. Simpler to reason about, and the
    difference does not matter at this scale.
    """

    sends: int
    last_sent_at: PosixTime
    expires_at: PosixTime

    @classmethod
    def opened(cls, *, now: PosixTime, window_minutes: int) -> Self:
        return cls(
            sends=1,
            last_sent_at=now,
            expires_at=_plus_minutes(now, window_minutes),
        )

    def is_over(self, now: PosixTime) -> bool:
        return _has_passed(self.expires_at, now=now)

    def extended(self, now: PosixTime) -> Self:
        """The same window with one more send counted, or a refusal."""
        if self.sends >= MAX_SENDS_PER_WINDOW:
            raise DeliveryThrottledError(
                "Too many messages requested for this address",
                retry_after_seconds=max(
                    1,
                    self.expires_at.as_epoch_seconds() - now.as_epoch_seconds(),
                ),
            )

        waited = now.as_epoch_seconds() - self.last_sent_at.as_epoch_seconds()

        if waited < MIN_SECONDS_BETWEEN_SENDS:
            raise DeliveryThrottledError(
                "Another message was sent to this address a moment ago",
                retry_after_seconds=MIN_SECONDS_BETWEEN_SENDS - waited,
            )

        return dataclasses.replace(self, sends=self.sends + 1, last_sent_at=now)


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationState(ValueObject):
    """The two counters a stored challenge is recognised by.

    Every change to a challenge moves one of them, which is what lets a write
    be made conditional on the record not having moved since it was read —
    two people racing the same code cannot both spend an attempt.
    """

    sends: int
    attempts: int


@dataclasses.dataclass(slots=True)
class EmailVerification:
    """A pending proof that somebody reads the mail sent to `email`.

    Lives in two phases. First it holds a code, until the person who received
    it types it back; then it holds a ticket, which is the thing registration
    actually consumes. The ticket exists so that verifying an address is not
    the same as reserving it: without one, anybody who guessed that an address
    had just been verified could race the owner to register it.
    """

    email: Email
    code_hash: SecretHash
    code_expires_at: PosixTime
    window: SendWindow
    attempts: int = 0
    ticket_hash: SecretHash | None = None
    ticket_expires_at: PosixTime | None = None

    @classmethod
    def issue(
        cls,
        *,
        email: Email,
        code_hash: SecretHash,
        now: PosixTime,
        code_ttl_minutes: int,
        window_minutes: int,
    ) -> Self:
        return cls(
            email=email,
            code_hash=code_hash,
            code_expires_at=_plus_minutes(now, code_ttl_minutes),
            window=SendWindow.opened(now=now, window_minutes=window_minutes),
        )

    @property
    def state(self) -> VerificationState:
        return VerificationState(sends=self.window.sends, attempts=self.attempts)

    def reissue(
        self,
        *,
        code_hash: SecretHash,
        now: PosixTime,
        code_ttl_minutes: int,
    ) -> None:
        """Replace the code with a freshly sent one, or refuse to send it.

        The attempt count resets with the code — the cap exists to stop
        guessing at *a* code, and this is a different one — but the send
        window does not, which is what keeps "ask for a new code" from being
        an unlimited way to mail somebody.
        """
        self.window = self.window.extended(now)
        self.code_hash = code_hash
        self.code_expires_at = _plus_minutes(now, code_ttl_minutes)
        self.attempts = 0
        # A ticket already handed out stays valid: re-sending a code is not a
        # reason to strand somebody who is already past that step.

    def record_attempt(self, now: PosixTime) -> None:
        """Count one guess, refusing before it is even compared."""
        if _has_passed(self.code_expires_at, now=now):
            raise VerificationExpiredError("That code has expired")

        if self.attempts >= MAX_CODE_ATTEMPTS:
            raise TooManyVerificationAttemptsError(
                "Too many attempts for this code",
            )

        self.attempts += 1

    def accept(
        self,
        *,
        ticket_hash: SecretHash,
        now: PosixTime,
        ticket_ttl_minutes: int,
    ) -> None:
        """Retire the code and hand out the ticket registration will spend."""
        self.ticket_hash = ticket_hash
        self.ticket_expires_at = _plus_minutes(now, ticket_ttl_minutes)
        # The code is spent the moment it works, so a second use of the same
        # digits is not a second registration.
        self.code_expires_at = now

    def ticket_matches(self, *, ticket_hash: SecretHash, now: PosixTime) -> bool:
        """Whether the ticket presented is this challenge's live one.

        Checked here as well as in the conditional write that spends it: the
        stored expiry is what makes the answer honest, since a table's own
        time-to-live sweep is eventual and may be hours late.
        """
        if self.ticket_hash is None or self.ticket_expires_at is None:
            return False

        if _has_passed(self.ticket_expires_at, now=now):
            return False

        return self.ticket_hash == ticket_hash


@dataclasses.dataclass(frozen=True, slots=True)
class PasswordResetTicket:
    """What a reset link is, once the random part of it is hashed.

    Carries the account it was issued for so that spending it needs no second
    lookup by address, and so that a link cannot be made to land on an account
    other than the one that asked for it.
    """

    token_hash: SecretHash
    user_id: UserId
    email: Email
    expires_at: PosixTime

    def is_expired(self, now: PosixTime) -> bool:
        return _has_passed(self.expires_at, now=now)


@dataclasses.dataclass(slots=True)
class PasswordResetWindow:
    """How much reset mail one address has caused, and the one live link.

    Kept apart from the ticket because the two are found by different things:
    a ticket is found by the secret in the link, and this is found by the
    address that asked. Holding the current ticket's hash here is what lets a
    new request retire the previous link instead of leaving both alive.
    """

    email: Email
    window: SendWindow
    issued_token_hash: SecretHash | None = None
