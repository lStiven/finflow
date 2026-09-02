"""Writing both sides of a card payment, against the same fakes as the
single-movement path.

The cases that matter are the partial ones: only one of the two accounts
declared, neither declared, and a redelivery landing on a pair where one side
was already written. Every one of them has to leave the ledger able to reach
the right answer, because a transfer with one side booked is the state that
quietly corrupts a balance.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from personal_finance.contexts.financial.application.commands import (
    OpenAccountCommand,
    RecordTransferCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    Outcome,
    RecordTransferUseCase,
)
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
PAID_AT = PosixTime.from_datetime(datetime(2026, 5, 21, 21, 30, tzinfo=UTC))
PAID = Money(amount=Decimal("3540258"), currency=Currency.COP)


# The same in-memory doubles the single-movement tests use, kept local rather
# than shared: a fake that grows to serve two suites stops being a stand-in
# for one repository and starts being a second implementation to reason about.
class FakeAccounts:
    def __init__(self) -> None:
        self.by_id: dict[str, Account] = {}
        self.by_fingerprint: dict[tuple[str, str], Account] = {}
        # Set to have `add` lose, the way a concurrent worker makes it lose.
        self.loser: Account | None = None
        self.swallow_the_winner = False

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(str(account_id.value))

        return account if account and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        return self.by_fingerprint.get((str(user_id.value), fingerprint.value))

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self._store(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        if self.loser is not None:
            winner = self.loser
            self.loser = None

            if not self.swallow_the_winner:
                self._store(winner)

            return False

        self._store(account)

        return True

    def _store(self, account: Account) -> None:
        self.by_id[str(account.id.value)] = account

        for fingerprint in account.fingerprints:
            self.by_fingerprint[(str(account.user_id.value), fingerprint.value)] = (
                account
            )


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
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id
            and movement.account_id is None
            and movement.account_fingerprint == fingerprint
        ]

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
        return [
            movement
            for movement, _ in self.rows.values()
            if movement.user_id == user_id and movement.account_id is None
        ]


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


def _command(**overrides: object) -> RecordTransferCommand:
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "amount": PAID,
        "occurred_at": PAID_AT,
        "source_instrument_kind": InstrumentKind.ACCOUNT.value,
        "source_last_four": "5261",
        "destination_instrument_kind": InstrumentKind.CREDIT_CARD.value,
        "destination_last_four": "7653",
    }
    parts.update(overrides)

    return RecordTransferCommand(**parts)  # type: ignore[arg-type]


def _use_case(
    accounts: FakeAccounts,
    ledger: FakeLedger,
    publisher: RecordingPublisher,
) -> RecordTransferUseCase:
    return RecordTransferUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=publisher,
    )


def _declare_savings(accounts: FakeAccounts, holds: str = "5000000") -> Account:
    account = Account.open(
        user_id=USER,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PAID_AT,
        bank="bancolombia",
        instrument_kind=InstrumentKind.ACCOUNT,
        last_four="5261",
        opening_balance=Money(amount=Decimal(holds), currency=Currency.COP),
    )
    account.pull_events()
    accounts.add(account)

    return account


def _declare_card(accounts: FakeAccounts, owes: str = "3540258") -> Account:
    account = Account.open(
        user_id=USER,
        name="Tarjeta",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PAID_AT,
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
        opening_balance=Money(amount=Decimal(owes), currency=Currency.COP),
    )
    account.pull_events()
    accounts.add(account)

    return account


# ------------------------------------------------------- both sides declared


def test_a_card_payment_lands_on_both_accounts() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    savings = _declare_savings(accounts)
    card = _declare_card(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.source.outcome is Outcome.APPLIED
    assert result.destination.outcome is Outcome.APPLIED
    assert savings.balance.signed_amount == Decimal("1459742")
    assert card.balance.signed_amount == Decimal("0")


def test_a_card_payment_writes_two_rows_and_no_more() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare_savings(accounts)
    _declare_card(accounts)

    _use_case(accounts, ledger, publisher).execute(_command())

    assert len(ledger.rows) == 2


def test_the_two_rows_face_opposite_ways_and_share_a_transfer() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare_savings(accounts)
    _declare_card(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())

    source = result.source.transaction
    destination = result.destination.transaction
    assert source.direction is MovementDirection.OUTGOING
    assert destination.direction is MovementDirection.INCOMING
    assert source.transfer is not None
    assert destination.transfer is not None
    assert source.transfer.transfer_id == destination.transfer.transfer_id


def test_a_card_payment_leaves_net_worth_untouched() -> None:
    """Assets fall by exactly what liabilities fall by. Anything else here
    means one of the two sides went the wrong way."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    savings = _declare_savings(accounts)
    card = _declare_card(accounts)
    before = savings.balance.signed_amount - card.balance.signed_amount

    _use_case(accounts, ledger, publisher).execute(_command())

    assert savings.balance.signed_amount - card.balance.signed_amount == before


# ------------------------------------------------------- one side declared


def test_only_the_card_declared_still_clears_its_debt() -> None:
    """The ordinary state for somebody who declared their card and not the
    account it is paid from. The other side waits, exactly like any alert for
    an account nobody has declared."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    card = _declare_card(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.destination.outcome is Outcome.APPLIED
    assert result.source.outcome is Outcome.UNASSIGNED
    assert card.balance.signed_amount == Decimal("0")
    assert len(ledger.rows) == 2


def test_only_the_account_declared_still_records_the_money_leaving() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    savings = _declare_savings(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.source.outcome is Outcome.APPLIED
    assert result.destination.outcome is Outcome.UNASSIGNED
    assert savings.balance.signed_amount == Decimal("1459742")


def test_neither_side_declared_loses_nothing() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.source.outcome is Outcome.UNASSIGNED
    assert result.destination.outcome is Outcome.UNASSIGNED
    assert len(ledger.rows) == 2


def test_declaring_the_missing_account_afterwards_adopts_its_side() -> None:
    """Adoption is the existing retroactive path, and a transfer leg goes
    through it unchanged: nothing special had to be taught to `ManageAccounts`."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _use_case(accounts, ledger, publisher).execute(_command())

    card = ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=publisher,
    ).open(
        OpenAccountCommand(
            user_id=USER,
            name="Tarjeta",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            opening_balance=Money(amount=Decimal("3540258"), currency=Currency.COP),
            bank="bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four="7653",
        ),
    )

    assert card.movements_applied == 1
    assert card.balance.signed_amount == Decimal("0")


# ------------------------------------------------------- delivered twice


def test_the_same_payment_delivered_twice_is_written_once() -> None:
    """Two rows, two balance changes, however many times the message arrives.

    Asserted on what was *stored* rather than on the live aggregates: a
    refused write leaves the in-memory account one movement ahead, which is
    documented on `RecordMovementResult` and is exactly why it withholds the
    account on a duplicate.
    """
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare_savings(accounts)
    _declare_card(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    use_case.execute(_command())
    published_after_first = len(publisher.published)
    again = use_case.execute(_command())

    assert again.source.outcome is Outcome.DUPLICATE
    assert again.destination.outcome is Outcome.DUPLICATE
    assert again.outcome is Outcome.DUPLICATE
    assert len(ledger.rows) == 2
    assert [delta for _, delta in ledger.rows.values()] == [
        Decimal("-3540258"),
        Decimal("-3540258"),
    ]
    assert len(publisher.published) == published_after_first


def test_a_redelivery_completes_a_pair_whose_second_side_was_never_written() -> None:
    """What a partial failure leaves behind, and what the retry has to do with
    it: finish the pair without writing the half that already landed."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare_savings(accounts)
    card = _declare_card(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    # The first attempt writes the source and dies before the destination.
    first = use_case.execute(_command())
    destination_id = first.destination.transaction.id.value
    del ledger.rows[destination_id]
    card.balance = card.balance.plus(PAID)

    repaired = use_case.execute(_command())

    assert repaired.source.outcome is Outcome.DUPLICATE
    assert repaired.destination.outcome is Outcome.APPLIED
    assert repaired.outcome is Outcome.APPLIED
    assert len(ledger.rows) == 2
    assert ledger.rows[destination_id][1] == Decimal("-3540258")
    assert card.balance.signed_amount == Decimal("0")


def test_two_payments_of_the_same_amount_on_one_day_are_two_transfers() -> None:
    """Different minutes, so they are different movements — the same rule the
    single-movement fingerprint already uses."""
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare_savings(accounts)
    _declare_card(accounts, owes="7080516")
    use_case = _use_case(accounts, ledger, publisher)

    use_case.execute(_command())
    use_case.execute(
        _command(
            occurred_at=PosixTime.from_epoch_seconds(
                PAID_AT.as_epoch_seconds() + 3600,
            ),
        ),
    )

    assert len(ledger.rows) == 4
