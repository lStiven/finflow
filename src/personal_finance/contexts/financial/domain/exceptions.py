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


class TransactionAlreadyAssignedError(FinancialDomainError):
    """Raised when a movement already on one account is offered to another.

    Reassignment is deferred, not supported and refused: moving a movement
    means first taking its amount back off the balance that holds it, and no
    operation does that yet. Until one does, a movement auto-assigned to the
    wrong account stays there — which matters, because reading a debit card
    as a savings account is a guess the user may need to correct.
    """
