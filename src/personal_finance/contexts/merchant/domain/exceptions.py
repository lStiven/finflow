class MerchantDomainError(Exception):
    """Base exception for merchant domain errors."""


class UnknownAliasError(MerchantDomainError):
    """Raised when an operation names an alias the merchant does not hold."""


class LastAliasError(MerchantDomainError):
    """Raised when detaching an alias would leave a merchant with none.

    A merchant no alias points at is unreachable: nothing would ever resolve
    to it again, and it would sit in the user's list forever.
    """


class MerchantOwnershipError(MerchantDomainError):
    """Raised when two merchants in one operation belong to different users."""
