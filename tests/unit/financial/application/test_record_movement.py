"""The use case, against fakes that can be made to lose races and fail."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from personal_finance.contexts.financial.application.commands import (
    RecordMovementCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    Outcome,
    RecordMovementUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    TransactionAssigned,
    TransactionRecorded,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    BalanceSign,
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
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))


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


def _command(**overrides: object) -> RecordMovementCommand:
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "direction": MovementDirection.OUTGOING,
        "amount": Money(amount=Decimal("50000"), currency=Currency.COP),
        "occurred_at": PURCHASE_TIME,
        "counterparty": "TIENDAS ARA 123",
        "instrument_kind": "credit_card",
        "last_four": "7653",
    }
    parts.update(overrides)

    return RecordMovementCommand(**parts)  # type: ignore[arg-type]


def _use_case(
    accounts: FakeAccounts,
    ledger: FakeLedger,
    publisher: RecordingPublisher,
) -> RecordMovementUseCase:
    return RecordMovementUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=publisher,
    )


def _declare(accounts: FakeAccounts, **overrides: object) -> Account:
    parts: dict[str, object] = {
        "user_id": USER,
        "name": "Tarjeta de crédito",
        "kind": AccountKind.CREDIT_CARD,
        "currency": Currency.COP,
        "opened_at": PURCHASE_TIME,
        "bank": "bancolombia",
        "instrument_kind": InstrumentKind.CREDIT_CARD,
        "last_four": "7653",
    }
    parts.update(overrides)
    account = Account.open(**parts)  # type: ignore[arg-type]
    account.pull_events()
    accounts.add(account)

    return account


def test_an_alert_lands_on_the_account_its_owner_declared() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    declared = _declare(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.outcome is Outcome.APPLIED
    assert result.account is not None
    assert result.account.id == declared.id
    assert result.account.balance.amount.amount == Decimal("50000")


def test_no_account_declared_means_the_movement_waits_rather_than_creating_one() -> (
    None
):
    """The core of the model. Finflow works with no accounts at all: every
    movement is recorded, nothing is assigned, and what came in and went out
    is still complete.
    """
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()

    result = _use_case(accounts, ledger, publisher).execute(_command())

    assert result.outcome is Outcome.UNASSIGNED
    assert accounts.list_by_user(USER) == []
    assert len(ledger.list_unassigned(USER)) == 1


def test_a_second_movement_lands_on_the_same_declared_account() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    first = use_case.execute(_command())
    second = use_case.execute(_command(counterparty="EXITO EXPRESS"))

    assert first.account is not None
    assert second.account is not None
    assert first.account.id == second.account.id
    assert len(accounts.list_by_user(USER)) == 1


def test_the_ledger_refusing_a_row_applies_nothing_and_publishes_nothing() -> None:
    """A redelivery must not move a balance, and must not look like new work.

    `event_id` is fresh on every attempt, so republishing the events of a
    refused write would read as a second movement to any subscriber deduping
    on it.
    """
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    use_case.execute(_command())
    published_after_first = len(publisher.published)
    result = use_case.execute(_command())

    assert result.outcome is Outcome.DUPLICATE
    assert len(ledger.rows) == 1
    assert len(publisher.published) == published_after_first


def test_an_alert_naming_no_instrument_is_recorded_unassigned() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()

    result = _use_case(accounts, ledger, publisher).execute(
        _command(instrument_kind=None, last_four=None),
    )

    assert result.outcome is Outcome.UNASSIGNED
    assert result.account is None
    assert len(ledger.list_unassigned(USER)) == 1
    assert accounts.list_by_user(USER) == []


def test_a_movement_in_a_currency_the_account_does_not_hold_stays_unassigned() -> None:
    """Never converted: an exchange rate is a fact about a moment nobody
    recorded, and guessing one would corrupt the balance quietly.
    """
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    use_case.execute(_command())
    result = use_case.execute(
        _command(
            counterparty="AMAZON",
            amount=Money(amount=Decimal("12"), currency=Currency.USD),
        ),
    )

    assert result.outcome is Outcome.UNASSIGNED
    assert result.reason is not None
    assert len(ledger.list_unassigned(USER)) == 1

    account = accounts.list_by_user(USER)[0]
    assert account.balance.amount.amount == Decimal("50000")


def test_a_late_alert_for_a_closed_account_does_not_reopen_it() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)
    use_case = _use_case(accounts, ledger, publisher)

    use_case.execute(_command())
    account = accounts.list_by_user(USER)[0]
    account.close(PURCHASE_TIME)
    account.pull_events()

    result = use_case.execute(_command(counterparty="EXITO EXPRESS"))

    assert result.outcome is Outcome.UNASSIGNED
    assert account.is_closed
    assert account.movements_applied == 1


def test_spending_lowers_an_asset_and_raises_a_liability() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)
    _declare(
        accounts,
        name="Cuenta de ahorros",
        kind=AccountKind.SAVINGS,
        instrument_kind=InstrumentKind.DEBIT_CARD,
    )
    use_case = _use_case(accounts, ledger, publisher)

    on_credit = use_case.execute(_command())
    on_debit = use_case.execute(_command(instrument_kind="debit_card"))

    assert on_credit.account is not None
    assert on_debit.account is not None
    assert on_credit.account.balance.sign is BalanceSign.POSITIVE
    assert on_debit.account.balance.sign is BalanceSign.NEGATIVE


def test_a_recorded_movement_announces_what_happened_to_it() -> None:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)

    _use_case(accounts, ledger, publisher).execute(_command())

    kinds = {type(event) for event in publisher.published}

    assert TransactionRecorded in kinds
    assert TransactionAssigned in kinds
    assert AccountBalanceChanged in kinds


def test_the_balance_delta_written_is_the_one_the_account_computed() -> None:
    # What the ledger adds to the stored total has to be exactly the move the
    # domain made, or the running total drifts from the rows behind it.
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    _declare(accounts)

    result = _use_case(accounts, ledger, publisher).execute(_command())
    _, delta = next(iter(ledger.rows.values()))

    assert result.account is not None
    assert delta == result.account.balance.signed_amount
    assert delta == Decimal("50000")
