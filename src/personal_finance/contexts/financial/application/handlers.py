from __future__ import annotations

import dataclasses
import enum
import logging

from personal_finance.contexts.financial.application.commands import (
    CloseAccountCommand,
    EditTransactionCommand,
    EnterTransactionCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RecordMovementCommand,
    RenameAccountCommand,
    SetCreditLimitCommand,
)
from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    FinancialDomainError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


_logger = logging.getLogger(__name__)


class AccountNotFoundError(Exception):
    """Raised when a command names an account this user does not own."""


class AccountAlreadyExistsError(Exception):
    """Raised when a declared account collides with one already held.

    Two accounts answering to one card would split a real balance in two, and
    every alert would land on whichever the lookup happened to find.
    """


class TransactionNotFoundError(Exception):
    """Raised when a command names a movement this user does not own."""


class Outcome(enum.Enum):
    """What happened to one movement."""

    # On an account, and counted in its balance.
    APPLIED = "applied"
    # Recorded, but on no account. The ordinary state for somebody who has
    # declared no accounts and only wants to see what comes in and goes out.
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
    # Why an assignable movement ended up unassigned anyway, when that happened.
    reason: str | None = None


class RecordMovementUseCase:
    """Turns one parsed bank alert into a ledger row, and a balance if it has
    somewhere to land.

    **It never creates an account.** Accounts are declared by their owner, so
    a movement whose card nobody has declared is recorded unassigned and waits
    — and is adopted the moment that account is added. Somebody who only wants
    to watch what comes in and goes out never declares one, and every movement
    stays unassigned forever, which is a complete answer rather than a
    degraded one.

    The order matters and is the whole design:

    1. **Build the movement**, which derives its identity from its content.
    2. **Find the account** it names, scoped to the user —
       `AccountFingerprint` carries no user of its own, so two people at one
       bank with the same last four digits would otherwise resolve to a single
       account holding both their money.
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
        fingerprint = transaction.account_fingerprint

        if fingerprint is None:
            return self._record_unassigned(
                transaction,
                reason="the alert named no instrument this could route",
            )

        account = self._accounts.find_by_fingerprint(
            user_id=command.user_id,
            fingerprint=fingerprint,
        )

        if account is None:
            return self._record_unassigned(
                transaction,
                reason="no account has been declared for this card",
            )

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


class ManageAccountsUseCase:
    """Everything the owner of an account can do to it.

    Declaring an account is **retroactive**: the alerts that arrived under its
    card before it existed are still in the ledger, unassigned, and they
    belong to it. They are adopted and the balance is replayed from them, so
    an account added today opens with the history it already had rather than
    at zero.

    Adoption is deliberately not atomic across every row it touches. It cannot
    be — there may be hundreds — so it assigns the rows first and recomputes
    the balance from them last. A crash in between leaves rows assigned and a
    stale total, which `rebuild` repairs on the next pass: the ledger is the
    authority and the balance is derived from it, which is exactly what makes
    that recoverable rather than a lost write.
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

    def open(self, command: OpenAccountCommand) -> Account:
        account = Account.open(
            user_id=command.user_id,
            name=command.name,
            kind=command.kind,
            currency=command.currency,
            opened_at=PosixTime.now(),
            opening_balance=command.opening_balance,
            bank=command.bank,
            instrument_kind=command.instrument_kind,
            last_four=command.last_four,
            credit_limit=command.credit_limit,
        )

        if not self._accounts.add(account):
            raise AccountAlreadyExistsError(
                "An account already answers to that bank and card",
            )

        self._adopt_waiting_movements(account)
        self._events.publish(account.pull_events())

        return account

    def link_instrument(self, command: LinkInstrumentCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.link_fingerprint(
            AccountFingerprint.from_parts(
                bank=command.bank,
                instrument_kind=command.instrument_kind,
                last_four=command.last_four,
            ),
        )
        self._accounts.save(account)
        self._adopt_waiting_movements(account)
        self._events.publish(account.pull_events())

        return account

    def set_credit_limit(self, command: SetCreditLimitCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.set_credit_limit(
            None
            if command.credit_limit is None
            else Money(amount=command.credit_limit, currency=account.currency),
        )
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def rename(self, command: RenameAccountCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.rename(command.name)
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def close(self, command: CloseAccountCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.close(PosixTime.now())
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def _adopt_waiting_movements(self, account: Account) -> None:
        """Claim the movements that were waiting for this account to exist.

        A movement the account cannot take is left where it is rather than
        assigned: a card whose first alert happened to be in another currency
        would otherwise be adopted, fail the replay, and leave an account
        that can never be recomputed again — and cannot be re-declared
        either, because it already exists.
        """
        adopted = 0

        for fingerprint in sorted(account.fingerprints, key=lambda key: key.value):
            for movement in self._ledger.list_unassigned_matching(
                user_id=account.user_id,
                fingerprint=fingerprint,
            ):
                if not _can_take(account, movement):
                    _logger.info(
                        "movement left unassigned: the account cannot take it",
                        extra={
                            "movement_id": movement.id.value,
                            "account_id": str(account.id.value),
                        },
                    )

                    continue

                movement.assign_to(account.id)
                self._ledger.save(movement)
                self._events.publish(movement.pull_events())
                adopted += 1

        if adopted:
            _resettle(account, self._accounts, self._ledger)

    def _load(self, user_id: UserId, account_id: AccountId) -> Account:
        account = self._accounts.find(user_id=user_id, account_id=account_id)

        if account is None:
            raise AccountNotFoundError(f"No account {account_id.value} for this user")

        return account


class ManageTransactionsUseCase:
    """Entering money by hand, and correcting what is already recorded.

    Both change a balance, and neither goes through the ledger's atomic add.
    They replay the account's rows instead: a correction is rare, and
    recomputing from the authority is the one path that cannot drift from it.
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

    def enter(self, command: EnterTransactionCommand) -> Transaction:
        account = (
            None
            if command.account_id is None
            else self._load_account(command.user_id, command.account_id)
        )
        transaction = Transaction.enter_manually(
            user_id=command.user_id,
            direction=command.direction,
            amount=command.amount,
            occurred_at=command.occurred_at,
            counterparty=command.counterparty,
            account_id=None if account is None else account.id,
            bank=command.bank,
            note=command.note,
        )

        if account is None:
            # Written unconditionally: a manual entry's identity is random, so
            # there is nothing it could collide with.
            self._ledger.record(transaction=transaction, balance_delta=None)
            self._events.publish(transaction.pull_events())

            return transaction

        balance_before = account.balance
        # Raises on a closed account or a currency it does not hold. Refused
        # rather than filed unassigned, unlike an alert: this movement is a
        # request somebody just made, and telling them it was rejected is more
        # use than silently putting it somewhere else.
        account.apply(transaction.as_movement())

        # The real delta, in the ledger's own atomic write: adding zero and
        # then replaying the whole account would widen the window in which a
        # concurrent alert can be lost.
        self._ledger.record(
            transaction=transaction,
            balance_delta=account.balance.signed_amount - balance_before.signed_amount,
        )
        self._events.publish(transaction.pull_events())
        self._events.publish(account.pull_events())

        return transaction

    def edit(self, command: EditTransactionCommand) -> Transaction:
        transaction = self._ledger.find(
            user_id=command.user_id,
            transaction_id=command.transaction_id,
        )

        if transaction is None:
            raise TransactionNotFoundError(
                f"No movement {command.transaction_id} for this user",
            )

        # Every account whose total this touches: the one it leaves and the
        # one it joins can be two different accounts.
        touched = {transaction.account_id}
        target = self._resolve_target(command, transaction)

        # Checked before anything is changed. Mutating first and refusing
        # afterwards would leave a movement its account can never take, and
        # every later replay of that account would fail on it.
        if target is not None:
            _refuse_unless_it_can_take(
                target,
                currency=(
                    transaction.amount.currency
                    if command.amount is None
                    else command.amount.currency
                ),
            )

        transaction.edit(
            amount=command.amount,
            occurred_at=command.occurred_at,
            counterparty=command.counterparty,
            note=command.note,
        )

        if command.detach:
            transaction.unassign()
        elif target is not None and transaction.account_id != target.id:
            # Already there is a no-op: unassigning and reassigning would
            # publish a movement leaving and rejoining an account it never
            # left, and replay the ledger for nothing.
            transaction.unassign()
            transaction.assign_to(target.id)

        touched.add(transaction.account_id)
        self._ledger.save(transaction)
        self._events.publish(transaction.pull_events())

        for account_id in touched:
            if account_id is None:
                continue

            account = self._accounts.find(
                user_id=command.user_id,
                account_id=account_id,
            )

            if account is not None:
                _resettle(account, self._accounts, self._ledger)

        return transaction

    def _resolve_target(
        self,
        command: EditTransactionCommand,
        transaction: Transaction,
    ) -> Account | None:
        """The account this movement will sit on once the edit is applied.

        None when it is being detached, or when nothing holds it and nothing
        is claiming it — an unassigned movement can be corrected freely,
        because no balance depends on it.
        """
        if command.detach:
            return None

        account_id = command.account_id or transaction.account_id

        if account_id is None:
            return None

        return self._load_account(command.user_id, account_id)

    def _load_account(self, user_id: UserId, account_id: AccountId) -> Account:
        account = self._accounts.find(user_id=user_id, account_id=account_id)

        if account is None:
            raise AccountNotFoundError(f"No account {account_id.value} for this user")

        return account


def _resettle(
    account: Account,
    accounts: AccountRepository,
    ledger: TransactionLedger,
) -> None:
    """Recompute a balance from the rows behind it, and store it.

    The repair path, used on purpose wherever a movement is adopted, corrected
    or moved. Nudging the total by a delta would work too, but replaying is
    the only version that cannot end up disagreeing with the ledger.
    """
    account.rebuild(
        movement.as_movement()
        for movement in ledger.list_movements(
            user_id=account.user_id,
            account_id=account.id,
        )
    )
    accounts.overwrite_balance(account)


def _can_take(account: Account, transaction: Transaction) -> bool:
    """Whether this account could hold this movement at all."""
    return not account.is_closed and account.currency is transaction.amount.currency


def _refuse_unless_it_can_take(account: Account, *, currency: Currency) -> None:
    """The same question, as the refusal a caller asked for.

    A closed account keeps its history and stops taking movements, and a
    currency it does not hold is never converted — an exchange rate is a fact
    about a moment nobody recorded here.
    """
    if account.is_closed:
        raise AccountClosedError(
            f"Account {account.id.value} is closed and takes no movements",
        )

    if account.currency is not currency:
        raise CurrencyMismatchError(
            f"A {currency.value} movement cannot go on a "
            f"{account.currency.value} account",
        )
