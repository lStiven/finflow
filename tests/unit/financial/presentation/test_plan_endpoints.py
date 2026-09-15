"""The plan and the allowance over HTTP.

What is pinned here is the shape of the two answers and the one status code
that carries meaning: **404 when nothing is declared**, on both endpoints. A
200 with zeros would read as «you have nothing left to spend», which is a
different thing from «you have not told me what the month looks like» and the
only one of the two that is somebody's actual situation.
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

from personal_finance.contexts.financial.application.allowance import (
    ManageMonthlyPlanUseCase,
    ReadMonthlyAllowanceUseCase,
)
from personal_finance.contexts.financial.application.bills import ListBillsUseCase
from personal_finance.contexts.financial.application.ports import BalanceReversal
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.plan import MonthlyPlan
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    MovementDirection,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_manage_plan_use_case,
    get_read_allowance_use_case,
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
MIDDAY = dt.time(hour=17)


class InMemoryPlans:
    def __init__(self) -> None:
        self.rows: dict[UserId, MonthlyPlan] = {}

    def find(self, *, user_id: UserId) -> MonthlyPlan | None:
        return self.rows.get(user_id)

    def save(self, plan: MonthlyPlan) -> None:
        self.rows[plan.user_id] = plan

    def remove(self, *, user_id: UserId) -> bool:
        return self.rows.pop(user_id, None) is not None


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


class InMemoryLedger:
    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def keep(self, movement: Transaction) -> Transaction:
        self.rows[movement.id.value] = movement

        return movement

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        return {
            movement_id: self.rows[movement_id]
            for movement_id in movement_ids
            if movement_id in self.rows and self.rows[movement_id].user_id == user_id
        }

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id == account_id
        ]

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            row
            for row in self.rows.values()
            if row.user_id == user_id and row.account_id is None
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]

    # The rest of the port, which neither of the two use cases under test
    # reads. They raise rather than pretend: a summary or a bills listing that
    # ever wrote something would fail here loudly instead of passing quietly.

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

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        raise NotImplementedError

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        raise NotImplementedError


class InMemoryAccounts:
    def __init__(self) -> None:
        self.rows: dict[AccountId, Account] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.rows.get(account_id)

        return account if account is not None and account.user_id == user_id else None

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return [account for account in self.rows.values() if account.user_id == user_id]

    # The rest of the port, which neither of the two use cases under test
    # reads. They raise rather than pretend: a summary or a bills listing that
    # ever wrote something would fail here loudly instead of passing quietly.

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


@dataclasses.dataclass(slots=True)
class Wiring:
    client: TestClient
    plans: InMemoryPlans
    bills: InMemoryBills
    ledger: InMemoryLedger


@pytest.fixture
def wired() -> Wiring:
    plans = InMemoryPlans()
    bills = InMemoryBills()
    ledger = InMemoryLedger()
    accounts = InMemoryAccounts()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_manage_plan_use_case] = lambda: (
        ManageMonthlyPlanUseCase(plans=plans)
    )
    app.dependency_overrides[get_read_allowance_use_case] = lambda: (
        ReadMonthlyAllowanceUseCase(
            plans=plans,
            spending=SummarizeSpendingUseCase(ledger=ledger, accounts=accounts),
            bills=ListBillsUseCase(bills=bills, accounts=accounts, charges=ledger),
        )
    )

    return Wiring(client=TestClient(app), plans=plans, bills=bills, ledger=ledger)


def _declare(client: TestClient, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    payload = {"expected_income": "5000000", "currency": "COP", **overrides}
    response = client.put("/financial/plan", json=payload)

    assert response.status_code == 200, response.text

    return response.json()


def _spend(wired: Wiring, amount: str, *, day: int = 5) -> None:
    now = dt.datetime.now(tz=dt.UTC).date()
    wired.ledger.keep(
        Transaction.enter_manually(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal(amount), currency=Currency.COP),
            occurred_at=PosixTime.from_datetime(
                dt.datetime.combine(now.replace(day=day), MIDDAY, tzinfo=dt.UTC),
            ),
            counterparty="Mercado",
        ),
    )


class TestDeclaring:
    def test_a_plan_is_stated_whole_and_read_back(self, wired: Wiring) -> None:
        body = _declare(wired.client, savings_target="1000000")

        assert body["expected_income"] == "5000000"
        assert body["savings_target"] == "1000000"
        assert body["currency"] == "COP"
        assert body["updated_at"] > 0

        read = wired.client.get("/financial/plan")
        assert read.status_code == 200
        assert read.json()["expected_income"] == "5000000"

    def test_restating_replaces_rather_than_merges(self, wired: Wiring) -> None:
        _declare(wired.client, savings_target="1000000")
        body = _declare(wired.client, expected_income="3000000")

        assert body["savings_target"] == "0"

    def test_an_income_of_nothing_is_refused_by_the_payload(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.put(
            "/financial/plan",
            json={"expected_income": "0", "currency": "COP"},
        )

        assert response.status_code == 422

    def test_keeping_more_than_you_earn_is_refused(self, wired: Wiring) -> None:
        response = wired.client.put(
            "/financial/plan",
            json={
                "expected_income": "5000000",
                "currency": "COP",
                "savings_target": "6000000",
            },
        )

        assert response.status_code == 400

    def test_forgetting_it_takes_the_card_away(self, wired: Wiring) -> None:
        _declare(wired.client)

        assert wired.client.delete("/financial/plan").status_code == 204
        assert wired.client.get("/financial/plan").status_code == 404
        assert wired.client.get("/financial/allowance").status_code == 404

    def test_forgetting_twice_is_silent(self, wired: Wiring) -> None:
        assert wired.client.delete("/financial/plan").status_code == 204


class TestTheNumber:
    def test_nothing_declared_is_404_and_never_a_zero(self, wired: Wiring) -> None:
        """«No me has dicho cómo es el mes» is not «no te queda nada»."""
        assert wired.client.get("/financial/allowance").status_code == 404

    def test_it_answers_with_every_piece_of_the_subtraction(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, savings_target="500000")
        _spend(wired, "300000")

        response = wired.client.get("/financial/allowance")

        assert response.status_code == 200
        body = response.json()
        assert body["expected_income"] == "5000000"
        assert body["savings_target"] == "500000"
        assert body["spent"] == "300000"
        assert body["committed"] == "0"
        assert body["available"] == "4200000"
        assert body["currency"] == "COP"
        assert body["days_left"] >= 1
        assert body["since"] <= body["until"]

    def test_an_unpaid_bill_is_committed_and_a_paid_one_is_spent(
        self,
        wired: Wiring,
    ) -> None:
        """The two figures never count the same charge twice."""
        _declare(wired.client)
        now = dt.datetime.now(tz=dt.UTC).date()
        bill = ScheduledBill.declare(
            user_id=USER_ID,
            name="Gimnasio",
            amount=Money(amount=Decimal("120000"), currency=Currency.COP),
            cadence=BillCadence.MONTHLY,
            starts_on=now.replace(day=1),
        )
        wired.bills.save(bill)

        unpaid = wired.client.get("/financial/allowance").json()
        assert unpaid["committed"] == "120000"
        assert unpaid["spent"] == "0"
        assert unpaid["available"] == "4880000"

        # The row a confirmation writes, under the id the bill derives — which
        # is the whole of how «paid» is answered.
        paid = Transaction.enter_manually(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal("120000"), currency=Currency.COP),
            occurred_at=PosixTime.from_datetime(
                dt.datetime.combine(bill.starts_on, MIDDAY, tzinfo=dt.UTC),
            ),
            counterparty=bill.name,
        )
        paid.id = bill.charge_id(bill.starts_on)
        wired.ledger.keep(paid)

        settled = wired.client.get("/financial/allowance").json()
        assert settled["committed"] == "0"
        assert settled["spent"] == "120000"
        assert settled["available"] == "4880000"

    def test_an_unknown_timezone_is_refused(self, wired: Wiring) -> None:
        _declare(wired.client)

        response = wired.client.get("/financial/allowance?timezone=Mars/Olympus")

        assert response.status_code == 400
