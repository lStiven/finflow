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


class InvalidCategoryLabelError(MerchantDomainError):
    """Raised when a category name is empty, too long, or unkeyable."""


class DuplicateCategoryError(MerchantDomainError):
    """Raised when a user already has a category under that name."""


class UnknownCategoryError(MerchantDomainError):
    """Raised when a command files a merchant under a category nobody has.

    Refused rather than accepted: a merchant pointing at a category that does
    not exist reads as a bucket the user never made, and their spending would
    quietly land in it.
    """


class ShippedCategoryError(MerchantDomainError):
    """Raised when a rename or a removal names a category the app ships.

    Those are code, the same for everybody, and one person editing them would
    be editing what everybody sees.
    """
