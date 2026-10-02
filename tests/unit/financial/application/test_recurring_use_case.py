"""What the detector proposes, and — mostly — what it refuses to propose.

The domain already pins which sets of dates are a rhythm. What is pinned here
is everything the ledger brings with it: that the same gym spelled two ways is
one series, that what this application wrote itself never comes back as a
suggestion, and that a bill already declared is marked rather than proposed
again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.ports import MerchantAttribution
from personal_finance.contexts.financial.application.recurring import (
    DetectRecurringQuery,
    DetectRecurringSeriesUseCase,
    RecurringView,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
    TransactionOrigin,
    TransferId,
    TransferLeg,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.new()
TIMEZONE = "America/Bogota"
# Bogotá is UTC-5, so anything recorded before 19:00 UTC is the same day in
# both zones. The nine-in-the-evening case is built on purpose further down.
NOON = dt.time(hour=17)


class FakeHistory:
    """The one thing the detector is allowed to ask the ledger."""

    def __init__(self, rows: Sequence[Transaction] = ()) -> None:
        self.rows = list(rows)

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows if row.user_id == user_id]


class FakeBills:
    def __init__(self, bills: Sequence[ScheduledBill] = ()) -> None:
        self.bills = list(bills)

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        return next(
            (
                bill
                for bill in self.bills
                if bill.user_id == user_id and bill.id == bill_id
            ),
            None,
        )

    def list_by_user(self, user_id: UserId) -> Sequence[ScheduledBill]:
        return [bill for bill in self.bills if bill.user_id == user_id]

    def save(self, bill: ScheduledBill) -> None:
        self.bills = [each for each in self.bills if each.id != bill.id] + [bill]

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        del user_id, bill_id

        return False


class FakeDirectory:
    """Merchant's answer, by exact counterparty text."""

    def __init__(self, known: Mapping[str, MerchantAttribution] | None = None) -> None:
        self._known = dict(known or {})
        self.asked: list[Sequence[str]] = []

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id
        self.asked.append(list(counterparties))

        return {
            counterparty: self._known[counterparty]
            for counterparty in counterparties
            if counterparty in self._known
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return frozenset({"subscriptions", "health"})

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        del user_id, counterparty, category, occurred_at

        return None


def merchant(
    merchant_id: str,
    *,
    name: str = "Gimnasio Bodytech",
    category: str = "health",
) -> MerchantAttribution:
    return MerchantAttribution(
        merchant_id=merchant_id,
        display_name=name,
        category=category,
        needs_review=False,
    )


def movement(
    day: str,
    *,
    counterparty: str = "GYMSA*BOG",
    amount: str = "120000",
    currency: Currency = Currency.COP,
    direction: MovementDirection = MovementDirection.OUTGOING,
    origin: TransactionOrigin = TransactionOrigin.MANUAL,
    account_id: AccountId | None = None,
    transfer: TransferLeg | None = None,
    at: dt.time = NOON,
    user_id: UserId = USER,
) -> Transaction:
    """One row of the ledger, built through the aggregate's own factory.

    The origin and the transfer leg are set afterwards rather than faked into
    the factory: what the detector reads is a stored movement, and the two
    fields it excludes on are exactly the ones no manual entry can carry.
    """
    entered = Transaction.enter_manually(
        user_id=user_id,
        direction=direction,
        amount=Money(amount=Decimal(amount), currency=currency),
        occurred_at=PosixTime.from_datetime(
            dt.datetime.combine(dt.date.fromisoformat(day), at, tzinfo=dt.UTC),
        ),
        counterparty=counterparty,
        account_id=account_id,
    )
    entered.origin = origin
    entered.transfer = transfer

    return entered


def detect(
    rows: Sequence[Transaction],
    *,
    bills: Sequence[ScheduledBill] = (),
    directory: FakeDirectory | None = None,
) -> RecurringView:
    use_case = DetectRecurringSeriesUseCase(
        ledger=FakeHistory(rows),
        bills=FakeBills(bills),
        merchants=directory,
    )

    return use_case.execute(DetectRecurringQuery(user_id=USER, timezone=TIMEZONE))


def monthly_on(day: int, *, months: int = 4, **rest: object) -> list[Transaction]:
    """`months` charges on the same day, the most recent one last month.

    Built backwards from today so the series is always current whenever the
    suite runs — a fixed date would turn dormant on its own and the test would
    start failing for a reason that has nothing to do with the code.
    """
    today = dt.date.today()
    first = dt.date(today.year, today.month, 1)
    days: list[str] = []

    for step in range(months, 0, -1):
        total = first.month - 1 - step
        year = first.year + total // 12
        month = total % 12 + 1
        days.append(dt.date(year, month, day).isoformat())

    return [movement(day_of, **rest) for day_of in days]  # type: ignore[arg-type]


class TestGrouping:
    def test_one_gym_spelled_two_ways_is_one_series(self) -> None:
        """The whole reason the key is the merchant and not the text."""
        directory = FakeDirectory(
            {
                "GYMSA*BOG": merchant("gym-1"),
                "PAGO GYM SA": merchant("gym-1"),
            },
        )
        rows = [
            *monthly_on(4, months=2, counterparty="GYMSA*BOG"),
            *monthly_on(4, months=4, counterparty="PAGO GYM SA")[:2],
        ]

        view = detect(rows, directory=directory)

        assert len(view.series) == 1
        found = view.series[0]
        assert found.key == "merchant:gym-1|outgoing|COP"
        assert found.name == "Gimnasio Bodytech"
        assert found.category == "health"
        assert found.series.sightings == 4

    def test_without_a_directory_the_folded_text_stands_in(self) -> None:
        view = detect(monthly_on(4, counterparty="Netflix  COL"))

        assert len(view.series) == 1
        assert view.series[0].key == "text:netflix col|outgoing|COP"
        assert view.series[0].name == "Netflix  COL"
        assert view.series[0].merchant_id is None

    def test_two_currencies_at_one_merchant_are_two_series(self) -> None:
        rows = [
            *monthly_on(9, counterparty="ICLOUD", amount="4500"),
            *monthly_on(9, counterparty="ICLOUD", amount="3", currency=Currency.USD),
        ]

        view = detect(rows)

        assert {found.series.amount.currency for found in view.series} == {
            Currency.COP,
            Currency.USD,
        }
        # And two keys, not one: a list keyed on the counterparty alone would
        # draw two rows with one key, and marking either would mark both.
        assert len({found.key for found in view.series}) == 2

    def test_the_day_is_read_where_the_owner_lives(self) -> None:
        """Nine in the evening in Bogotá is already tomorrow in UTC.

        Read in UTC these charges land on the 16th, the 15th and the 15th, and
        no cadence survives that.
        """
        rows = monthly_on(15, at=dt.time(hour=2))

        view = detect(rows)

        assert len(view.series) == 1
        assert view.series[0].series.next_due_on.day == 14


class TestExclusions:
    def test_what_the_app_computed_itself_is_never_a_suggestion(self) -> None:
        """The interest of a credit is perfectly monthly and perfectly equal.
        Unfiltered it would head the ranking."""
        view = detect(
            monthly_on(
                1,
                counterparty="Intereses",
                origin=TransactionOrigin.ACCRUAL,
            ),
        )

        assert view.series == ()

    def test_what_a_declared_bill_wrote_is_never_a_suggestion(self) -> None:
        """Otherwise the detector reads its own handwriting back."""
        view = detect(
            monthly_on(
                4,
                counterparty="Gimnasio",
                origin=TransactionOrigin.SCHEDULED,
            ),
        )

        assert view.series == ()

    def test_paying_the_card_every_month_is_not_a_subscription(self) -> None:
        leg = TransferLeg(
            transfer_id=TransferId(value="transfer-1"),
            role=TransferRole.SOURCE,
        )

        view = detect(monthly_on(20, counterparty="Pago tarjeta", transfer=leg))

        assert view.series == ()

    def test_a_salary_is_detected_and_marked_rather_than_dropped(self) -> None:
        """E3 wants it. What it must never do is be netted against spending,
        which is why nothing here totals anything."""
        view = detect(
            monthly_on(
                30,
                counterparty="NOMINA ACME",
                amount="4200000",
                direction=MovementDirection.INCOMING,
            ),
        )

        assert len(view.series) == 1
        assert view.series[0].series.direction is MovementDirection.INCOMING


class TestAlreadyDeclared:
    def test_a_declared_bill_is_marked_through_its_merchant(self) -> None:
        """The bill says "Gimnasio" and the charges say `GYMSA*BOG`. Only the
        merchant knows those are the same thing."""
        directory = FakeDirectory(
            {
                "GYMSA*BOG": merchant("gym-1"),
                "Gimnasio": merchant("gym-1"),
            },
        )
        bill = ScheduledBill.declare(
            user_id=USER,
            name="Gimnasio",
            amount=Money(amount=Decimal("120000"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date(2026, 1, 4),
        )

        view = detect(monthly_on(4), bills=[bill], directory=directory)

        assert len(view.series) == 1
        assert view.series[0].bill_id == bill.id

    def test_a_bill_nothing_has_attributed_still_marks_its_own_series(
        self,
    ) -> None:
        """The ordinary case, and the one that used to be missed.

        Nothing ever attributes a name somebody typed — or one this screen
        wrote by accepting a suggestion — so matching only through the
        merchant left the suggestion un-marked and the list went on offering
        it, making a second bill on every click.
        """
        bill = ScheduledBill.declare(
            user_id=USER,
            name="Spotify",
            amount=Money(amount=Decimal("16900"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date(2026, 1, 9),
        )

        view = detect(
            monthly_on(9, counterparty="Spotify", amount="16900"),
            bills=[bill],
        )

        assert len(view.series) == 1
        assert view.series[0].bill_id == bill.id

    def test_a_bill_in_one_currency_says_nothing_about_another(self) -> None:
        bill = ScheduledBill.declare(
            user_id=USER,
            name="ICLOUD",
            amount=Money(amount=Decimal("4500"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date(2026, 1, 9),
        )

        view = detect(
            monthly_on(9, counterparty="ICLOUD", amount="3", currency=Currency.USD),
            bills=[bill],
        )

        assert len(view.series) == 1
        assert view.series[0].bill_id is None

    def test_an_undeclared_series_carries_no_bill(self) -> None:
        view = detect(monthly_on(4, counterparty="Spotify"))

        assert len(view.series) == 1
        assert view.series[0].bill_id is None

    def test_both_sides_are_resolved_in_one_round_trip(self) -> None:
        directory = FakeDirectory({"GYMSA*BOG": merchant("gym-1")})
        bill = ScheduledBill.declare(
            user_id=USER,
            name="Gimnasio",
            amount=Money(amount=Decimal("120000"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date(2026, 1, 4),
        )

        detect(monthly_on(4), bills=[bill], directory=directory)

        assert len(directory.asked) == 1
        assert "Gimnasio" in directory.asked[0]


class TestWindow:
    def test_nothing_older_than_the_history_window_counts(self) -> None:
        old = movement("2019-05-04", counterparty="GYMSA*BOG")

        view = detect([old, *monthly_on(4, months=2)])

        # Two sightings inside the window, and the ancient one does not make a
        # third.
        assert view.series == ()

    def test_the_window_is_reported_so_a_screen_can_say_what_it_looked_at(
        self,
    ) -> None:
        view = detect(monthly_on(4))

        assert (view.until - view.since).days >= 365


class TestOrder:
    def test_the_surest_series_comes_first(self) -> None:
        rows = [
            *monthly_on(4, months=6, counterparty="GYMSA*BOG"),
            *monthly_on(20, months=3, counterparty="SPOTIFY", amount="16900"),
        ]

        view = detect(rows)

        assert [found.name for found in view.series] == ["GYMSA*BOG", "SPOTIFY"]
        assert view.series[0].series.confidence > view.series[1].series.confidence

    def test_another_owners_rhythms_are_not_read(self) -> None:
        view = detect(monthly_on(4, user_id=UserId.new()))

        assert view.series == ()
