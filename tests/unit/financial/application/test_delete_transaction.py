"""Erasing a movement, and giving the balance back what it took.

The other half of correcting a mistake. `edit` fixes a movement that happened;
this is for one that did not — a purchase that was reversed, a duplicate
entered twice, a row somebody created while trying things out. Detaching it is
not the same answer: detached, the movement still exists and still counts in
what came in and what went out.

Two things every test here is really about. **The balance goes back to what it
would have been**, recomputed from the rows that are left rather than nudged by
the amount, so it cannot end up disagreeing with them. And **a transfer goes as
a pair**: two rows stating one movement of money cannot be half-erased, or the
survivor claims a payment to a movement that is no longer there while one of
the two balances still carries its side of it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.commands import (
    CloseAccountCommand,
    DeleteTransactionCommand,
    EnterTransferLegCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RecordMovementCommand,
    RecordTransferCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
    RecordMovementUseCase,
    RecordTransferUseCase,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.events import (
    AccountBalanceReversed,
    TransactionErased,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    BalanceSign,
    InstrumentKind,
    MovementDirection,
    TransferRole,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
OTHER_USER = UserId.from_string("11111111-2222-3333-4444-555555555555")
WHEN = PosixTime.from_datetime(datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
LATER = PosixTime.from_datetime(datetime(2026, 5, 22, 9, 15, tzinfo=UTC))


# Local doubles, like every other suite here: a fake shared between two of them
# stops standing in for one repository and becomes a second implementation to
# keep in step.
class FakeAccounts:
    def __init__(self) -> None:
        self.by_id: dict[str, Account] = {}
        self.pointers: dict[tuple[UserId, str], AccountId] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(str(account_id.value))

        return account if account and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        account_id = self.pointers.get((user_id, fingerprint.value))

        return None if account_id is None else self.by_id.get(str(account_id.value))

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self.by_id[str(account.id.value)] = account

        for print_ in account.fingerprints:
            self.pointers[(account.user_id, print_.value)] = account.id

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class FakeLedger:
    """Rows keyed the way the real table keys them, and nothing else.

    `record` refuses a key it already holds, because that conditional write is
    what makes a redelivery a no-op; `remove` drops keys unconditionally, for
    the same reason the real one does — the caller has already read the rows
    and decided they go, and keeps the reversals it was handed so a test can
    look at them.
    """

    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}
        # What the last erasure asked the balances to move by, so a test can
        # check the number the ledger was handed and not only the one the
        # aggregate ended up holding.
        self.reversals: list[BalanceReversal] = []

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        del balance_delta

        if transaction.id.value in self.rows:
            return False

        self.rows[transaction.id.value] = transaction

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = transaction

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        self.reversals = list(reversals)

        for transaction in transactions:
            self.rows.pop(transaction.id.value, None)

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row if row is not None and row.user_id == user_id else None

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in self.list_unassigned(user_id)
            if row.account_fingerprint == fingerprint
        ]

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        movements = [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id == account_id
        ]
        movements.sort(key=lambda row: row.occurred_at.as_epoch_seconds())

        return movements

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id is None
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


class Workbench:
    """One user's accounts, ledger and the four use cases over them."""

    def __init__(self, ledger: FakeLedger | None = None) -> None:
        accounts = FakeAccounts()
        ledger = FakeLedger() if ledger is None else ledger
        events = RecordingPublisher()
        self.accounts = accounts
        self.ledger = ledger
        self.events = events
        self.manage_accounts = ManageAccountsUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        )
        self.transactions = ManageTransactionsUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        )
        self.record = RecordMovementUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        )
        self.record_transfer = RecordTransferUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        )

    def balance_of(self, account: Account) -> Decimal:
        stored = self.accounts.find(user_id=account.user_id, account_id=account.id)

        assert stored is not None

        return stored.balance.signed_amount

    def rebuilt_balance_of(self, account: Account) -> Decimal:
        """What replaying every remaining row says the balance should be.

        The check that matters most here: a stored balance and the rows behind
        it agreeing is the whole promise an erasure has to keep.
        """
        stored = self.accounts.find(user_id=account.user_id, account_id=account.id)

        assert stored is not None

        return stored.balance_after(
            movement.as_movement()
            for movement in self.ledger.list_movements(
                user_id=account.user_id,
                account_id=account.id,
            )
        ).signed_amount


@pytest.fixture
def bench() -> Workbench:
    return Workbench()


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _savings(
    bench: Workbench,
    *,
    holds: str = "1000000",
    owner: UserId = USER,
    last_four: str = "7111",
) -> Account:
    return bench.manage_accounts.open(
        OpenAccountCommand(
            user_id=owner,
            name="Ahorros",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.ACCOUNT,
            last_four=last_four,
            opening_balance=_cop(holds),
        ),
    )


def _card(bench: Workbench, *, owes: str = "800000") -> Account:
    return bench.manage_accounts.open(
        OpenAccountCommand(
            user_id=USER,
            name="Tarjeta",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="7653",
            opening_balance=_cop(owes),
        ),
    )


def _spend(
    bench: Workbench,
    *,
    amount: str,
    counterparty: str = "RESTAURANTE EL LAGO",
    last_four: str = "7111",
    instrument_kind: str = "account",
    occurred_at: PosixTime = WHEN,
    direction: MovementDirection = MovementDirection.OUTGOING,
    owner: UserId = USER,
) -> Transaction:
    """One bank alert, through the path a real one takes."""
    return bench.record.execute(
        RecordMovementCommand(
            user_id=owner,
            bank="Bancolombia",
            direction=direction,
            amount=_cop(amount),
            occurred_at=occurred_at,
            counterparty=counterparty,
            instrument_kind=instrument_kind,
            last_four=last_four,
        ),
    ).transaction


def _pay_the_card_from_the_same_bank(
    bench: Workbench,
    *,
    amount: str = "500000",
) -> tuple[Transaction, Transaction]:
    """The alert that names both instruments, so both rows exist here."""
    result = bench.record_transfer.execute(
        RecordTransferCommand(
            user_id=USER,
            bank="Bancolombia",
            amount=_cop(amount),
            occurred_at=WHEN,
            source_instrument_kind="account",
            source_last_four="7111",
            destination_instrument_kind="credit_card",
            destination_last_four="7653",
        ),
    )

    return result.source.transaction, result.destination.transaction


def _delete(bench: Workbench, transaction_id: str, *, owner: UserId = USER) -> object:
    return bench.transactions.delete(
        DeleteTransactionCommand(user_id=owner, transaction_id=transaction_id),
    )


# ------------------------------------------------- the money comes back


def test_deleting_a_purchase_gives_the_account_its_money_back(
    bench: Workbench,
) -> None:
    """The case the whole feature exists for: two thousand spent at a
    restaurant, erased, is two thousand the account holds again.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")

    assert bench.balance_of(account) == Decimal("998000")

    bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=purchase.id.value),
    )

    assert bench.balance_of(account) == Decimal("1000000")


def test_the_row_is_gone_and_not_merely_detached(bench: Workbench) -> None:
    """Detaching leaves a movement that still counts in what went out. This
    one has to leave nothing behind.
    """
    _savings(bench)
    purchase = _spend(bench, amount="2000")

    _delete(bench, purchase.id.value)

    assert bench.ledger.find(user_id=USER, transaction_id=purchase.id.value) is None
    assert bench.ledger.list_all(USER) == []


def test_deleting_an_income_takes_it_back_off_the_balance(bench: Workbench) -> None:
    """Money coming in is restored in the other direction — a salary recorded
    twice has to be removable without the account keeping half of it.
    """
    account = _savings(bench, holds="1000000")
    salary = _spend(
        bench,
        amount="4500000",
        counterparty="NOMINA",
        direction=MovementDirection.INCOMING,
    )

    assert bench.balance_of(account) == Decimal("5500000")

    _delete(bench, salary.id.value)

    assert bench.balance_of(account) == Decimal("1000000")


def test_deleting_a_card_purchase_lowers_what_the_card_owes(
    bench: Workbench,
) -> None:
    """Direction alone never says which way a balance goes. Spending raises a
    card's debt, so erasing that spending has to lower it.
    """
    card = _card(bench, owes="800000")
    purchase = _spend(
        bench,
        amount="150000",
        last_four="7653",
        instrument_kind="credit_card",
    )

    assert bench.balance_of(card) == Decimal("950000")

    _delete(bench, purchase.id.value)

    assert bench.balance_of(card) == Decimal("800000")


def test_the_other_movements_on_the_account_are_untouched(bench: Workbench) -> None:
    """An erasure removes one row, not the account's history."""
    account = _savings(bench, holds="1000000")
    kept = _spend(bench, amount="30000", counterparty="ARA")
    doomed = _spend(bench, amount="2000", occurred_at=LATER)

    _delete(bench, doomed.id.value)

    assert bench.balance_of(account) == Decimal("970000")
    assert [row.id for row in bench.ledger.list_all(USER)] == [kept.id]


def test_the_stored_balance_matches_a_rebuild_of_what_is_left(
    bench: Workbench,
) -> None:
    """The promise an erasure has to keep, and the reason the balance is
    recomputed rather than nudged by the amount: the number on the account and
    the rows behind it must be the same number.
    """
    account = _savings(bench, holds="1000000")
    _spend(bench, amount="30000", counterparty="ARA")
    doomed = _spend(bench, amount="2000", occurred_at=LATER)
    _spend(
        bench,
        amount="120000",
        counterparty="NOMINA",
        direction=MovementDirection.INCOMING,
    )

    _delete(bench, doomed.id.value)

    assert bench.balance_of(account) == bench.rebuilt_balance_of(account)
    assert bench.balance_of(account) == Decimal("1090000")


def test_the_use_case_hands_back_the_account_as_it_now_stands(
    bench: Workbench,
) -> None:
    """So a screen showing a balance does not need a second call to stop
    showing money that no longer moved.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=purchase.id.value),
    )

    assert [movement.id for movement in result.erased] == [purchase.id]
    assert [restored.id for restored in result.restored] == [account.id]
    assert result.restored[0].balance.signed_amount == Decimal("1000000")


# ------------------------------------------- movements on no account at all


def test_an_unassigned_movement_is_erased_and_no_balance_is_touched(
    bench: Workbench,
) -> None:
    """The ordinary state for somebody who declared no accounts: the movement
    is real and worth erasing, and there is no balance for it to give back to.
    """
    purchase = _spend(bench, amount="2000")

    assert purchase.account_id is None

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=purchase.id.value),
    )

    assert bench.ledger.list_all(USER) == []
    assert list(result.restored) == []


def test_erasing_an_unassigned_movement_leaves_declared_balances_alone(
    bench: Workbench,
) -> None:
    """An account that never held the movement must not be replayed into a
    different number because something unrelated was erased.
    """
    account = _savings(bench, holds="1000000")
    orphan = _spend(bench, amount="2000", last_four="9999")

    assert orphan.account_id is None

    _delete(bench, orphan.id.value)

    assert bench.balance_of(account) == Decimal("1000000")


# ----------------------------------------------------- what is refused


def test_a_movement_that_is_not_there_is_reported_as_missing(
    bench: Workbench,
) -> None:
    with pytest.raises(TransactionNotFoundError):
        _delete(bench, "0198f4e4-0000-7000-8000-000000000000")


def test_erasing_the_same_movement_twice_refuses_the_second_time(
    bench: Workbench,
) -> None:
    """And — the part that matters — the balance moves once, not twice: the
    second attempt never reaches a replay at all.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")

    _delete(bench, purchase.id.value)

    with pytest.raises(TransactionNotFoundError):
        _delete(bench, purchase.id.value)

    assert bench.balance_of(account) == Decimal("1000000")


def test_one_person_cannot_erase_another_persons_movement(
    bench: Workbench,
) -> None:
    """Reported as missing rather than forbidden, like every other read here:
    whether somebody else has a movement is not something this tells a
    stranger. And the owner's balance must not move.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")

    with pytest.raises(TransactionNotFoundError):
        _delete(bench, purchase.id.value, owner=OTHER_USER)

    assert bench.ledger.find(user_id=USER, transaction_id=purchase.id.value)
    assert bench.balance_of(account) == Decimal("998000")


def test_a_movement_on_a_closed_account_can_still_be_erased(
    bench: Workbench,
) -> None:
    """A closed account stops taking new movements; correcting what is already
    on it is not new money moving, which is why `rebuild` allows a closed
    account too. Refusing here would leave a wrong row on a closed account
    with nothing that could ever remove it.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")
    bench.manage_accounts.close(
        CloseAccountCommand(user_id=USER, account_id=account.id),
    )

    _delete(bench, purchase.id.value)

    assert bench.balance_of(account) == Decimal("1000000")
    assert bench.balance_of(account) == bench.rebuilt_balance_of(account)


# ------------------------------------------------- transfers go as a pair


def test_erasing_one_side_of_a_transfer_erases_the_other(
    bench: Workbench,
) -> None:
    """Two rows state one movement of money. Half-erased, the survivor claims
    a payment to a movement that is no longer there.
    """
    _savings(bench, holds="1000000")
    _card(bench, owes="800000")
    source, destination = _pay_the_card_from_the_same_bank(bench, amount="500000")

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=source.id.value),
    )

    assert {movement.id for movement in result.erased} == {source.id, destination.id}
    assert bench.ledger.list_all(USER) == []


def test_erasing_a_transfer_restores_both_balances(bench: Workbench) -> None:
    """The account gets its money back and the card's debt goes back up. Only
    one of the two restored would be worse than not erasing at all.
    """
    account = _savings(bench, holds="1000000")
    card = _card(bench, owes="800000")
    _, destination = _pay_the_card_from_the_same_bank(bench, amount="500000")

    assert bench.balance_of(account) == Decimal("500000")
    assert bench.balance_of(card) == Decimal("300000")

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=destination.id.value),
    )

    assert bench.balance_of(account) == Decimal("1000000")
    assert bench.balance_of(card) == Decimal("800000")
    assert {restored.id for restored in result.restored} == {account.id, card.id}


def test_a_transfer_is_erased_from_either_side(bench: Workbench) -> None:
    """Whichever row the owner is looking at is the one they will press
    delete on, and both have to mean the same thing.
    """
    account = _savings(bench, holds="1000000")
    card = _card(bench, owes="800000")
    source, _ = _pay_the_card_from_the_same_bank(bench, amount="500000")

    _delete(bench, source.id.value)

    assert bench.balance_of(account) == Decimal("1000000")
    assert bench.balance_of(card) == Decimal("800000")


def test_a_transfer_whose_other_side_is_already_gone_erases_what_is_left(
    bench: Workbench,
) -> None:
    """A pair broken by something else is repaired by erasing the survivor,
    not refused: the row would otherwise be unremovable forever.
    """
    account = _savings(bench, holds="1000000")
    source, destination = _pay_the_card_from_the_same_bank(bench, amount="500000")
    bench.ledger.remove([destination], reversals=[])

    _delete(bench, source.id.value)

    assert bench.ledger.list_all(USER) == []
    assert bench.balance_of(account) == Decimal("1000000")


def test_a_leg_paid_from_outside_this_app_is_erased_alone(
    bench: Workbench,
) -> None:
    """A card paid from another bank has one knowable side and no second row
    to take with it. Its debt goes back up by what the payment took off.
    """
    card = _card(bench, owes="800000")
    leg = bench.transactions.enter_transfer_leg(
        EnterTransferLegCommand(
            user_id=USER,
            role=TransferRole.DESTINATION,
            amount=_cop("500000"),
            occurred_at=WHEN,
            counterparty="Nequi",
            account_id=card.id,
            bank="Bancolombia",
        ),
    )

    assert bench.balance_of(card) == Decimal("300000")

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=leg.id.value),
    )

    assert [movement.id for movement in result.erased] == [leg.id]
    assert bench.balance_of(card) == Decimal("800000")
    assert bench.accounts.find(user_id=USER, account_id=card.id) is not None


def test_a_transfer_whose_two_sides_share_an_account_unwinds_once(
    bench: Workbench,
) -> None:
    """One account answers to several instruments — a checking account emails
    as a card for purchases and as an account number for transfers — so both
    sides of a transfer can land on it. Two reversals for one account would
    unwind half of it twice, and report the same account twice to a caller
    redrawing a balance from the answer.
    """
    account = _savings(bench, holds="1000000")
    bench.manage_accounts.link_instrument(
        LinkInstrumentCommand(
            user_id=USER,
            account_id=account.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="7653",
        ),
    )
    source, destination = _pay_the_card_from_the_same_bank(bench, amount="500000")

    assert source.account_id == account.id
    assert destination.account_id == account.id

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=source.id.value),
    )

    assert [restored.id for restored in result.restored] == [account.id]
    # One entry carrying both sides, not two half-reversals of the same total.
    assert [reversal.movements for reversal in bench.ledger.reversals] == [2]
    assert bench.balance_of(account) == Decimal("1000000")
    assert bench.balance_of(account) == bench.rebuilt_balance_of(account)


def test_the_undeclared_side_of_a_transfer_goes_too(bench: Workbench) -> None:
    """The card was never declared, so its side sits unassigned waiting to be
    adopted. Erasing the transfer must not leave that half behind, waiting to
    land on an account the owner declares next week.
    """
    _savings(bench, holds="1000000")
    source, destination = _pay_the_card_from_the_same_bank(bench, amount="500000")

    assert destination.account_id is None

    _delete(bench, source.id.value)

    assert bench.ledger.list_unassigned(USER) == []


# ------------------------------------------------------- what is announced


def test_the_erasure_is_announced_with_what_the_movement_was_worth(
    bench: Workbench,
) -> None:
    """After the row is gone nothing else can explain the balance moving, so
    the event has to carry the amount and the account itself.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")
    bench.events.published.clear()

    _delete(bench, purchase.id.value)

    erased = [
        event
        for event in bench.events.published
        if isinstance(event, TransactionErased)
    ]

    assert len(erased) == 1
    assert erased[0].movement_id == purchase.id
    assert erased[0].amount == _cop("2000")
    assert erased[0].direction is MovementDirection.OUTGOING
    assert erased[0].account_id == account.id


def test_the_balance_it_landed_on_is_announced_beside_it(
    bench: Workbench,
) -> None:
    """The pair of facts a reader needs: the row that left, and the number the
    balance landed on because of it.
    """
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")
    bench.events.published.clear()

    _delete(bench, purchase.id.value)

    reversed_ = [
        event
        for event in bench.events.published
        if isinstance(event, AccountBalanceReversed)
    ]

    assert len(reversed_) == 1
    assert reversed_[0].account_id == account.id
    assert reversed_[0].movement_id == purchase.id
    assert reversed_[0].amount == _cop("2000")
    assert reversed_[0].balance.signed_amount == Decimal("1000000")
    assert reversed_[0].balance.sign is BalanceSign.POSITIVE


def test_nothing_is_announced_when_the_movement_was_not_there(
    bench: Workbench,
) -> None:
    """A refusal is not a fact about somebody's money."""
    _savings(bench)
    bench.events.published.clear()

    with pytest.raises(TransactionNotFoundError):
        _delete(bench, "0198f4e4-0000-7000-8000-000000000000")

    assert bench.events.published == []


# ---------------------------------- the balance does not wait for a read


class StaleReadLedger(FakeLedger):
    """A ledger whose reads lag behind its writes, which is what DynamoDB is.

    `list_movements` keeps answering with rows `remove` already took out. A
    query is eventually consistent unless it asks not to be, so this is the
    ordinary case rather than a rare one.
    """

    def __init__(self) -> None:
        super().__init__()
        self.removed: list[Transaction] = []

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        self.removed.extend(transactions)
        super().remove(transactions, reversals=reversals)

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in [*self.rows.values(), *self.removed]
            if row.user_id == user_id and row.account_id == account_id
        ]


def test_the_money_comes_back_even_when_the_ledger_reads_stale() -> None:
    """The reason the balance unwinds inside the write instead of being
    replayed from the rows afterwards.

    Recomputing from a query that still answers with the row just deleted
    lands on the same number, stores it, and hands it back looking right —
    with the movement gone, the money not returned, and nothing left to
    trigger a repair, because a second delete finds nothing to delete.
    """
    bench = Workbench(StaleReadLedger())
    account = _savings(bench, holds="1000000")
    purchase = _spend(bench, amount="2000")

    result = bench.transactions.delete(
        DeleteTransactionCommand(user_id=USER, transaction_id=purchase.id.value),
    )

    assert bench.balance_of(account) == Decimal("1000000")
    assert result.restored[0].balance.signed_amount == Decimal("1000000")
