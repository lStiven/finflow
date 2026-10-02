"""A week against its owner's own normal — the figure Monday's summary says."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.application.weekly_spending import (
    ReadWeeklySpendingUseCase,
    WeeklySpending,
    WeeklySpendingQuery,
    monday_of,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.new()
TIMEZONE = "America/Bogota"

# Monday 21 September 2026; the week read is 21 to 27 September.
MONDAY = dt.date(2026, 9, 21)

ARA = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Ara",
    category="groceries",
    needs_review=False,
)
ANDRES = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000002",
    display_name="Andrés",
    category="restaurants",
    needs_review=False,
)
VET = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000003",
    display_name="Vet",
    category="custom:gatos",
    needs_review=False,
)
LOOSE = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000004",
    display_name="Loose",
    category="uncategorized",
    needs_review=True,
)


class FakeDirectory:
    def __init__(self) -> None:
        self._known = {"ARA": ARA, "ANDRES": ANDRES, "VET": VET, "LOOSE": LOOSE}

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            text: self._known[text] for text in counterparties if text in self._known
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        return frozenset(self.category_labels(user_id=user_id))

    def category_labels(self, *, user_id: UserId) -> Mapping[str, str]:
        del user_id

        return {
            "groceries": "Groceries",
            "restaurants": "Restaurants",
            "uncategorized": "Uncategorized",
            "custom:gatos": "Gatos",
        }

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        raise NotImplementedError


class InMemoryLedger:
    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        raise NotImplementedError

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        raise NotImplementedError

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        raise NotImplementedError

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        raise NotImplementedError

    def save(self, transaction: Transaction) -> None:
        raise NotImplementedError

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        raise NotImplementedError


class NoAccounts:
    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return None

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return []

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        raise NotImplementedError

    def save(self, account: Account) -> None:
        raise NotImplementedError

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        raise NotImplementedError

    def overwrite_balance(self, account: Account) -> None:
        raise NotImplementedError

    def restate_balance(self, account: Account) -> None:
        raise NotImplementedError

    def add(self, account: Account) -> bool:
        raise NotImplementedError


def _noon(day: dt.date) -> PosixTime:
    # Noon in Bogotá is 17:00 UTC.
    return PosixTime.from_datetime(
        dt.datetime.combine(day, dt.time(17), tzinfo=dt.UTC),
    )


class World:
    def __init__(self) -> None:
        self.ledger = InMemoryLedger()
        self.directory = FakeDirectory()

    def spend(
        self,
        amount: str,
        on: dt.date,
        *,
        counterparty: str = "ARA",
        direction: MovementDirection = MovementDirection.OUTGOING,
        currency: Currency = Currency.COP,
        at: PosixTime | None = None,
        account_id: AccountId | None = None,
    ) -> Transaction:
        movement = Transaction.enter_manually(
            user_id=USER,
            direction=direction,
            amount=Money(amount=Decimal(amount), currency=currency),
            occurred_at=at or _noon(on),
            counterparty=counterparty,
            account_id=account_id,
        )
        self.ledger.rows[movement.id.value] = movement

        return movement

    def read(self, week_of: dt.date = MONDAY) -> WeeklySpending:
        return ReadWeeklySpendingUseCase(
            ledger=self.ledger,
            spending=SummarizeSpendingUseCase(
                ledger=self.ledger,
                accounts=NoAccounts(),
                merchants=self.directory,
            ),
            categories=self.directory,
        ).execute(
            WeeklySpendingQuery(user_id=USER, timezone=TIMEZONE, week_of=week_of),
        )


def _weeks_ago(count: int, weekday: int = 2) -> dt.date:
    return MONDAY - dt.timedelta(weeks=count) + dt.timedelta(days=weekday)


def test_the_week_is_monday_to_sunday_whatever_day_is_asked() -> None:
    week = World().read(dt.date(2026, 9, 24))

    assert week.week_start == MONDAY
    assert week.week_end == dt.date(2026, 9, 27)
    assert monday_of(dt.date(2026, 9, 27)) == MONDAY


def test_nothing_spent_ever_has_nothing_to_say() -> None:
    assert World().read().has_anything_to_say is False


def test_the_week_is_compared_with_the_average_of_the_four_before_it() -> None:
    world = World()
    for back, amount in ((1, "100000"), (2, "200000"), (3, "300000"), (4, "400000")):
        world.spend(amount, _weeks_ago(back))
    world.spend("150000", MONDAY + dt.timedelta(days=1))
    world.spend("50000", MONDAY + dt.timedelta(days=6))

    [cop] = world.read().currencies

    assert cop.spent == Decimal("200000")
    assert cop.movements == 2
    assert cop.typical == Decimal("250000.00")
    assert cop.history_weeks == 4


def test_weeks_before_the_owner_arrived_are_not_counted_as_zero() -> None:
    # First movement two weeks ago: the normal is two weeks, not four with
    # two empty ones that would make every week of theirs look expensive.
    world = World()
    world.spend("100000", _weeks_ago(2))
    world.spend("300000", _weeks_ago(1))
    world.spend("200000", MONDAY)

    [cop] = world.read().currencies

    assert cop.history_weeks == 2
    assert cop.typical == Decimal("200000.00")


def test_a_quiet_week_inside_the_history_does_count_as_zero() -> None:
    world = World()
    world.spend("400000", _weeks_ago(4))
    world.spend("200000", MONDAY)

    [cop] = world.read().currencies

    assert cop.history_weeks == 4
    assert cop.typical == Decimal("100000.00")


def test_a_first_week_has_no_normal_yet() -> None:
    world = World()
    world.spend("80000", MONDAY + dt.timedelta(days=2))

    [cop] = world.read().currencies

    assert cop.typical is None
    assert cop.history_weeks == 0
    assert cop.rise is None


def test_income_and_transfers_are_not_spending() -> None:
    world = World()
    world.spend("5000000", MONDAY, direction=MovementDirection.INCOMING)
    account = Account.open(
        user_id=USER,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=_noon(MONDAY),
    ).id
    world.spend("900000", MONDAY, account_id=account).declare_transfer()
    world.spend("40000", MONDAY)

    [cop] = world.read().currencies

    assert cop.spent == Decimal("40000")
    assert cop.movements == 1


def test_the_week_ends_at_sunday_midnight_in_bogota_not_in_utc() -> None:
    world = World()
    # Sunday 27 September, 20:00 in Bogotá — already Monday in UTC.
    world.spend(
        "70000",
        MONDAY,
        at=PosixTime.from_datetime(dt.datetime(2026, 9, 28, 1, tzinfo=dt.UTC)),
    )
    # Monday 28 September, 00:30 in Bogotá: next week.
    world.spend(
        "999000",
        MONDAY,
        at=PosixTime.from_datetime(dt.datetime(2026, 9, 28, 5, 30, tzinfo=dt.UTC)),
    )

    [cop] = world.read().currencies

    assert cop.spent == Decimal("70000")


def test_what_went_up_is_the_named_category_furthest_above_its_own_normal() -> None:
    world = World()
    for back in range(1, 5):
        world.spend("100000", _weeks_ago(back), counterparty="ARA")
        world.spend("50000", _weeks_ago(back), counterparty="ANDRES")
    world.spend("120000", MONDAY, counterparty="ARA")  # +20.000
    world.spend("140000", MONDAY, counterparty="ANDRES")  # +90.000
    world.spend("500000", MONDAY, counterparty="LOOSE")  # never named
    world.spend("600000", MONDAY, counterparty="NADIE")  # no merchant

    [cop] = world.read().currencies

    assert cop.rise is not None
    assert cop.rise.category == "restaurants"
    assert cop.rise.label == "Restaurants"
    assert cop.rise.spent == Decimal("140000")
    assert cop.rise.typical == Decimal("50000.00")


def test_a_category_of_ones_own_carries_its_owners_name() -> None:
    world = World()
    world.spend("10000", _weeks_ago(1), counterparty="ARA")
    world.spend("90000", MONDAY, counterparty="VET")

    [cop] = world.read().currencies

    assert cop.rise is not None
    assert cop.rise.label == "Gatos"


def test_nothing_went_up_when_every_category_went_down() -> None:
    world = World()
    world.spend("300000", _weeks_ago(1), counterparty="ARA")
    world.spend("100000", MONDAY, counterparty="ARA")

    [cop] = world.read().currencies

    assert cop.rise is None


def test_two_currencies_are_never_added_together_and_the_busier_leads() -> None:
    world = World()
    world.spend("100", MONDAY, currency=Currency.USD)
    world.spend("300000", MONDAY, currency=Currency.COP)
    world.spend("20000", MONDAY, currency=Currency.COP)

    currencies = world.read().currencies

    assert [(week.currency, week.spent) for week in currencies] == [
        (Currency.COP, Decimal("320000")),
        (Currency.USD, Decimal("100")),
    ]


def test_a_week_with_nothing_spent_but_a_history_still_says_so() -> None:
    world = World()
    world.spend("100000", _weeks_ago(1))

    [cop] = world.read().currencies

    assert cop.spent == Decimal(0)
    assert cop.movements == 0
    # Arrived last week: one week of history, not four.
    assert cop.typical == Decimal("100000.00")
    assert cop.history_weeks == 1
