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
