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

    Not a refusal to reassign — that is a real operation — but a refusal to do
    it by overwriting the link, which would leave the amount counted on a
    balance that never gave it up. `Transaction.unassign` is the step that has
    to come first, and it is what the edit path uses.
    """


class TransferLegError(FinancialDomainError):
    """Raised when one side of a transfer is edited as if it stood alone.

    The two sides of a transfer state one fact: this amount left that account
    and arrived at this one. Correcting the amount or the date of a single
    side would leave the pair describing two different movements, with two
    balances that no longer reconcile and nothing on either row to say which
    is right. What can still be corrected is where each side belongs — the
    account it sits on — and the note beside it.
    """
