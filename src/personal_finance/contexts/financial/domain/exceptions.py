class FinancialDomainError(Exception):
    """Base exception for financial domain errors."""


class AccountClosedError(FinancialDomainError):
    """Raised when a closed account is asked to move money.

    A closed account keeps its history but stops taking new movements: a late
    alert for it must surface as unassigned, not silently reopen it.
    """


class CurrencyMismatchError(FinancialDomainError):
    """Raised when an amount in one currency meets a balance in another.

    Never converted here: an exchange rate is a fact about a moment in time,
    and guessing one would corrupt the balance quietly.
    """
