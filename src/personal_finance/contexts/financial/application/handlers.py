from __future__ import annotations

import dataclasses
import enum
import logging

from personal_finance.contexts.financial.application.commands import (
    RecordMovementCommand,
)
from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    FinancialDomainError,
)
from personal_finance.shared.application.ports import EventPublisher


_logger = logging.getLogger(__name__)


class AccountVanishedError(Exception):
    """Raised when the account that just won a creation race cannot be read.

    Only reachable if something deleted it in between. Left as an error rather
    than a retry: guessing at that point would mean opening a second account
    for money that already has one.
    """


class Outcome(enum.Enum):
    """What happened to one movement."""

    # On an account, and counted in its balance. The common case.
    APPLIED = "applied"
    # Recorded, but on no account: the alert named no instrument this could
    # use, or the account it names cannot take the movement.
    UNASSIGNED = "unassigned"
    # This exact movement was already in the ledger. Nothing was applied.
    DUPLICATE = "duplicate"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordMovementResult:
    """What happened, and only what is safe to read afterwards.

    `account` is deliberately absent on `DUPLICATE`. The aggregate was applied
    in memory before the write was refused, so its balance is one movement
    ahead of what is stored, and handing it back would let a caller report a
    number that double-counts the redelivery.
    """

    outcome: Outcome
    transaction: Transaction
    account: Account | None = None
    # Why an assigned movement ended up unassigned anyway, when that happened.
    reason: str | None = None


class RecordMovementUseCase:
    """Turns one parsed bank alert into a ledger row and a balance.

    The order matters and is the whole design:

    1. **Build the movement**, which derives its identity from its content.
    2. **Find the account** it names, or open one on first sighting. The
       lookup is scoped to the user — `AccountFingerprint` carries no user of
       its own, so two people at one bank with the same last four digits would
       otherwise resolve to a single account holding both their money.
    3. **Apply it in memory** to learn how far the balance moves. `Account`
       owns that rule: an outgoing movement raises what a credit card owes and
       lowers what a savings account holds.
    4. **Write the row and the balance change together**, conditionally. The
       write is what decides the movement is new; nothing here remembers what
       it has seen, because that answer cannot survive a restart.
    5. **Publish only what was written.** A refused write means a redelivery,
       and its events are dropped rather than pulled: `event_id` is fresh on
       every attempt, so republishing them would look like new work to every
       subscriber that dedupes on it.

    A movement that cannot be applied is kept unassigned rather than dropped
    or forced: a closed account, or an alert in a currency the account does
    not hold. The money moved either way, and a record somebody can see and
    place is worth more than a clean failure.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
        event_publisher: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._ledger = ledger
        self._events = event_publisher

    def execute(self, command: RecordMovementCommand) -> RecordMovementResult:
        transaction = Transaction.from_alert(
            user_id=command.user_id,
            bank=command.bank,
            direction=command.direction,
            amount=command.amount,
            occurred_at=command.occurred_at,
            counterparty=command.counterparty,
            instrument_kind=command.instrument_kind,
            last_four=command.last_four,
        )

        if transaction.account_fingerprint is None or transaction.account_kind is None:
            return self._record_unassigned(
                transaction,
                reason="the alert named no instrument this could route",
            )

        account = self._resolve_account(command, transaction)
        balance_before = account.balance

        try:
            # Applied before assigned, so a refusal leaves the movement
            # untouched: a closed account, or a currency it does not hold.
            # Neither is worth losing the movement over, and neither may be
            # forced — an exchange rate is a fact about a moment nobody
            # recorded.
            account.apply(transaction.as_movement())
        except FinancialDomainError as error:
            return self._record_unassigned(transaction, reason=str(error))

        transaction.assign_to(account.id)

        delta = account.balance.signed_amount - balance_before.signed_amount

        if not self._ledger.record(transaction=transaction, balance_delta=delta):
            # Dropped, not pulled: `event_id` is fresh on every attempt, so
            # publishing the events of a refused write would read as new work
            # to any subscriber deduping on it.
            transaction.pull_events()
            account.pull_events()

            return RecordMovementResult(
                outcome=Outcome.DUPLICATE,
                transaction=transaction,
            )

        self._publish(transaction, account)

        return RecordMovementResult(
            outcome=Outcome.APPLIED,
            transaction=transaction,
            account=account,
        )

    def _resolve_account(
        self,
        command: RecordMovementCommand,
        transaction: Transaction,
    ) -> Account:
        fingerprint = transaction.account_fingerprint
        account_kind = transaction.account_kind

        if fingerprint is None or account_kind is None:
            raise ValueError("An unroutable movement has no account to resolve")

        existing = self._accounts.find_by_fingerprint(
            user_id=command.user_id,
            fingerprint=fingerprint,
        )

        if existing is not None:
            return existing

        # Nothing has answered to this bank and card before. Open the account
        # the sighting implies, generically named and flagged for review.
        if command.last_four is None or command.instrument_kind is None:
            raise ValueError("A routable movement always names its instrument")

        opened = Account.open_automatically(
            user_id=command.user_id,
            bank=command.bank,
            instrument_kind=command.instrument_kind,
            last_four=command.last_four,
            kind=account_kind,
            currency=command.amount.currency,
            opened_at=command.occurred_at,
        )

        if self._accounts.add(opened):
            self._publish_account(opened)

            return opened

        # Another worker opened it first, from the same redelivered alert.
        # Theirs is the account; a second one would hold half the movements.
        won = self._accounts.find_by_fingerprint(
            user_id=command.user_id,
            fingerprint=fingerprint,
        )

        if won is None:
            raise AccountVanishedError(
                f"Account for {fingerprint.value} was created and then lost",
            )

        return won

    def _record_unassigned(
        self,
        transaction: Transaction,
        *,
        reason: str,
    ) -> RecordMovementResult:
        if not self._ledger.record(transaction=transaction, balance_delta=None):
            transaction.pull_events()

            return RecordMovementResult(
                outcome=Outcome.DUPLICATE,
                transaction=transaction,
                reason=reason,
            )

        self._publish(transaction, None)
        _logger.info(
            "movement kept unassigned",
            # Never the counterparty, the amount or the card: those together
            # are a line of somebody's spending history.
            extra={"movement_id": transaction.id.value, "reason": reason},
        )

        return RecordMovementResult(
            outcome=Outcome.UNASSIGNED,
            transaction=transaction,
            reason=reason,
        )

    def _publish(self, transaction: Transaction, account: Account | None) -> None:
        events = transaction.pull_events()

        if account is not None:
            events.extend(account.pull_events())

        self._events.publish(events)

    def _publish_account(self, account: Account) -> None:
        self._events.publish(account.pull_events())
