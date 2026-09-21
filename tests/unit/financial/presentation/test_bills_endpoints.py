"""The bills surface a frontend calls, over fakes.

Its own file rather than more rows in `test_financial_endpoints.py`: declaring
a bill touches neither the ledger nor a balance, and that is the property most
worth being able to see at a glance.

**Confirming one does**, and the ledger is wired in here for exactly that — so
the file can also show the other half: that money moves only through the pay
endpoint, and that pressing it twice moves it once.
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

from personal_finance.contexts.financial.application.autopay import (
    SettleDueChargesUseCase,
)
from personal_finance.contexts.financial.application.bills import (
    ListBillsUseCase,
    ManageBillsUseCase,
    SettleBillChargeUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.domain.bills import BillId, ScheduledBill
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_list_bills_use_case,
    get_manage_bills_use_case,
    get_merchant_directory,
    get_settle_charge_use_case,
    get_settle_due_charges_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.entities import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
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

    # The rest of `AccountRepository`, because confirming a charge goes through
    # the very use case that records a movement entered by hand.

    def save(self, account: Account) -> None:
        self.rows[account.id] = account

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        for account in self.list_by_user(user_id):
            if fingerprint in account.fingerprints:
                return account

        return None

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        del fingerprint
        self.save(account)

    def add(self, account: Account) -> bool:
        self.save(account)

        return True


class InMemoryLedger:
    """Enough of the ledger for a confirmation to land in it.

    `record` refuses a key it already holds, which is the only behaviour that
    matters here: it is what makes confirming the same period twice write one
    row, with no counting anywhere.
    """

    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

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
        del reversals

        for transaction in transactions:
            self.rows.pop(transaction.id.value, None)

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return row if row is not None and row.user_id == user_id else None

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        return {
            movement_id: row
            for movement_id in movement_ids
            if (row := self.rows.get(movement_id)) is not None
            and row.user_id == user_id
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


class NullPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


class RecordingMerchants:
    """Only what a confirmed charge asks of Merchant: file this name here."""

    def __init__(self) -> None:
        self.filed: list[tuple[str, str]] = []

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id, counterparties

        return {}

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return frozenset({"health", "housing"})

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        del user_id, occurred_at
        self.filed.append((counterparty, category))

        return None


@dataclasses.dataclass(slots=True)
class Wiring:
    client: TestClient
    bills: InMemoryBills
    accounts: InMemoryAccounts
    ledger: InMemoryLedger
    merchants: RecordingMerchants


@pytest.fixture
def wired() -> Wiring:
    bills = InMemoryBills()
    accounts = InMemoryAccounts()
    ledger = InMemoryLedger()
    merchants = RecordingMerchants()
    transactions = ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullPublisher(),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_merchant_directory] = lambda: merchants
    app.dependency_overrides[get_manage_bills_use_case] = lambda: ManageBillsUseCase(
        bills=bills,
        accounts=accounts,
        charges=ledger,
    )
    app.dependency_overrides[get_list_bills_use_case] = lambda: ListBillsUseCase(
        bills=bills,
        accounts=accounts,
        charges=ledger,
    )
    settle = SettleBillChargeUseCase(
        bills=bills,
        accounts=accounts,
        charges=ledger,
        transactions=transactions,
    )
    app.dependency_overrides[get_settle_charge_use_case] = lambda: settle
    app.dependency_overrides[get_settle_due_charges_use_case] = lambda: (
        SettleDueChargesUseCase(
            bills=bills,
            charges=ledger,
            ledger=ledger,
            settle=settle,
            merchants=merchants,
        )
    )

    return Wiring(
        client=TestClient(app),
        bills=bills,
        accounts=accounts,
        ledger=ledger,
        merchants=merchants,
    )


@pytest.fixture
def client(wired: Wiring) -> TestClient:
    return wired.client


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
    assert total["outstanding"] == "900000"


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
    wired: Wiring,
) -> None:
    client, accounts = wired.client, wired.accounts
    account = _account(accounts, closed=True)
    _declare(client, account_id=str(account.id.value))

    [bill] = client.get("/financial/bills").json()["bills"]

    assert bill["frozen"] is True


def test_reopening_the_account_unfreezes_the_bill(
    wired: Wiring,
) -> None:
    """Nothing had to remember to undo it: frozen is read off the account."""
    client, accounts = wired.client, wired.accounts
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
    wired: Wiring,
) -> None:
    client, accounts = wired.client, wired.accounts
    account = _account(accounts)
    bill = _declare(client)

    response = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"account_id": str(account.id.value), "clear_account": True},
    )

    assert response.status_code == 422


def test_an_account_can_be_taken_off_a_bill(
    wired: Wiring,
) -> None:
    client, accounts = wired.client, wired.accounts
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
    wired: Wiring,
) -> None:
    """The write used to answer `frozen: false` and the listing rendered right
    after it answered `true`."""
    client, accounts = wired.client, wired.accounts
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


# ----------------------------------------------------------------------
# Answering for a charge — the half that moves money
# ----------------------------------------------------------------------


def _charge_url(bill: dict[str, Any], period: str, what: str = "pay") -> str:
    return f"/financial/bills/{bill['id']}/occurrences/{period}/{what}"


def test_confirming_a_charge_writes_one_movement(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    body = client.post(_charge_url(bill, "2026-09-04"), json={}).json()

    assert body["occurrence"]["state"] == "paid"
    assert body["occurrence"]["movement_id"] is not None
    [movement] = ledger.rows.values()
    assert movement.counterparty == "Gimnasio"
    assert movement.origin.value == "scheduled"


def test_confirming_the_same_charge_twice_moves_the_money_once(
    wired: Wiring,
) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    first = client.post(_charge_url(bill, "2026-09-04"), json={})
    second = client.post(_charge_url(bill, "2026-09-04"), json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(ledger.rows) == 1
    assert (
        first.json()["occurrence"]["movement_id"]
        == second.json()["occurrence"]["movement_id"]
    )


def test_the_period_in_the_path_is_what_decides_which_charge_is_paid(
    wired: Wiring,
) -> None:
    """Without it, "paid" in September and "paid" in October would be the same
    request."""
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    client.post(_charge_url(bill, "2026-09-04"), json={})
    client.post(_charge_url(bill, "2026-10-04"), json={})

    assert len(ledger.rows) == 2


def test_a_period_this_bill_is_not_charged_on_is_a_bad_request(
    wired: Wiring,
) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    response = client.post(_charge_url(bill, "2026-09-05"), json={})

    assert response.status_code == 400
    assert ledger.rows == {}


def test_a_period_that_is_not_a_date_never_reaches_the_ledger(
    wired: Wiring,
) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    response = client.post(_charge_url(bill, "manana"), json={})

    assert response.status_code == 422
    assert ledger.rows == {}


def test_a_stated_price_is_what_is_recorded(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, amount="120000", starts_on="2026-09-04")

    body = client.post(
        _charge_url(bill, "2026-09-04"),
        json={"amount": "130000", "currency": "COP"},
    ).json()

    [movement] = ledger.rows.values()
    assert movement.amount.amount == Decimal("130000")
    # The projection is kept beside it rather than overwritten: the gap is the
    # gym raising its price, and it is worth seeing.
    assert body["occurrence"]["amount"] == "120000"
    assert body["occurrence"]["settled_amount"] == "130000"


def test_a_price_without_its_currency_is_refused(wired: Wiring) -> None:
    client = wired.client
    bill = _declare(client, starts_on="2026-09-04")

    response = client.post(_charge_url(bill, "2026-09-04"), json={"amount": "130000"})

    assert response.status_code == 422


def test_the_default_day_stays_on_the_period_it_belongs_to(wired: Wiring) -> None:
    """Midnight would file a charge due on the 1st into the previous month for
    everybody west of Greenwich, this app's own users included."""
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-01")

    client.post(_charge_url(bill, "2026-09-01"), json={})

    [movement] = ledger.rows.values()
    moved = dt.datetime.fromtimestamp(
        movement.occurred_at.as_epoch_seconds(),
        tz=dt.timezone(dt.timedelta(hours=-5)),
    )
    assert moved.date() == dt.date(2026, 9, 1)


def test_a_confirmed_charge_is_filed_under_the_bills_category(
    wired: Wiring,
) -> None:
    """Without this a confirmed charge would sit outside every breakdown by
    category, which is most of what it was declared for."""
    client, merchants = wired.client, wired.merchants
    bill = _declare(client, starts_on="2026-09-04", category="health")

    client.post(_charge_url(bill, "2026-09-04"), json={})

    assert merchants.filed == [("Gimnasio", "health")]


def test_confirming_a_charge_on_a_closed_account_is_a_conflict(
    wired: Wiring,
) -> None:
    client, accounts, ledger = wired.client, wired.accounts, wired.ledger
    account = _account(accounts)
    bill = _declare(client, starts_on="2026-09-04", account_id=str(account.id.value))
    account.close(PosixTime.now())

    response = client.post(_charge_url(bill, "2026-09-04"), json={})

    assert response.status_code == 409
    assert ledger.rows == {}


def test_a_paused_bill_charges_nothing_through_the_api(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")
    client.post(f"/financial/bills/{bill['id']}/pause")

    response = client.post(_charge_url(bill, "2026-09-04"), json={})

    assert response.status_code == 400
    assert ledger.rows == {}


def test_undoing_a_confirmation_erases_the_movement(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")
    client.post(_charge_url(bill, "2026-09-04"), json={})

    body = client.delete(_charge_url(bill, "2026-09-04")).json()

    assert ledger.rows == {}
    assert body["occurrence"]["state"] != "paid"
    assert body["occurrence"]["movement_id"] is None


def test_undoing_a_confirmation_that_never_happened_is_not_a_404(
    wired: Wiring,
) -> None:
    """The undo behind a button somebody presses because they are unsure. A
    404 on the second press is a worse answer than nothing."""
    client = wired.client
    bill = _declare(client, starts_on="2026-09-04")

    response = client.delete(_charge_url(bill, "2026-09-04"))

    assert response.status_code == 200


def test_skipping_writes_nothing_to_the_ledger(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")

    body = client.post(_charge_url(bill, "2026-09-04", "skip")).json()

    assert body["occurrence"]["state"] == "skipped"
    assert ledger.rows == {}


def test_a_skipped_charge_is_in_neither_of_the_months_figures(
    wired: Wiring,
) -> None:
    client = wired.client
    bill = _declare(client, starts_on="2026-09-04")

    client.post(_charge_url(bill, "2026-09-04", "skip"))

    body = client.get(
        "/financial/bills",
        params={"since": "2026-09-01", "until": "2026-09-30"},
    ).json()
    assert body["totals"] == []


def test_a_paid_charge_leaves_outstanding_and_stays_in_expected(
    wired: Wiring,
) -> None:
    client = wired.client
    bill = _declare(client, amount="900000", starts_on="2026-09-04")

    client.post(_charge_url(bill, "2026-09-04"), json={})

    [total] = client.get(
        "/financial/bills",
        params={"since": "2026-09-01", "until": "2026-09-30"},
    ).json()["totals"]
    assert total["expected"] == "900000"
    assert total["outstanding"] == "0"


def test_a_paid_charge_cannot_be_skipped(wired: Wiring) -> None:
    client = wired.client
    bill = _declare(client, starts_on="2026-09-04")
    client.post(_charge_url(bill, "2026-09-04"), json={})

    response = client.post(_charge_url(bill, "2026-09-04", "skip"))

    assert response.status_code == 400
    assert "already paid" in response.json()["detail"]


def test_a_skip_can_be_taken_back(wired: Wiring) -> None:
    client = wired.client
    bill = _declare(client, starts_on="2026-09-04")
    client.post(_charge_url(bill, "2026-09-04", "skip"))

    body = client.delete(_charge_url(bill, "2026-09-04", "skip")).json()

    assert body["occurrence"]["state"] != "skipped"


def test_a_stranger_cannot_charge_somebody_elses_bill(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")
    stranger = UserId.from_string("99999999-9999-9999-9999-999999999999")
    client.app.dependency_overrides[get_current_user_id] = lambda: stranger  # type: ignore[attr-defined]

    response = client.post(_charge_url(bill, "2026-09-04"), json={})

    assert response.status_code == 404
    assert ledger.rows == {}


def test_a_bill_cannot_name_a_category_its_owner_does_not_have(
    wired: Wiring,
) -> None:
    """Not decoration: a confirmed charge is filed under it, and filing is an
    enrichment that may not fail a movement — so an unknown category would
    produce charges silently absent from every breakdown."""
    client = wired.client

    response = client.post(
        "/financial/bills",
        json={
            "name": "Gimnasio",
            "amount": "120000",
            "currency": "COP",
            "cadence": "monthly",
            "starts_on": "2026-09-04",
            "category": "gimnasio",
        },
    )

    assert response.status_code == 422
    assert client.get("/financial/bills").json()["bills"] == []


def test_correcting_a_bill_into_an_unknown_category_is_refused_too(
    wired: Wiring,
) -> None:
    client = wired.client
    bill = _declare(client, category="health")

    response = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"category": "gimnasio"},
    )

    assert response.status_code == 422


def test_a_charge_is_confirmed_in_the_bills_own_currency(wired: Wiring) -> None:
    """A rate is a fact about a moment nobody recorded here, so a figure in
    another currency is refused rather than converted."""
    client = wired.client
    bill = _declare(client, amount="14", currency="USD", starts_on="2026-09-04")

    refused = client.post(
        _charge_url(bill, "2026-09-04"),
        json={"amount": "14", "currency": "COP"},
    )
    accepted = client.post(
        _charge_url(bill, "2026-09-04"),
        json={"amount": "15", "currency": "USD"},
    )

    assert refused.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["occurrence"]["settled_amount"] == "15"


# ----------------------------------------------------------------------
# Charging itself, and the movement that answers a charge
# ----------------------------------------------------------------------


def _arm(client: TestClient, bill: dict[str, Any], *, enabled: bool = True) -> Any:  # noqa: ANN401
    return client.post(
        f"/financial/bills/{bill['id']}/autopay",
        json={"enabled": enabled, "timezone": "America/Bogota"},
    )


def _armed_before(wired: Wiring, bill: dict[str, Any], day: dt.date) -> None:
    """Arm the bill as if it had been armed before that charge fell due.

    Reaching into the repository on purpose: arming is never retroactive —
    the day it was turned on is the earliest charge it may reach — so no
    sequence of calls to the API can produce an automatic charge on the same
    day the switch is flipped. That rule is pinned in the use cases; what
    these cases are about is what the endpoint answers once it applies.
    """
    _arm(wired.client, bill)
    stored = wired.bills.rows[(USER_ID, BillId.from_string(bill["id"]))]
    stored.autopay_from = day


def _settle_due(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/financial/bills/settle",
        json={"timezone": "America/Bogota"},
    )

    assert response.status_code == 200, response.text

    return response.json()


def _entered(
    ledger: InMemoryLedger,
    *,
    day: dt.date,
    counterparty: str = "Gimnasio",
    amount: str = "120000",
) -> Transaction:
    """A movement the bank announced, put straight into the ledger."""
    movement = Transaction.enter_manually(
        user_id=USER_ID,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(
            int(dt.datetime.combine(day, dt.time(hour=12), tzinfo=dt.UTC).timestamp()),
        ),
        counterparty=counterparty,
    )
    ledger.rows[movement.id.value] = movement

    return movement


def test_a_bill_is_declared_without_charging_itself(wired: Wiring) -> None:
    """Off unless somebody turns it on, which is what makes every other rule
    here a refusal to guess rather than a hope."""
    bill = _declare(wired.client, starts_on="2026-09-04")

    assert bill["autopay"] is False
    assert bill["autopay_from"] is None


def test_arming_a_bill_answers_with_the_day_it_was_armed(wired: Wiring) -> None:
    bill = _declare(wired.client, starts_on="2026-09-04")

    body = _arm(wired.client, bill).json()

    assert body["autopay"] is True
    assert body["autopay_from"] is not None

    disarmed = _arm(wired.client, bill, enabled=False).json()

    assert disarmed["autopay"] is False
    assert disarmed["autopay_from"] is None


def test_settling_charges_an_armed_bill_whose_window_has_closed(
    wired: Wiring,
) -> None:
    client, ledger = wired.client, wired.ledger
    due = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=10)
    bill = _declare(client, starts_on=due.isoformat())
    _armed_before(wired, bill, due - dt.timedelta(days=1))

    body = _settle_due(client)

    assert [each["action"] for each in body["settled"]] == ["charged"]
    assert body["settled"][0]["occurrence"]["state"] == "paid"
    assert body["settled"][0]["occurrence"]["settled_by"] == "confirmed"
    assert len(ledger.rows) == 1


def test_an_automatic_charge_is_filed_under_the_bills_category(
    wired: Wiring,
) -> None:
    """The same enrichment the button already does. Without it the charge
    sits outside every breakdown, which is most of what it was declared
    for."""
    client = wired.client
    due = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=10)
    bill = _declare(client, starts_on=due.isoformat(), category="health")
    _armed_before(wired, bill, due - dt.timedelta(days=1))

    _settle_due(client)

    assert wired.merchants.filed == [("Gimnasio", "health")]


def test_settling_answers_a_charge_with_a_movement_instead_of_writing_one(
    wired: Wiring,
) -> None:
    client, ledger = wired.client, wired.ledger
    due = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=10)
    bill = _declare(client, starts_on=due.isoformat())
    _armed_before(wired, bill, due - dt.timedelta(days=1))
    paid = _entered(ledger, day=due)

    body = _settle_due(client)

    assert [each["action"] for each in body["settled"]] == ["matched"]
    assert body["settled"][0]["occurrence"]["movement_id"] == paid.id.value
    assert body["settled"][0]["occurrence"]["settled_by"] == "matched"
    assert len(ledger.rows) == 1


def test_an_unclear_match_comes_back_as_a_proposal(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    due = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=10)
    bill = _declare(client, starts_on=due.isoformat())
    _armed_before(wired, bill, due - dt.timedelta(days=1))
    _entered(ledger, day=due, amount="260000")

    body = _settle_due(client)

    assert body["settled"] == []
    assert body["proposals"][0]["bill_name"] == "Gimnasio"
    assert body["proposals"][0]["candidates"][0]["quality"] == "likely"
    assert len(ledger.rows) == 1


def test_a_movement_can_be_linked_to_a_charge_by_hand(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")
    paid = _entered(ledger, day=dt.date(2026, 9, 6), counterparty="PAGO PSE GYMSA")

    body = client.post(
        _charge_url(bill, "2026-09-04", "link"),
        json={"movement_id": paid.id.value},
    )

    assert body.status_code == 200, body.text
    assert body.json()["occurrence"]["state"] == "paid"
    assert body.json()["occurrence"]["settled_by"] == "matched"
    assert len(ledger.rows) == 1


def test_unlinking_leaves_the_movement_where_it_is(wired: Wiring) -> None:
    client, ledger = wired.client, wired.ledger
    bill = _declare(client, starts_on="2026-09-04")
    paid = _entered(ledger, day=dt.date(2026, 9, 4))
    client.post(
        _charge_url(bill, "2026-09-04", "link"),
        json={"movement_id": paid.id.value},
    )

    body = client.delete(_charge_url(bill, "2026-09-04", "link"))

    assert body.status_code == 200, body.text
    assert body.json()["occurrence"]["state"] != "paid"
    assert paid.id.value in ledger.rows


def test_linking_a_movement_that_is_not_there_reads_as_missing(
    wired: Wiring,
) -> None:
    bill = _declare(wired.client, starts_on="2026-09-04")

    response = wired.client.post(
        _charge_url(bill, "2026-09-04", "link"),
        json={"movement_id": "nothing"},
    )

    assert response.status_code == 404


def test_arming_today_leaves_a_charge_that_already_fell_due_alone(
    wired: Wiring,
) -> None:
    """The switch is not retroactive. That charge has been on screen as
    overdue for a week and may already have been paid where this app cannot
    see it; taking the money now would be the app inventing an expense."""
    client = wired.client
    due = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=10)
    bill = _declare(client, starts_on=due.isoformat())
    _arm(client, bill)

    body = _settle_due(client)

    assert body["settled"] == []
    assert wired.ledger.rows == {}


def test_a_bill_cannot_be_for_a_figure_the_table_cannot_hold(
    client: TestClient,
) -> None:
    """The same ceiling the rest of the router keeps, on the three money
    fields under `/bills`. Unbounded, confirming such a charge writes a
    movement whose magnitude DynamoDB refuses — a 500 where a 422 belongs."""
    absurd = "1e400"
    declared = client.post(
        "/financial/bills",
        json={
            "name": "Gimnasio",
            "amount": absurd,
            "currency": "COP",
            "cadence": "monthly",
            "starts_on": "2026-09-04",
        },
    )
    bill = _declare(client, starts_on="2026-09-04")
    amended = client.patch(
        f"/financial/bills/{bill['id']}",
        json={"amount": absurd, "currency": "COP"},
    )
    confirmed = client.post(
        _charge_url(bill, "2026-09-04"),
        json={"amount": absurd, "currency": "COP"},
    )

    assert declared.status_code == 422
    assert amended.status_code == 422
    assert confirmed.status_code == 422
