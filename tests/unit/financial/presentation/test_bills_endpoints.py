"""The bills surface a frontend calls, over fakes.

Its own file rather than more rows in `test_financial_endpoints.py`: nothing
here touches the ledger or a balance, and that is the property most worth
being able to see at a glance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.bills import (
    ListBillsUseCase,
    ManageBillsUseCase,
)
from personal_finance.contexts.financial.domain.bills import BillId, ScheduledBill
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_list_bills_use_case,
    get_manage_bills_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


class InMemoryBills:
    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, BillId], ScheduledBill] = {}

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        return self.rows.get((user_id, bill_id))

    def list_by_user(self, user_id: UserId) -> list[ScheduledBill]:
        return [bill for (owner, _), bill in self.rows.items() if owner == user_id]

    def save(self, bill: ScheduledBill) -> None:
        self.rows[(bill.user_id, bill.id)] = bill

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        return self.rows.pop((user_id, bill_id), None) is not None


class InMemoryAccounts:
    def __init__(self) -> None:
        self.rows: dict[AccountId, Account] = {}

    def keep(self, account: Account) -> Account:
        """Not part of the port — how a test puts an account in place."""
        self.rows[account.id] = account

        return account

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.rows.get(account_id)

        return account if account and account.user_id == user_id else None

    def list_by_user(self, user_id: UserId) -> list[Account]:
        return [a for a in self.rows.values() if a.user_id == user_id]


@pytest.fixture
def wired() -> tuple[TestClient, InMemoryBills, InMemoryAccounts]:
    bills = InMemoryBills()
    accounts = InMemoryAccounts()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_manage_bills_use_case] = lambda: ManageBillsUseCase(
        bills=bills,
        accounts=accounts,
    )
    app.dependency_overrides[get_list_bills_use_case] = lambda: ListBillsUseCase(
        bills=bills,
        accounts=accounts,
    )

    return TestClient(app), bills, accounts


@pytest.fixture
def client(wired: tuple[TestClient, InMemoryBills, InMemoryAccounts]) -> TestClient:
    return wired[0]


def _declare(client: TestClient, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    payload = {
        "name": "Gimnasio",
        "amount": "120000",
        "currency": "COP",
        "cadence": "monthly",
        "starts_on": "2026-09-04",
    } | overrides
    response = client.post("/financial/bills", json=payload)

    assert response.status_code == 201, response.text

    return response.json()


def _account(accounts: InMemoryAccounts, *, closed: bool = False) -> Account:
    account = Account.open(
        user_id=USER_ID,
        name="Ahorros",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.now(),
    )

    if closed:
        account.close(PosixTime.now())

    return accounts.keep(account)


# ----------------------------------------------------------------------
# Declaring
# ----------------------------------------------------------------------


def test_a_declared_bill_comes_back_with_its_id(client: TestClient) -> None:
    body = _declare(client)

    assert body["name"] == "Gimnasio"
    assert body["amount"] == "120000"
    assert body["status"] == "active"
    assert body["frozen"] is False


def test_money_leaves_as_a_string(client: TestClient) -> None:
    """Like everywhere else money crosses a boundary here: a JSON float loses
    cents without ever raising."""
    body = _declare(client, amount="120000.50")

    assert body["amount"] == "120000.50"


def test_a_bill_for_nothing_is_refused(client: TestClient) -> None:
    assert (
        client.post(
            "/financial/bills",
            json={
                "name": "Gimnasio",
                "amount": "0",
                "currency": "COP",
                "cadence": "monthly",
                "starts_on": "2026-09-04",
            },
        ).status_code
        == 422
    )


def test_a_cadence_nobody_defined_is_refused(client: TestClient) -> None:
    assert (
        client.post(
            "/financial/bills",
            json={
                "name": "Gimnasio",
                "amount": "120000",
                "currency": "COP",
                "cadence": "fortnightly-ish",
                "starts_on": "2026-09-04",
            },
        ).status_code
        == 422
    )


def test_an_account_the_caller_does_not_own_is_missing_not_forbidden(
    client: TestClient,
) -> None:
    response = client.post(
        "/financial/bills",
        json={
            "name": "Gimnasio",
            "amount": "120000",
            "currency": "COP",
            "cadence": "monthly",
            "starts_on": "2026-09-04",
            "account_id": str(AccountId.new().value),
        },
    )

    assert response.status_code == 404


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------


def test_the_listing_defaults_to_this_month(client: TestClient) -> None:
    body = client.get("/financial/bills").json()

    assert dt.date.fromisoformat(body["since"]).day == 1
    assert body["bills"] == []
    assert body["totals"] == []


def test_the_listing_carries_both_totals(client: TestClient) -> None:
    _declare(client, name="Arriendo", amount="900000", starts_on="2026-09-01")

    body = client.get(
        "/financial/bills",
        params={"since": "2026-09-01", "until": "2026-09-30"},
    ).json()
    [total] = body["totals"]

    assert total["currency"] == "COP"
    assert total["expected"] == "900000"
    assert "upcoming" in total


def test_half_a_window_is_a_bad_request(client: TestClient) -> None:
    response = client.get("/financial/bills", params={"since": "2026-09-01"})

    assert response.status_code == 400
    assert "both ends" in response.json()["detail"]


def test_an_unknown_timezone_is_refused_rather_than_silently_utc(
    client: TestClient,
) -> None:
    assert (
        client.get("/financial/bills", params={"timezone": "Mars/Olympus"}).status_code
        == 400
    )


def test_a_bill_whose_account_is_closed_reads_as_frozen(
    wired: tuple[TestClient, InMemoryBills, InMemoryAccounts],
) -> None:
    client, _, accounts = wired
    account = _account(accounts, closed=True)
    _declare(client, account_id=str(account.id.value))

    [bill] = client.get("/financial/bills").json()["bills"]

    assert bill["frozen"] is True


def test_reopening_the_account_unfreezes_the_bill(
    wired: tuple[TestClient, InMemoryBills, InMemoryAccounts],
) -> None:
    """Nothing had to remember to undo it: frozen is read off the account."""
    client, _, accounts = wired
    account = _account(accounts, closed=True)
    _declare(client, account_id=str(account.id.value))

    account.reopen()

    [bill] = client.get("/financial/bills").json()["bills"]

    assert bill["frozen"] is False


# ----------------------------------------------------------------------
# Correcting, pausing, forgetting
# ----------------------------------------------------------------------


def test_the_price_can_be_corrected(client: TestClient) -> None:
    bill = _declare(client)

    response = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"amount": "135000", "currency": "COP"},
    )

    assert response.status_code == 200
    assert response.json()["amount"] == "135000"


def test_changing_the_amount_without_its_currency_is_refused(
    client: TestClient,
) -> None:
    bill = _declare(client)

    assert (
        client.patch(
            f"/financial/bills/{bill['id']}", json={"amount": "135000"}
        ).status_code
        == 422
    )


def test_a_patch_that_changes_nothing_is_refused(client: TestClient) -> None:
    bill = _declare(client)

    assert client.patch(f"/financial/bills/{bill['id']}", json={}).status_code == 422


def test_setting_and_clearing_the_same_field_at_once_is_refused(
    wired: tuple[TestClient, InMemoryBills, InMemoryAccounts],
) -> None:
    client, _, accounts = wired
    account = _account(accounts)
    bill = _declare(client)

    response = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"account_id": str(account.id.value), "clear_account": True},
    )

    assert response.status_code == 422


def test_an_account_can_be_taken_off_a_bill(
    wired: tuple[TestClient, InMemoryBills, InMemoryAccounts],
) -> None:
    client, _, accounts = wired
    account = _account(accounts)
    bill = _declare(client, account_id=str(account.id.value))

    body = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"clear_account": True},
    ).json()

    assert body["account_id"] is None


def test_pausing_stops_the_predictions_and_resuming_brings_them_back(
    client: TestClient,
) -> None:
    bill = _declare(client)

    assert client.post(f"/financial/bills/{bill['id']}/pause").status_code == 200
    assert client.get("/financial/bills").json()["occurrences"] == []

    assert client.post(f"/financial/bills/{bill['id']}/resume").status_code == 200
    assert client.get("/financial/bills").json()["bills"][0]["status"] == "active"


def test_a_forgotten_bill_is_gone(client: TestClient) -> None:
    bill = _declare(client)

    assert client.delete(f"/financial/bills/{bill['id']}").status_code == 204
    assert client.get("/financial/bills").json()["bills"] == []


def test_forgetting_the_same_bill_twice_is_a_404(client: TestClient) -> None:
    bill = _declare(client)
    client.delete(f"/financial/bills/{bill['id']}")

    assert client.delete(f"/financial/bills/{bill['id']}").status_code == 404


def test_an_id_that_is_not_one_reads_as_missing(client: TestClient) -> None:
    """Never 400: whether a bill exists is not something this tells whoever
    typed something into the path."""
    assert client.delete("/financial/bills/not-a-uuid").status_code == 404


def test_declaring_on_a_closed_account_answers_frozen_immediately(
    wired: tuple[TestClient, InMemoryBills, InMemoryAccounts],
) -> None:
    """The write used to answer `frozen: false` and the listing rendered right
    after it answered `true`."""
    client, _, accounts = wired
    account = _account(accounts, closed=True)

    body = _declare(client, account_id=str(account.id.value))

    assert body["frozen"] is True


def test_a_write_says_when_the_next_charge_lands(client: TestClient) -> None:
    body = _declare(client, starts_on="2020-01-04")

    assert body["next_occurrence"] is not None
    assert body["next_occurrence"]["due_on"] >= dt.date.today().isoformat()


def test_the_direction_can_be_corrected_through_the_api(client: TestClient) -> None:
    bill = _declare(client)

    body = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"direction": "incoming"},
    ).json()

    assert body["direction"] == "incoming"


def test_a_name_longer_than_the_ledger_takes_is_refused(client: TestClient) -> None:
    response = client.post(
        "/financial/bills",
        json={
            "name": "G" * 200,
            "amount": "120000",
            "currency": "COP",
            "cadence": "monthly",
            "starts_on": "2026-09-04",
        },
    )

    assert response.status_code == 422
