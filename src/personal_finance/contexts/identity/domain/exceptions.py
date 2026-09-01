class IdentityDomainError(Exception):
    """Base exception for identity domain errors."""


class EmailAlreadyRegisteredError(IdentityDomainError):
    """Raised when a registration targets an email that already has an
    account.
    """


class InvalidCredentialsError(IdentityDomainError):
    """Raised for a failed login. Deliberately generic: it must never reveal
    whether the email or the password was the one that did not match.
    """


class InvalidAccessTokenError(IdentityDomainError):
    """Raised for a token that is missing, malformed, expired, or signed with
    a different secret. Callers must treat every case as simply "not
    authenticated" rather than branching on which one it was.
    """


class UserNotFoundError(IdentityDomainError):
    """Raised when an operation targets an account that is not there —
    reachable in practice only for a token whose account was deleted after it
    was issued.
    """


class EmailNotVerifiedError(IdentityDomainError):
    """Raised when a registration arrives without a usable proof that the
    address it names can actually be read — no ticket, the wrong one, or one
    that already expired or was already spent.
    """


class InvalidVerificationCodeError(IdentityDomainError):
    """Raised for a code that does not match the pending challenge.

    Deliberately generic, like `InvalidCredentialsError`: "no challenge for
    this address" and "wrong digits" must be the same answer, or the endpoint
    becomes a way to ask which addresses are mid-registration.
    """


class VerificationExpiredError(IdentityDomainError):
    """Raised when the code being checked is past its short life. Separate
    from a wrong code because the remedy is different: ask for a new one.
    """


class TooManyVerificationAttemptsError(IdentityDomainError):
    """Raised when a challenge has been guessed at its limit. Six digits is a
    million possibilities, so the attempt cap — not the code — is what makes
    guessing hopeless.
    """


class DeliveryThrottledError(IdentityDomainError):
    """Raised when another mail to this address would be too soon, or one too
    many for the window.

    These endpoints are unauthenticated and make the deployment send mail, so
    without a cap they are a way to use it as a mail bomb aimed at somebody
    else and to burn its daily sending quota.
    """

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class InvalidPasswordResetTokenError(IdentityDomainError):
    """Raised for a reset token that is unknown, expired, or already spent.
    One answer for all three, so the endpoint never confirms that a token was
    real.
    """


class PasswordUnchangedError(IdentityDomainError):
    """Raised when the new password is the one already on the account.

    Not a security rule but an honest one: a password change that changes
    nothing still invalidates every session, which would look like a bug.
    """
