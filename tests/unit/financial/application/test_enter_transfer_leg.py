"""Entering by hand the side of a card payment this app can actually see.

Paying a Bancolombia card from a Bancolombia account arrives as one alert
naming both instruments, and `RecordTransferUseCase` writes the pair. Paid
from another bank, from a wallet or in cash, there is no such alert — so the
owner says it, and the danger moves. It is no longer a half-written pair; it
is a single movement that lands in a total it does not belong in, reported as
income on the card or as an expense on the account it left.

Every test here is about that one movement doing exactly two things: moving
the balance it names, and counting for nothing.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransferLegCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    BalanceSign,
    InstrumentKind,
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
PAID_AT = PosixTime.from_datetime(datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
PAID = Money(amount=Decimal("3540258"), currency=Currency.COP)


# Local doubles, like every other suite here: a fake shared between two of
# them stops standing in for one repository and becomes a second
# implementation to keep in step.
class FakeAccounts:
    def __init__(self) -> None:
        self.by_id: dict[str, Account] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(str(account_id.value))

        return account if account and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        return None

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self.by_id[str(account.id.value)] = account

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class FakeLedger:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Transaction, Decimal | None]] = {}

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        if transaction.id.value in self.rows:
            return False

        self.rows[transaction.id.value] = (transaction, balance_delta)

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = (transaction, None)

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        del reversals

        for transaction in transactions:
            self.rows.pop(transaction.id.value, None)

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        return []

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row[0] if row and row[0].user_id == user_id else None

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id and movement.account_id == account_id
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id
        ]

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return []


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _card(
    accounts: FakeAccounts,
    *,
    owes: str = "3540258",
    owner: UserId = USER,
    currency: Currency = Currency.COP,
) -> Account:
    account = Account.open(
        user_id=owner,
        name="Tarjeta",
        kind=AccountKind.CREDIT_CARD,
        currency=currency,
        opened_at=PAID_AT,
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
        opening_balance=Money(amount=Decimal(owes), currency=currency),
    )
    account.pull_events()
    accounts.add(account)

    return account


def _savings(accounts: FakeAccounts, *, holds: str = "5000000") -> Account:
    account = Account.open(
        user_id=USER,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PAID_AT,
        bank="lulo bank",
        instrument_kind=InstrumentKind.ACCOUNT,
        last_four="7111",
        opening_balance=Money(amount=Decimal(holds), currency=Currency.COP),
    )
    account.pull_events()
    accounts.add(account)

    return account


def _use_case(
    accounts: FakeAccounts,
    ledger: FakeLedger,
    publisher: RecordingPublisher,
) -> ManageTransactionsUseCase:
    return ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=publisher,
    )


def _command(account: Account, **overrides: object) -> EnterTransferLegCommand:
    parts: dict[str, object] = {
        "user_id": USER,
        "role": TransferRole.DESTINATION,
        "amount": PAID,
        "occurred_at": PAID_AT,
        "counterparty": "Nequi",
        "account_id": account.id,
        "bank": "bancolombia",
    }
    parts.update(overrides)

    return EnterTransferLegCommand(**parts)  # type: ignore[arg-type]


# --------------------------------------------------------- what it does to money


def test_paying_a_card_from_outside_clears_the_debt() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))

    assert card.balance.signed_amount == Decimal("0")
    assert card.balance.sign is BalanceSign.POSITIVE


def test_paying_a_card_elsewhere_lowers_the_account_it_left() -> None:
    """The mirror: the account is here, the card is at another bank."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    savings = _savings(accounts)

    _use_case(accounts, ledger, publisher).enter_transfer_leg(
        _command(savings, role=TransferRole.SOURCE, amount=PAID),
    )

    assert savings.balance.signed_amount == Decimal("1459742")


def test_the_row_carries_the_balance_it_actually_moved() -> None:
    """Written as a real delta in the ledger's own atomic add, like every
    other movement: a zero here would leave the stored balance behind."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    movement = _use_case(accounts, ledger, publisher).enter_transfer_leg(
        _command(card),
    )

    _, delta = ledger.rows[movement.id.value]
    assert delta == Decimal("-3540258")


def test_the_movement_is_a_transfer_so_no_total_counts_it() -> None:
    """The whole point of the endpoint. Without this the card payment reads
    as the month's largest income."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    movement = _use_case(accounts, ledger, publisher).enter_transfer_leg(
        _command(card),
    )

    assert movement.is_transfer is True
    assert movement.transfer is not None
    assert movement.transfer.counterpart_is_external is True


def test_exactly_one_row_is_written() -> None:
    """Unlike the alert-derived path, which writes two. There is no second
    side to write, and inventing one would be a balance nobody moved."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))

    assert len(ledger.rows) == 1


def test_the_same_payment_entered_twice_is_written_once() -> None:
    """The double submit, at the layer that would have written it. Two rows
    here means the debt falls twice — the balance is then wrong and nothing
    on any screen says so."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    first = use_case.enter_transfer_leg(_command(card))
    second = use_case.enter_transfer_leg(_command(card))

    assert first.id == second.id
    assert len(ledger.rows) == 1


def test_the_second_attempt_does_not_move_the_balance_again() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    use_case.enter_transfer_leg(_command(card))
    owed = card.balance.signed_amount
    use_case.enter_transfer_leg(_command(card))

    assert card.balance.signed_amount == owed


def test_the_second_attempt_announces_nothing() -> None:
    """`event_id` is fresh on every attempt, so publishing the second one's
    events would read as new work to a subscriber deduplicating on it."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    use_case.enter_transfer_leg(_command(card))
    announced = len(publisher.published)
    use_case.enter_transfer_leg(_command(card))

    assert len(publisher.published) == announced


def test_the_second_attempt_answers_with_the_payment_already_recorded() -> None:
    """Not a refusal the caller has to interpret: the outcome it asked for
    already holds, so it gets that row."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    first = use_case.enter_transfer_leg(_command(card, note="primer intento"))
    second = use_case.enter_transfer_leg(_command(card, note="segundo intento"))

    assert second.note == "primer intento"
    assert second.id == first.id


def test_a_genuinely_different_payment_is_still_written() -> None:
    """The guard must not swallow a real second payment: another amount, or
    another minute, is another movement."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    use_case.enter_transfer_leg(_command(card))
    use_case.enter_transfer_leg(_command(card, amount=_cop("500000")))

    assert len(ledger.rows) == 2
    assert card.balance.signed_amount == Decimal("5959742")


def test_two_users_paying_identically_each_get_their_own_row() -> None:
    """The fingerprint carries the owner, so one person's double-submit guard
    can never swallow another person's payment."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    mine = _card(accounts, owes="10000000")
    theirs = _card(accounts, owner=OTHER_USER, owes="10000000")
    use_case = _use_case(accounts, ledger, publisher)

    use_case.enter_transfer_leg(_command(mine))
    use_case.enter_transfer_leg(
        _command(theirs, user_id=OTHER_USER, account_id=theirs.id),
    )

    assert len(ledger.rows) == 2


def test_it_announces_the_movement_and_the_balance() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))

    announced = {type(event).__name__ for event in publisher.published}
    assert "TransactionRecorded" in announced
    assert "AccountBalanceChanged" in announced


# ------------------------------------------------------------ what is refused


def test_an_account_that_does_not_exist_is_refused() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)

    with pytest.raises(AccountNotFoundError):
        _use_case(accounts, ledger, publisher).enter_transfer_leg(
            _command(card, account_id=AccountId.new()),
        )


def test_another_users_account_cannot_be_paid_into() -> None:
    """Isolation, asserted where it would be cheapest to lose: the account is
    looked up by id, and an id is guessable in a way a partition is not."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    theirs = _card(accounts, owner=OTHER_USER)

    with pytest.raises(AccountNotFoundError):
        _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(theirs))

    assert theirs.balance.signed_amount == Decimal("3540258")


def test_a_closed_account_is_refused() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)
    card.close(closed_at=PAID_AT)

    with pytest.raises(AccountClosedError):
        _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))


def test_a_currency_the_account_does_not_hold_is_refused() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, currency=Currency.USD)

    with pytest.raises(CurrencyMismatchError):
        _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))


def test_a_refused_entry_writes_nothing_at_all() -> None:
    """Refused after the balance was touched and before the row was stored
    would leave an account whose total no rebuild can reproduce."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts, currency=Currency.USD)

    with pytest.raises(CurrencyMismatchError):
        _use_case(accounts, ledger, publisher).enter_transfer_leg(_command(card))

    assert ledger.rows == {}
    assert publisher.published == []
    assert card.balance.signed_amount == Decimal("3540258")


# ----------------------------------------------------------- against a rebuild


def test_the_balance_survives_a_rebuild_from_the_ledger() -> None:
    """The strongest integrity check there is: the stored balance and the one
    derived by replaying every row must be the same number. A leg the replay
    read differently from the write would show up here and nowhere else."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _card(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    use_case.enter_transfer_leg(_command(card, amount=PAID))

    stored = card.balance.signed_amount
    replayed = card.balance_after(
        movement.as_movement() for movement in ledger.list_all(USER)
    )

    assert replayed.signed_amount == stored
