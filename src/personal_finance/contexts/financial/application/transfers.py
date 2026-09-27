"""Declaring, after the fact, that a movement was a transfer — and undoing it.

The alert for paying a card at another bank names the account the money left
and the institution it went to: "Pagaste $3,625,733.00 a BANCO COMERCIAL AV
VILLAS desde tu producto *5261". It is read, correctly, as an ordinary
outgoing movement — and so the month counts a payment as spending, the card's
debt never falls, and net worth drops by money that never left. Nothing in the
alert can fix that, because nothing in it says the card is the owner's. The
owner can, and this is where they do.

Three answers, one per situation:

* **The other side is already a movement here** — both banks emailed. The two
  are paired; no balance moves, because both already did.
* **The other side is an account here that never emailed** — AV Villas sends a
  receipt nobody can read as a movement. That side is written on the account
  the owner names, moving its balance: the card's debt falls.
* **The other side is not in this app at all.** The movement is marked a
  transfer on its own, and only stops counting as spending or income.

Everything is one write (`TransferDeclarations`), and all of it can be undone:
a reclassified movement goes back to what it was, a written side is erased
with its balance. None of it touches how a movement is *recorded* — an alert
arriving again is still refused as the duplicate it is, and lands on the row
that now says "transfer".
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    BalanceReversal,
    ScheduledBillRepository,
    TransactionLedger,
    TransferDeclarations,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    TransferDeclarationError,
)
from personal_finance.contexts.financial.domain.transfers import (
    could_be_other_side,
    names_institution,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    DeclarationRefusal,
    MovementFingerprint,
    MovementId,
    TransferBasis,
    TransferRole,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import UserId


# How many movements a proposal offers as the other side. The rules already
# demand the same amount to the cent going the other way within days, so a
# long list means round amounts; the closest few are the ones worth reading.
MAX_PROPOSED_COUNTERPARTS = 5


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeclareTransferCommand:
    """A movement its owner says was a transfer, and where its other side is.

    At most one of the two: `counterpart_movement_id` pairs it with a movement
    already here, `counterpart_account_id` writes the other side on that
    account. Neither means the other side is outside this app.
    """

    user_id: UserId
    transaction_id: str
    counterpart_account_id: AccountId | None = None
    counterpart_movement_id: str | None = None

    def __post_init__(self) -> None:
        if (
            self.counterpart_account_id is not None
            and self.counterpart_movement_id is not None
        ):
            raise ValueError(
                "The other side is an existing movement or an account, not both",
            )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransferOptions:
    """What a screen can offer for one movement, before anything is written.

    `refusal` set means nothing can be offered, and the lists are empty.
    `accounts` is every account that could take the other side, the ones the
    movement's counterparty names first — `suggested` holds their ids.
    """

    movement: Transaction
    role: TransferRole
    refusal: DeclarationRefusal | None
    accounts: Sequence[Account]
    suggested: frozenset[AccountId]
    counterparts: Sequence[Transaction]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransferDeclared:
    """The rows that now say "transfer", and the balance that moved.

    `movements` starts with the movement the owner declared. `accounts` is
    empty unless a side was written, which is the only case a balance moves.
    """

    movements: Sequence[Transaction]
    accounts: Sequence[Account]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransferUndone:
    """What undoing put back, and what it took away.

    `restored` is spending or income again. `erased` names the written side
    when there was one — it no longer exists — and `accounts` the balance it
    gave back.
    """

    restored: Sequence[Transaction]
    erased: Sequence[str]
    accounts: Sequence[Account]


class DeclareTransferUseCase:
    """Offering, declaring and undoing a transfer somebody's alert could not name."""

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
        declarations: TransferDeclarations,
        bills: ScheduledBillRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._ledger = ledger
        self._declarations = declarations
        self._bills = bills
        self._events = event_publisher

    def options(self, *, user_id: UserId, transaction_id: str) -> TransferOptions:
        """What could be the other side of this movement.

        Reads every movement the owner has, which is the price of finding the
        other side without an index by amount: it runs when somebody opens
        one movement, never on a list.
        """
        movement = self._find(user_id, transaction_id)
        role = TransferRole.of(movement.direction)
        linked = self._linked_to_bills(user_id)
        refusal = _refusal(movement, linked)

        if refusal is not None:
            return TransferOptions(
                movement=movement,
                role=role,
                refusal=refusal,
                accounts=(),
                suggested=frozenset(),
                counterparts=(),
            )

        eligible = [
            account
            for account in self._accounts.list_by_user(user_id)
            if not account.is_closed
            and account.currency is movement.amount.currency
            and account.id != movement.account_id
        ]
        suggested = frozenset(
            account.id
            for account in eligible
            if names_institution(movement.counterparty, account.bank)
        )
        counterparts = sorted(
            (
                candidate
                for candidate in self._ledger.list_all(user_id)
                if candidate.id.value not in linked
                and could_be_other_side(movement, candidate)
            ),
            key=lambda candidate: abs(
                candidate.occurred_at.as_epoch_seconds()
                - movement.occurred_at.as_epoch_seconds(),
            ),
        )

        return TransferOptions(
            movement=movement,
            role=role,
            refusal=None,
            accounts=sorted(
                eligible,
                key=lambda account: (account.id not in suggested, account.name),
            ),
            suggested=suggested,
            counterparts=counterparts[:MAX_PROPOSED_COUNTERPARTS],
        )

    def declare(self, command: DeclareTransferCommand) -> TransferDeclared:
        movement = self._find(command.user_id, command.transaction_id)

        if movement.transfer is not None:
            # Pressed twice, or two tabs: the declaration asked for is already
            # the one stored, and saying so beats a refusal to interpret.
            if self._already_declared(movement, command):
                return TransferDeclared(movements=[movement], accounts=[])

            raise TransferDeclarationError(DeclarationRefusal.ALREADY_TRANSFER.reason)

        linked = self._linked_to_bills(command.user_id)

        if (refusal := _refusal(movement, linked)) is not None:
            raise TransferDeclarationError(refusal.reason)

        if command.counterpart_movement_id is not None:
            return self._pair(movement, command.counterpart_movement_id, linked)

        if command.counterpart_account_id is not None:
            return self._write_counterpart(
                movement,
                command.counterpart_account_id,
                command,
            )

        movement.declare_transfer()
        self._store(command, reclassified=[movement])

        return TransferDeclared(movements=[movement], accounts=[])

    def undo(self, *, user_id: UserId, transaction_id: str) -> TransferUndone:
        """Take a declared transfer back, from whichever side it is asked.

        Refused on a transfer the bank stated or its owner entered as one:
        there is no earlier version of that movement to go back to, and
        erasing it is what `DELETE` is for.
        """
        movement = self._find(user_id, transaction_id)
        leg = movement.transfer

        if leg is None:
            raise TransferDeclarationError("This movement is not a transfer")

        if not leg.basis.is_declared:
            raise TransferDeclarationError(
                "This transfer was not declared after the fact, so there is "
                "nothing to put back; delete it instead",
            )

        sides = [movement]

        if leg.counterpart_id is not None:
            other = self._ledger.find(
                user_id=user_id,
                transaction_id=leg.counterpart_id.value,
            )

            # Gone is a pair already broken some other way; putting back what
            # is left is the repair, not a second thing to refuse.
            if (
                other is not None
                and other.transfer is not None
                and other.transfer.transfer_id == leg.transfer_id
            ):
                sides.append(other)

        restored = [
            side
            for side in sides
            if side.transfer is not None
            and side.transfer.basis is TransferBasis.RECLASSIFIED
        ]
        written = next(
            (
                side
                for side in sides
                if side.transfer is not None
                and side.transfer.basis is TransferBasis.COUNTERPART
            ),
            None,
        )

        for side in restored:
            side.undo_declaration()

        account, reversal = self._unwind(written)

        if not self._declarations.undeclare(
            restored=restored,
            transfer_id=leg.transfer_id,
            erased=written,
            reversal=reversal,
        ):
            raise TransferDeclarationError(
                "This transfer changed while it was being undone; reload it",
            )

        for side in restored:
            self._events.publish(side.pull_events())

        if written is not None:
            written.erase()
            self._events.publish(written.pull_events())

        if account is not None:
            self._events.publish(account.pull_events())

        return TransferUndone(
            restored=restored,
            erased=[] if written is None else [written.id.value],
            accounts=[] if account is None else [account],
        )

    def _pair(
        self,
        movement: Transaction,
        counterpart_id: str,
        linked: frozenset[str],
    ) -> TransferDeclared:
        other = self._find(movement.user_id, counterpart_id)

        if (refusal := _refusal(other, linked)) is not None:
            raise TransferDeclarationError(refusal.reason)

        movement.pair_with(other)
        self._store_pair(movement, other)

        return TransferDeclared(movements=[movement, other], accounts=[])

    def _write_counterpart(
        self,
        movement: Transaction,
        account_id: AccountId,
        command: DeclareTransferCommand,
    ) -> TransferDeclared:
        account = self._accounts.find(user_id=movement.user_id, account_id=account_id)

        if account is None:
            raise AccountNotFoundError(f"No account {account_id.value} for this user")

        written = movement.declare_counterpart(
            account_id=account.id,
            counterparty=self._name_of_side(movement),
            bank=account.bank or "",
        )
        before = account.balance.signed_amount
        # Refuses a closed account and a currency it does not hold, before
        # anything is stored; the declaration above lives only in memory.
        account.apply(written.as_movement())

        stored = self._declarations.declare(
            reclassified=[movement],
            written=written,
            balance_delta=account.balance.signed_amount - before,
        )

        if not stored:
            self._drop(movement, written, account)

            return self._settled(command)

        self._events.publish(movement.pull_events())
        self._events.publish(written.pull_events())
        self._events.publish(account.pull_events())

        return TransferDeclared(movements=[movement, written], accounts=[account])

    def _store(
        self,
        command: DeclareTransferCommand,
        *,
        reclassified: Sequence[Transaction],
    ) -> None:
        if not self._declarations.declare(
            reclassified=reclassified,
            written=None,
            balance_delta=None,
        ):
            for movement in reclassified:
                movement.pull_events()

            current = self._find(command.user_id, command.transaction_id)

            if not self._already_declared(current, command):
                raise TransferDeclarationError(
                    DeclarationRefusal.ALREADY_TRANSFER.reason,
                )

            return

        for movement in reclassified:
            self._events.publish(movement.pull_events())

    def _store_pair(self, movement: Transaction, other: Transaction) -> None:
        self._store(
            DeclareTransferCommand(
                user_id=movement.user_id,
                transaction_id=movement.id.value,
                counterpart_movement_id=other.id.value,
            ),
            reclassified=[movement, other],
        )

    def _settled(self, command: DeclareTransferCommand) -> TransferDeclared:
        """What a declaration that lost its race finds stored instead.

        Success when the winner declared exactly this — the same press twice.
        The side written for a movement has one possible identity, so two
        screens naming two different accounts race for one row, and the one
        that lost is told so rather than shown the other's choice as its own.
        """
        current = self._find(command.user_id, command.transaction_id)
        leg = current.transfer

        if (
            leg is None
            or leg.counterpart_id is None
            or not self._already_declared(current, command)
        ):
            raise TransferDeclarationError(DeclarationRefusal.ALREADY_TRANSFER.reason)

        other = self._ledger.find(
            user_id=command.user_id,
            transaction_id=leg.counterpart_id.value,
        )

        return TransferDeclared(
            movements=[current] if other is None else [current, other],
            accounts=[],
        )

    def _already_declared(
        self,
        movement: Transaction,
        command: DeclareTransferCommand,
    ) -> bool:
        """Whether `movement` already is the transfer `command` asks for."""
        leg = movement.transfer

        if leg is None or leg.basis is not TransferBasis.RECLASSIFIED:
            return False

        if command.counterpart_movement_id is not None:
            return (
                leg.counterpart_id is not None
                and leg.counterpart_id.value == command.counterpart_movement_id
            )

        if command.counterpart_account_id is not None:
            written_id = _written_id(movement)

            if leg.counterpart_id != written_id:
                return False

            written = self._ledger.find(
                user_id=movement.user_id,
                transaction_id=written_id.value,
            )

            return (
                written is not None
                and written.account_id == command.counterpart_account_id
            )

        return leg.counterpart_id is None

    def _name_of_side(self, movement: Transaction) -> str:
        """How the written side names this one: its account, as its owner calls it.

        A movement on no account is one waiting for its account to be
        declared, and the bank that emailed it is the only name it has yet.
        """
        if movement.account_id is not None:
            account = self._accounts.find(
                user_id=movement.user_id,
                account_id=movement.account_id,
            )

            if account is not None:
                return account.name

        return movement.bank.title() or movement.counterparty

    def _unwind(
        self,
        written: Transaction | None,
    ) -> tuple[Account | None, BalanceReversal | None]:
        """Take the written side back off its balance, in memory."""
        if written is None or written.account_id is None:
            return None, None

        account = self._accounts.find(
            user_id=written.user_id,
            account_id=written.account_id,
        )

        if account is None:
            # Gone since: the row still leaves, with no balance to correct.
            return None, None

        before = account.balance.signed_amount
        account.reverse(written.as_movement())

        return account, BalanceReversal(
            account_id=account.id,
            delta=account.balance.signed_amount - before,
            movements=1,
        )

    def _linked_to_bills(self, user_id: UserId) -> frozenset[str]:
        """Every movement a bill counts as one of its charges.

        None on this branch: linking a movement to a bill's charge arrives
        with E2·C, and until then no bill can count one. The port stays so
        that merge only has to fill this in.
        """
        del user_id

        return frozenset()

    def _find(self, user_id: UserId, transaction_id: str) -> Transaction:
        movement = self._ledger.find(user_id=user_id, transaction_id=transaction_id)

        if movement is None:
            raise TransactionNotFoundError(
                f"No movement {transaction_id} for this user"
            )

        return movement

    @staticmethod
    def _drop(*aggregates: Transaction | Account) -> None:
        """Discard events for writes that did not happen."""
        for aggregate in aggregates:
            aggregate.pull_events()


def _refusal(
    movement: Transaction,
    linked: frozenset[str],
) -> DeclarationRefusal | None:
    """Why `movement` cannot be declared, asking the bills as well."""
    refusal = movement.declaration_refusal

    if refusal is not None:
        return refusal

    if movement.id.value in linked:
        # A bill is paid by spending. Calling its charge a transfer would take
        # it out of the month while the bill still reads as paid by it.
        return DeclarationRefusal.LINKED_TO_BILL

    return None


def _written_id(movement: Transaction) -> MovementId:
    """The only id the side written for `movement` can ever have."""
    return MovementId.from_fingerprint(
        MovementFingerprint.from_declared_counterpart(
            user_id=movement.user_id,
            movement_id=movement.id,
        ),
    )
