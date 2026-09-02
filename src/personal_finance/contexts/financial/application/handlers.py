from __future__ import annotations

from collections.abc import Iterator, Sequence
import dataclasses
from decimal import Decimal
import enum
import logging

from personal_finance.contexts.financial.application.commands import (
    CloseAccountCommand,
    DeleteTransactionCommand,
    EditTransactionCommand,
    EnterTransactionCommand,
    EnterTransferLegCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RecordMovementCommand,
    RecordTransferCommand,
    RenameAccountCommand,
    RestateBalanceCommand,
    SetCreditLimitCommand,
)
from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    BalanceReversal,
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
    Balance,
    LedgerMovement,
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
        self._placer = MovementPlacer(
            accounts=accounts,
            ledger=ledger,
            event_publisher=event_publisher,
        )

    def execute(self, command: RecordMovementCommand) -> RecordMovementResult:
        return self._placer.place(
            Transaction.from_alert(
                user_id=command.user_id,
                bank=command.bank,
                direction=command.direction,
                amount=command.amount,
                occurred_at=command.occurred_at,
                counterparty=command.counterparty,
                instrument_kind=command.instrument_kind,
                last_four=command.last_four,
            ),
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordTransferResult:
    """What happened to each side of one transfer.

    Both are reported because they are genuinely independent outcomes: the
    account may be declared and the card not, and a redelivery can find one
    side already written and the other missing — which is exactly the state a
    partial failure leaves behind, and exactly what a retry has to repair.
    """

    source: RecordMovementResult
    destination: RecordMovementResult

    @property
    def outcome(self) -> Outcome:
        """One answer for a log line, taking the weaker of the two sides.

        `DUPLICATE` only when *both* sides were already there: a pair with one
        side still missing is work the retry did, not a no-op.
        """
        outcomes = {self.source.outcome, self.destination.outcome}

        if outcomes == {Outcome.DUPLICATE}:
            return Outcome.DUPLICATE

        if Outcome.APPLIED in outcomes:
            return Outcome.APPLIED

        return Outcome.UNASSIGNED


class RecordTransferUseCase:
    """Turns one alert about money moving inside somebody's own finances into
    the two ledger rows it actually is.

    A payment to your own credit card is not an expense: an account falls and
    a card's debt falls with it, and net worth does not move. Recorded as a
    single movement it is wrong whichever side it lands on — on the account
    the debt never clears, on the card an outgoing movement *raises* what is
    owed. So both sides are written, each with its own identity derived from
    its own content.

    **Each side is placed independently, and that is deliberate.** Only one of
    the two accounts may be declared; the other side is then recorded
    unassigned and adopted later, by the account that claims it, through the
    same retroactive path everything else uses. A side already in the ledger
    is refused by its own conditional write, so a redelivery after a partial
    failure completes the pair instead of doubling the half that succeeded.

    What it never does is invent the missing half. If a side cannot be
    recorded at all, the exception leaves the message on the queue: a transfer
    with one side booked is the one state that would quietly corrupt a
    balance, and a retry is what repairs it.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
        event_publisher: EventPublisher,
    ) -> None:
        self._placer = MovementPlacer(
            accounts=accounts,
            ledger=ledger,
            event_publisher=event_publisher,
        )

    def execute(self, command: RecordTransferCommand) -> RecordTransferResult:
        source, destination = Transaction.as_transfer(
            user_id=command.user_id,
            bank=command.bank,
            amount=command.amount,
            occurred_at=command.occurred_at,
            source_instrument_kind=command.source_instrument_kind,
            source_last_four=command.source_last_four,
            destination_instrument_kind=command.destination_instrument_kind,
            destination_last_four=command.destination_last_four,
        )

        return RecordTransferResult(
            source=self._placer.place(source),
            destination=self._placer.place(destination),
        )


class MovementPlacer:
    """Puts one already-built movement where it belongs, and writes it.

    Extracted from `RecordMovementUseCase` so a transfer's two sides go
    through the identical path: find the account by fingerprint, apply it in
    memory to learn how far the balance moves, then write the row and the
    balance change together, conditionally. Two copies of this would be two
    places for the rule about what a redelivery may do to a balance.
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

    def place(self, transaction: Transaction) -> RecordMovementResult:
        fingerprint = transaction.account_fingerprint

        if fingerprint is None:
            return self._record_unassigned(
                transaction,
                reason="the alert named no instrument this could route",
            )

        account = self._accounts.find_by_fingerprint(
            user_id=transaction.user_id,
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

    def restate_balance(self, command: RestateBalanceCommand) -> Account:
        """Correct what an account holds, keeping every movement it holds.

        The owner states today's figure and the opening balance is solved
        backwards from the rows already on record, which is the only half of
        the sum nobody can look up. Declaring an account without knowing what
        it held before its first alert is therefore not a dead end: open it
        at zero, then say what the bank shows.

        Both numbers go out in one write: they are two halves of the same
        sum, and an opening balance stored without the balance it explains
        would be a state no replay is scheduled to notice. A movement landing
        between the read and that write is the same race a replay already
        runs, and self-corrects the same way — the next replay reads the row
        that was missed.
        """
        account = self._load(command.user_id, command.account_id)
        account.restate_balance(
            Balance.from_signed(command.balance, account.currency),
            _replayed(account, self._ledger),
        )
        self._accounts.restate_balance(account)
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeleteTransactionResult:
    """What an erasure actually took out, and where the money went back.

    Both halves matter to a caller. `erased` is more than one row for a
    transfer, because the pair goes together, and a client that assumed one
    would leave the other on screen pointing at nothing. `restored` carries
    the accounts as they now stand, so a balance on screen does not need a
    second round trip to stop showing money that no longer moved.
    """

    erased: Sequence[Transaction]
    restored: Sequence[Account]


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

        return self._record_on(account, transaction)

    def enter_transfer_leg(self, command: EnterTransferLegCommand) -> Transaction:
        """Record the owner's side of a payment between their own balances.

        Paying a credit card from an account at the same bank arrives as one
        alert naming both instruments, and the transfer path writes the pair
        without anybody being asked. Paid from another bank, from a wallet or
        in cash, no alert can name both — so this is how somebody says that a
        movement is a payment rather than an expense, and it is entered by
        hand for the same reason any other unannounced movement is.

        The account is not optional here and the domain says why. What the
        caller is asserting is that a balance moved; refusing the entry when
        no balance can move is more use than storing a row that changes
        nothing and reads as though it did.
        """
        account = self._load_account(command.user_id, command.account_id)
        transaction = Transaction.enter_transfer_leg(
            user_id=command.user_id,
            role=command.role,
            amount=command.amount,
            occurred_at=command.occurred_at,
            counterparty=command.counterparty,
            account_id=account.id,
            bank=command.bank,
            note=command.note,
        )
        # Asked before the balance is touched, not only after the write. The
        # conditional write below is what makes this safe under two requests
        # at once; this read is what makes the ordinary case — somebody
        # pressing the button twice — answer with the payment they already
        # made instead of a refusal they would have to interpret.
        already = self._ledger.find(
            user_id=command.user_id,
            transaction_id=transaction.id.value,
        )

        if already is not None:
            transaction.pull_events()

            return already

        return self._record_on(account, transaction)

    def _record_on(self, account: Account, transaction: Transaction) -> Transaction:
        """Apply one hand-entered movement to its account and store both.

        Shared by the two entry paths because the bookkeeping is identical:
        what differs between an expense and a transfer leg is what the row
        says about itself, never what its balance does.
        """
        balance_before = account.balance
        # Raises on a closed account or a currency it does not hold. Refused
        # rather than filed unassigned, unlike an alert: this movement is a
        # request somebody just made, and telling them it was rejected is more
        # use than silently putting it somewhere else.
        account.apply(transaction.as_movement())

        # The real delta, in the ledger's own atomic write: adding zero and
        # then replaying the whole account would widen the window in which a
        # concurrent alert can be lost.
        written = self._ledger.record(
            transaction=transaction,
            balance_delta=account.balance.signed_amount - balance_before.signed_amount,
        )

        if not written:
            # A row with this id is already there, so nothing was written and
            # the stored balance never moved — only the copy in memory, which
            # is discarded with this call. Reachable from the transfer-leg
            # path, whose identity comes from its content: two requests racing
            # each other both get here and exactly one wins. Events are pulled
            # and dropped rather than published: `event_id` is fresh on every
            # attempt, so publishing them would read as new work to any
            # subscriber deduplicating on it.
            transaction.pull_events()
            account.pull_events()

            stored = self._ledger.find(
                user_id=transaction.user_id,
                transaction_id=transaction.id.value,
            )

            return transaction if stored is None else stored

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
            # Refuses a leg no account could take back. Raising here, after
            # the edit above, persists nothing: the save and the resettle are
            # both below, so the mutated aggregate is simply discarded.
            transaction.detach()
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

    def delete(self, command: DeleteTransactionCommand) -> DeleteTransactionResult:
        """Erase a movement, and give the balance back what it took.

        The other half of correcting a mistake. `edit` fixes a movement that
        happened; this is for one that did not — a purchase that was reversed,
        a duplicate somebody entered twice, a row created while trying things
        out. Detaching it is not the same answer: detached it still exists,
        still shows in what came in and what went out, and still has to be
        explained every time somebody reads the month.

        The row and the balance change leave together, in the ledger's own
        atomic write, exactly as they arrived. Deleting first and replaying
        the account's rows afterwards — the repair path a correction uses —
        would be wrong here twice over: the replay reads eventually, so it can
        still count the row just deleted and store the balance unchanged, and
        a failure between the two would leave the account carrying a movement
        that is gone with no row left for a retry to find.

        **A transfer goes as a pair.** Two rows stating one movement of money
        cannot be half-erased: the survivor would claim a transfer to a
        movement that is no longer there, one balance restored and the other
        still carrying its side of a payment that, as far as the app is now
        concerned, never happened. So erasing either side erases both, and
        both balances unwind in the same write. A leg whose counterpart is
        outside this app has nothing to take with it and goes alone.
        """
        transaction = self._ledger.find(
            user_id=command.user_id,
            transaction_id=command.transaction_id,
        )

        if transaction is None:
            raise TransactionNotFoundError(
                f"No movement {command.transaction_id} for this user",
            )

        doomed = [transaction, *self._counterpart_of(transaction)]
        restored = self._unwind(command.user_id, doomed)
        self._ledger.remove(
            doomed,
            reversals=[reversal for _, reversal in restored],
        )

        for movement in doomed:
            movement.erase()
            self._events.publish(movement.pull_events())

        for account, _ in restored:
            self._events.publish(account.pull_events())

        return DeleteTransactionResult(
            erased=doomed,
            restored=[account for account, _ in restored],
        )

    def _counterpart_of(self, transaction: Transaction) -> Sequence[Transaction]:
        """The other side of a transfer, when it is a row in this ledger.

        Empty for everything else, and also for a leg whose counterpart is
        named but no longer stored. That second case is a pair already broken
        — by a row removed some other way — and erasing what is left is the
        repair, not a second thing to refuse.
        """
        leg = transaction.transfer

        if leg is None or leg.counterpart_id is None:
            return ()

        counterpart = self._ledger.find(
            user_id=transaction.user_id,
            transaction_id=leg.counterpart_id.value,
        )

        return () if counterpart is None else (counterpart,)

    def _unwind(
        self,
        user_id: UserId,
        movements: Sequence[Transaction],
    ) -> Sequence[tuple[Account, BalanceReversal]]:
        """Take these movements back off the balances holding them, in memory.

        Nothing is stored here — this is the step that learns how far each
        balance moves, so the ledger can unwind it in the same write that
        removes the rows. `Account` owns the rule in both directions, which is
        what keeps erasing a card purchase from lowering a debt it raised.

        Accounts are collected in the order the movements name them and each
        appears once: both sides of a transfer can sit on the same account,
        and two entries for it would apply half the reversal twice. An account
        that no longer exists is skipped rather than refused — the movement is
        leaving either way, and there is no balance left to correct.
        """
        unwound: dict[AccountId, tuple[Account, Decimal, int]] = {}

        for movement in movements:
            account_id = movement.account_id

            if account_id is None:
                continue

            held = unwound.get(account_id)
            account = (
                held[0]
                if held is not None
                else self._accounts.find(user_id=user_id, account_id=account_id)
            )

            if account is None:
                continue

            before = account.balance.signed_amount
            account.reverse(movement.as_movement())
            moved = account.balance.signed_amount - before
            unwound[account_id] = (
                account,
                moved if held is None else held[1] + moved,
                1 if held is None else held[2] + 1,
            )

        return [
            (
                account,
                BalanceReversal(
                    account_id=account_id,
                    delta=delta,
                    movements=count,
                ),
            )
            for account_id, (account, delta, count) in unwound.items()
        ]

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


def _replayed(account: Account, ledger: TransactionLedger) -> Iterator[LedgerMovement]:
    """Every movement on an account, as the balance rules consume them.

    One reader for both paths that replay: a repair and a restatement must
    derive their number from the same rows, or they disagree the moment one
    of them learns to skip a row the other still counts.
    """
    return (
        movement.as_movement()
        for movement in ledger.list_movements(
            user_id=account.user_id,
            account_id=account.id,
        )
    )


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
    account.rebuild(_replayed(account, ledger))
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
