"""The suggestion surface, over fakes.

One property is worth more than the rest and this file exists to keep it
visible: **the detector never writes.** Asking for suggestions leaves the
ledger and the bills exactly as they were, however many series come back, and
accepting one is a `POST /financial/bills` like any other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.ports import MerchantAttribution
from personal_finance.contexts.financial.application.recurring import (
    DetectRecurringSeriesUseCase,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
    TransactionOrigin,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_detect_recurring_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
# Midday UTC is the same calendar day in Bogotá, so a fixture's date is the
# date the endpoint reads.
MIDDAY = dt.time(hour=17)


class InMemoryHistory:
    def __init__(self) -> None:
        self.rows: list[Transaction] = []

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows if row.user_id == user_id]


class InMemoryBills:
    def __init__(self) -> None:
        self.rows: dict[BillId, ScheduledBill] = {}

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        bill = self.rows.get(bill_id)

        return bill if bill is not None and bill.user_id == user_id else None

    def list_by_user(self, user_id: UserId) -> list[ScheduledBill]:
        return [bill for bill in self.rows.values() if bill.user_id == user_id]

    def save(self, bill: ScheduledBill) -> None:
        self.rows[bill.id] = bill

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        del user_id

        return self.rows.pop(bill_id, None) is not None


class FakeMerchants:
    def __init__(self, known: Mapping[str, MerchantAttribution] | None = None) -> None:
        self.known = dict(known or {})

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            counterparty: self.known[counterparty]
            for counterparty in counterparties
            if counterparty in self.known
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return frozenset({"health", "subscriptions"})

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


@dataclasses.dataclass(slots=True)
class Wiring:
    client: TestClient
    history: InMemoryHistory
    bills: InMemoryBills
    merchants: FakeMerchants


@pytest.fixture
def wired() -> Wiring:
    history = InMemoryHistory()
    bills = InMemoryBills()
    merchants = FakeMerchants()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_detect_recurring_use_case] = lambda: (
        DetectRecurringSeriesUseCase(
            ledger=history,
            bills=bills,
            merchants=merchants,
        )
    )

    return Wiring(
        client=TestClient(app),
        history=history,
        bills=bills,
        merchants=merchants,
    )


def _charge(
    wired: Wiring,
    day: dt.date,
    *,
    counterparty: str = "NETFLIX COL",
    amount: str = "44900",
    direction: MovementDirection = MovementDirection.OUTGOING,
    origin: TransactionOrigin = TransactionOrigin.MANUAL,
) -> None:
    movement = Transaction.enter_manually(
        user_id=USER_ID,
        direction=direction,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=PosixTime.from_datetime(
            dt.datetime.combine(day, MIDDAY, tzinfo=dt.UTC),
        ),
        counterparty=counterparty,
    )
    movement.origin = origin
    wired.history.rows.append(movement)


def _monthly(wired: Wiring, *, day: int = 15, months: int = 4, **rest: Any) -> None:  # noqa: ANN401
    """Charges on the same day of the last `months` months.

    Anchored on today rather than on a fixed date so the series is current
    whenever the suite runs: a hardcoded year would go dormant on its own and
    the test would start failing for a reason unrelated to the code.
    """
    first = dt.date.today().replace(day=1)

    for step in range(months, 0, -1):
        total = first.month - 1 - step
        year = first.year + total // 12
        month = total % 12 + 1
        _charge(wired, dt.date(year, month, day), **rest)


def _series(client: TestClient) -> dict[str, Any]:
    response = client.get("/financial/recurring")

    assert response.status_code == 200

    return response.json()


class TestReading:
    def test_a_monthly_charge_comes_back_as_a_suggestion(self, wired: Wiring) -> None:
        _monthly(wired)

        body = _series(wired.client)

        assert body["months"] == 25
        assert len(body["series"]) == 1
        found = body["series"][0]
        assert found["name"] == "NETFLIX COL"
        assert found["cadence"] == "monthly"
        assert found["direction"] == "outgoing"
        assert found["amount"] == "44900"
        assert found["currency"] == "COP"
        assert found["variable"] is False
        assert found["sightings"] == 4
        assert found["bill_id"] is None
        # A string, like every other decimal crossing this boundary.
        assert Decimal(found["confidence"]) > Decimal("0.5")

    def test_nothing_recurring_is_an_empty_list_and_a_window(
        self,
        wired: Wiring,
    ) -> None:
        """Which reads as "not enough history yet", not as "you have none"."""
        _charge(wired, dt.date.today() - dt.timedelta(days=3))

        body = _series(wired.client)

        assert body["series"] == []
        assert body["since"] < body["until"]

    def test_an_unknown_timezone_is_refused_rather_than_read_as_utc(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.get("/financial/recurring?timezone=Mars/Olympus")

        assert response.status_code == 400


class TestItNeverWrites:
    def test_asking_for_suggestions_writes_nothing(self, wired: Wiring) -> None:
        """The property this endpoint lives or dies by."""
        _monthly(wired)
        before = len(wired.history.rows)

        _series(wired.client)

        assert len(wired.history.rows) == before
        assert wired.bills.rows == {}


class TestAlreadyDeclared:
    def test_a_declared_bill_is_marked_rather_than_proposed_blind(
        self,
        wired: Wiring,
    ) -> None:
        wired.merchants.known = {
            "NETFLIX COL": MerchantAttribution(
                merchant_id="netflix",
                display_name="Netflix",
                category="subscriptions",
                needs_review=False,
            ),
            "Netflix": MerchantAttribution(
                merchant_id="netflix",
                display_name="Netflix",
                category="subscriptions",
                needs_review=False,
            ),
        }
        bill = ScheduledBill.declare(
            user_id=USER_ID,
            name="Netflix",
            amount=Money(amount=Decimal("44900"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=dt.date.today().replace(day=15),
        )
        wired.bills.save(bill)
        _monthly(wired)

        body = _series(wired.client)

        assert len(body["series"]) == 1
        found = body["series"][0]
        assert found["bill_id"] == str(bill.id.value)
        assert found["merchant_id"] == "netflix"
        assert found["name"] == "Netflix"
        assert found["category"] == "subscriptions"
