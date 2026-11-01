"""Entering by hand a payment between two accounts this app holds.

Someone paid their card from savings at another bank and entered it as one
leg: the savings fell and the debt did not. Both ends are accounts here, so
both sides are written, and both balances move, in one write — never one
without the other, never twice.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransferBetweenCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    EnterTransferBetweenUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    TransferLegError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
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
PAID_AT = PosixTime.from_datetime(datetime(2026, 10, 30, 14, 0, tzinfo=UTC))
PAID = Money(amount=Decimal("2000000"), currency=Currency.COP)


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

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        del fingerprint
        self.save(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class FakeLedger:
    """The ledger and the pair writer at once, as production wires them."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[Transaction, Decimal | None]] = {}
        self.pair_writes = 0

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

    def record_pair(
        self,
        *,
        source: Transaction,
        destination: Transaction,
        source_delta: Decimal,
        destination_delta: Decimal,
    ) -> bool:
        if source.id.value in self.rows or destination.id.value in self.rows:
            return False

        self.pair_writes += 1
        self.rows[source.id.value] = (source, source_delta)
        self.rows[destination.id.value] = (destination, destination_delta)

        return True

    def save(self, transaction: Transaction) -> None:
        self.rows[transaction.id.value] = (transaction, None)

    def remove(self, transactions: Sequence[Transaction], *, reversals: object) -> None:
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


def _account(
    accounts: FakeAccounts,
    *,
    kind: AccountKind,
    holds: str,
    owner: UserId = USER,
    currency: Currency = Currency.COP,
) -> Account:
    is_card = kind is AccountKind.CREDIT_CARD
    account = Account.open(
        user_id=owner,
        name="Tarjeta" if is_card else "Ahorros",
        kind=kind,
        currency=currency,
        opened_at=PAID_AT,
        bank="nu" if is_card else "lulo bank",
        instrument_kind=InstrumentKind.CREDIT_CARD
        if is_card
        else InstrumentKind.ACCOUNT,
        last_four="7653" if is_card else "7111",
        opening_balance=Money(amount=Decimal(holds), currency=currency),
    )
    account.pull_events()
    accounts.add(account)

    return account


def _fixture() -> tuple[
    FakeAccounts,
    FakeLedger,
    RecordingPublisher,
    EnterTransferBetweenUseCase,
]:
    accounts, ledger, publisher = FakeAccounts(), FakeLedger(), RecordingPublisher()
    use_case = EnterTransferBetweenUseCase(
        accounts=accounts,
        ledger=ledger,
        pairs=ledger,
        event_publisher=publisher,
    )

    return accounts, ledger, publisher, use_case


def _command(
    source: Account,
    destination: Account,
    **overrides: object,
) -> EnterTransferBetweenCommand:
    parts: dict[str, object] = {
        "user_id": USER,
        "source_account_id": source.id,
        "destination_account_id": destination.id,
        "amount": PAID,
        "occurred_at": PAID_AT,
    }
    parts.update(overrides)

    return EnterTransferBetweenCommand(**parts)  # type: ignore[arg-type]


# --------------------------------------------------------- what it does to money


def test_paying_the_card_from_savings_lowers_both() -> None:
    accounts, _, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="3540258")

    use_case.execute(_command(savings, card))

    assert savings.balance.signed_amount == Decimal("3000000")
    assert card.balance.signed_amount == Decimal("1540258")


def test_both_rows_carry_the_balance_each_actually_moved() -> None:
    """Signed by the account's own rule: money arriving on a liability is
    debt going down."""
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="3540258")

    leaving, arriving = use_case.execute(_command(savings, card))

    assert ledger.rows[leaving.id.value][1] == Decimal("-2000000")
    assert ledger.rows[arriving.id.value][1] == Decimal("-2000000")


def test_the_two_sides_are_one_write() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")

    use_case.execute(_command(savings, card))

    assert ledger.pair_writes == 1
    assert len(ledger.rows) == 2


def test_neither_side_counts_as_spending_or_income() -> None:
    accounts, _, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")

    leaving, arriving = use_case.execute(_command(savings, card))

    assert leaving.is_transfer
    assert arriving.is_transfer
    assert leaving.transfer is not None
    assert leaving.transfer.role is TransferRole.SOURCE


def test_it_announces_both_movements_and_both_balances() -> None:
    accounts, _, publisher, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")

    use_case.execute(_command(savings, card))

    names = [type(event).__name__ for event in publisher.published]
    assert names.count("TransactionRecorded") == 2
    assert names.count("AccountBalanceChanged") == 2


def test_the_balances_survive_a_rebuild_from_the_ledger() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="3540258")

    use_case.execute(_command(savings, card))

    for account in (savings, card):
        replayed = account.balance_after(
            movement.as_movement()
            for movement in ledger.list_movements(user_id=USER, account_id=account.id)
        )
        assert replayed.signed_amount == account.balance.signed_amount


# ------------------------------------------------------------- a double submit


def test_the_same_payment_entered_twice_moves_the_money_once() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="3540258")

    first = use_case.execute(_command(savings, card))
    second = use_case.execute(_command(savings, card))

    assert [side.id for side in first] == [side.id for side in second]
    assert ledger.pair_writes == 1
    assert savings.balance.signed_amount == Decimal("3000000")
    assert card.balance.signed_amount == Decimal("1540258")


def test_the_second_attempt_announces_nothing() -> None:
    accounts, _, publisher, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")
    use_case.execute(_command(savings, card))
    publisher.published.clear()

    use_case.execute(_command(savings, card))

    assert publisher.published == []


# ------------------------------------------------------------ what is refused


def test_an_account_that_does_not_exist_is_refused() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")

    with pytest.raises(AccountNotFoundError):
        use_case.execute(
            _command(savings, card, destination_account_id=AccountId.new())
        )

    assert ledger.rows == {}


def test_another_users_account_cannot_be_either_side() -> None:
    """Isolation where it is cheapest to lose: both ids are looked up, and an
    id is guessable in a way a partition is not."""
    accounts, ledger, _, use_case = _fixture()
    mine = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    theirs = _account(
        accounts, kind=AccountKind.CREDIT_CARD, holds="0", owner=OTHER_USER
    )

    with pytest.raises(AccountNotFoundError):
        use_case.execute(_command(mine, theirs))

    with pytest.raises(AccountNotFoundError):
        use_case.execute(_command(theirs, mine))

    assert ledger.rows == {}
    assert theirs.balance.signed_amount == Decimal("0")


def test_one_account_paying_itself_is_refused() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")

    with pytest.raises(ValueError, match="two different accounts"):
        use_case.execute(_command(savings, savings))

    assert ledger.rows == {}


def test_a_closed_destination_moves_neither_side() -> None:
    """Refused before the source is applied, not only before anything is
    stored: half a pair must never land, not even in memory."""
    accounts, ledger, publisher, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")
    card.close(closed_at=PAID_AT)

    with pytest.raises(AccountClosedError):
        use_case.execute(_command(savings, card))

    assert ledger.rows == {}
    assert publisher.published == []
    assert savings.balance.signed_amount == Decimal("5000000")


def test_two_currencies_are_refused_rather_than_converted() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(
        accounts,
        kind=AccountKind.CREDIT_CARD,
        holds="0",
        currency=Currency.USD,
    )

    with pytest.raises(CurrencyMismatchError):
        use_case.execute(_command(savings, card))

    assert ledger.rows == {}
    assert savings.balance.signed_amount == Decimal("5000000")


# ------------------------------------------------------------ two at once


class RacedLedger(FakeLedger):
    """Another request writes the same pair between this one's read and its
    write — the case the conditional write exists for."""

    def record_pair(
        self,
        *,
        source: Transaction,
        destination: Transaction,
        source_delta: Decimal,
        destination_delta: Decimal,
    ) -> bool:
        super().record_pair(
            source=source,
            destination=destination,
            source_delta=source_delta,
            destination_delta=destination_delta,
        )

        return False


def test_losing_the_race_answers_with_what_the_winner_stored() -> None:
    accounts, ledger, publisher = FakeAccounts(), RacedLedger(), RecordingPublisher()
    use_case = EnterTransferBetweenUseCase(
        accounts=accounts,
        ledger=ledger,
        pairs=ledger,
        event_publisher=publisher,
    )
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    card = _account(accounts, kind=AccountKind.CREDIT_CARD, holds="0")

    leaving, arriving = use_case.execute(_command(savings, card))

    assert leaving.id.value in ledger.rows
    assert arriving.id.value in ledger.rows
    # The winner announced it; the loser's events would read as new work.
    assert publisher.published == []


# ---------------------------------------------- a cuota to a watched credit


def _mortgage(accounts: FakeAccounts) -> Account:
    mortgage = Account.open(
        user_id=USER,
        name="Hipoteca",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=PAID_AT,
        opening_balance=Money(amount=Decimal("60000000"), currency=Currency.COP),
    )
    mortgage.pull_events()
    accounts.add(mortgage)

    return mortgage


def test_a_cuota_lowers_the_savings_and_the_mortgage_in_one_write() -> None:
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    mortgage = _mortgage(accounts)

    leaving, arriving = use_case.execute(_command(savings, mortgage))

    assert ledger.pair_writes == 1
    assert savings.balance.signed_amount == Decimal("3000000")
    assert mortgage.balance.signed_amount == Decimal("58000000")
    # Spending where it left, so a total still counts the cuota once.
    assert leaving.is_transfer is False
    assert arriving.is_transfer is True


def test_a_cuota_half_erased_and_entered_again_is_refused() -> None:
    """The savings side was deleted on its own and the leg on the mortgage
    stayed: answering with the pair in memory would claim a payment no row
    records."""
    accounts, ledger, _, use_case = _fixture()
    savings = _account(accounts, kind=AccountKind.SAVINGS, holds="5000000")
    mortgage = _mortgage(accounts)
    leaving, _ = use_case.execute(_command(savings, mortgage))
    del ledger.rows[leaving.id.value]

    with pytest.raises(TransferLegError, match="already recorded"):
        use_case.execute(_command(savings, mortgage))
