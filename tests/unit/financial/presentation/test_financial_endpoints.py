"""The surface a frontend calls, exercised end to end over fakes."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.financing import (
    AccrueFinancingUseCase,
    ManageFinancingUseCase,
    ReadFinancingUseCase,
    RevalueAccountUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.queries import (
    GetAccountUseCase,
    GetTransactionUseCase,
    ListAccountsUseCase,
    ListTransactionsUseCase,
    ReadFinancialHistoryUseCase,
    ReadSpendingTrendUseCase,
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    InstrumentKind,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_account_use_case,
    get_accrue_financing_use_case,
    get_list_accounts_use_case,
    get_list_transactions_use_case,
    get_manage_accounts_use_case,
    get_manage_financing_use_case,
    get_manage_transactions_use_case,
    get_merchant_directory,
    get_read_financing_use_case,
    get_read_history_use_case,
    get_read_trend_use_case,
    get_revalue_account_use_case,
    get_summarize_spending_use_case,
    get_transaction_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
WHEN = 1_787_500_000


class InMemoryAccounts:
    def __init__(self) -> None:
        self.by_id: dict[tuple[UserId, AccountId], Account] = {}
        self.pointers: dict[tuple[UserId, str], AccountId] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return self.by_id.get((user_id, account_id))

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        account_id = self.pointers.get((user_id, fingerprint.value))

        return None if account_id is None else self.by_id.get((user_id, account_id))

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for (owner, _), account in self.by_id.items() if owner == user_id
        ]

    def add(self, account: Account) -> bool:
        taken = any(
            (account.user_id, print_.value) in self.pointers
            for print_ in account.fingerprints
        )

        if taken:
            return False

        self.save(account)

        return True

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        self.pointers.pop((account.user_id, fingerprint.value), None)
        self.save(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)

    def save(self, account: Account) -> None:
        self.by_id[(account.user_id, account.id)] = account

        for print_ in account.fingerprints:
            self.pointers[(account.user_id, print_.value)] = account.id


class InMemoryLedger:
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


ARA = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Ara",
    category="groceries",
    needs_review=False,
)


class FakeDirectory:
    """Stands in for merchant, answering by exact counterparty text."""

    def __init__(self) -> None:
        # What `classify` was asked to file, so a test can assert that
        # entering a movement with a category reached the other context.
        self.classified: list[tuple[str, str, PosixTime]] = []

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            counterparty: ARA
            for counterparty in counterparties
            if counterparty == "TIENDAS ARA"
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return frozenset(
            {
                "groceries",
                "transport",
                "subscriptions",
                "uncategorized",
                "custom:gatos",
            },
        )

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        del user_id
        self.classified.append((counterparty, category, occurred_at))

        if not counterparty.strip(" -"):
            # Text no fingerprint can be built from: there is no merchant to
            # make of it, and the movement is a movement either way.
            return None

        return MerchantAttribution(
            merchant_id="aaaaaaaa-0000-0000-0000-00000000000c",
            display_name=counterparty.title(),
            category=category,
            needs_review=False,
        )


@pytest.fixture
def client() -> TestClient:
    app, _, _ = _build()

    return app


@pytest.fixture
def wired() -> tuple[TestClient, InMemoryLedger]:
    """The same app, plus the ledger behind it.

    A transfer whose two sides are both known reaches the ledger from the bus,
    and no endpoint writes that pair (`POST /transactions/transfer` writes a
    lone leg, never two), so a test about how the API *reads* them puts the
    pair in place itself.
    """
    app, ledger, _ = _build()

    return app, ledger


@pytest.fixture
def attributing() -> tuple[TestClient, FakeDirectory]:
    """The same app, plus the merchant directory standing in for the other
    context, so a test can see what was asked of it.
    """
    app, _, directory = _build()

    return app, directory


def _build() -> tuple[TestClient, InMemoryLedger, FakeDirectory]:
    accounts = InMemoryAccounts()
    ledger = InMemoryLedger()
    publisher = NullPublisher()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_manage_accounts_use_case] = lambda: (
        ManageAccountsUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_manage_transactions_use_case] = lambda: (
        ManageTransactionsUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_list_accounts_use_case] = lambda: ListAccountsUseCase(
        accounts=accounts,
    )
    app.dependency_overrides[get_account_use_case] = lambda: GetAccountUseCase(
        accounts=accounts,
    )
    directory = FakeDirectory()
    app.dependency_overrides[get_list_transactions_use_case] = lambda: (
        ListTransactionsUseCase(ledger=ledger, merchants=directory)
    )
    app.dependency_overrides[get_transaction_use_case] = lambda: GetTransactionUseCase(
        ledger=ledger,
        merchants=directory,
    )
    app.dependency_overrides[get_merchant_directory] = lambda: directory
    app.dependency_overrides[get_summarize_spending_use_case] = lambda: (
        SummarizeSpendingUseCase(
            ledger=ledger,
            accounts=accounts,
            merchants=directory,
        )
    )
    app.dependency_overrides[get_read_history_use_case] = lambda: (
        ReadFinancialHistoryUseCase(ledger=ledger, accounts=accounts)
    )
    app.dependency_overrides[get_read_trend_use_case] = lambda: (
        ReadSpendingTrendUseCase(
            ledger=ledger,
            accounts=accounts,
            merchants=directory,
        )
    )
    app.dependency_overrides[get_manage_financing_use_case] = lambda: (
        ManageFinancingUseCase(accounts=accounts, event_publisher=publisher)
    )
    app.dependency_overrides[get_accrue_financing_use_case] = lambda: (
        AccrueFinancingUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_revalue_account_use_case] = lambda: (
        RevalueAccountUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_read_financing_use_case] = lambda: (
        ReadFinancingUseCase(accounts=accounts, ledger=ledger)
    )

    return TestClient(app), ledger, directory


def _declare(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Tarjeta Bancolombia",
        "kind": "credit_card",
        "currency": "COP",
        "bank": "Bancolombia",
        "instrument_kind": "credit_card",
        "last_four": "7653",
    }
    payload.update(overrides)
    response = client.post("/financial/accounts", json=payload)

    assert response.status_code == 201, response.text

    return response.json()


def _enter(client: TestClient, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "direction": "outgoing",
        "amount": "50000",
        "currency": "COP",
        "occurred_at": WHEN,
        "counterparty": "TIENDAS ARA",
    }
    payload.update(overrides)
    response = client.post("/financial/transactions", json=payload)

    assert response.status_code == 201, response.text

    return response.json()


def test_somebody_who_declared_nothing_gets_an_empty_but_valid_answer(
    client: TestClient,
) -> None:
    # Finflow with no accounts is a complete answer, not a broken one.
    response = client.get("/financial/accounts")

    assert response.status_code == 200
    assert response.json() == {"accounts": [], "net_worth": []}


def test_declaring_an_account_returns_it_with_the_key_it_answers_to(
    client: TestClient,
) -> None:
    account = _declare(client)

    assert account["name"] == "Tarjeta Bancolombia"
    assert account["category"] == "liability"
    assert account["balance"] == "0"
    assert account["instruments"]


def test_an_account_without_an_instrument_is_perfectly_valid(
    client: TestClient,
) -> None:
    # Cash in a drawer. It simply never matches an alert.
    account = _declare(
        client,
        name="Efectivo",
        kind="cash",
        bank=None,
        instrument_kind=None,
        last_four=None,
    )

    assert account["instruments"] == []


def test_an_account_can_name_its_bank_without_naming_a_card(
    client: TestClient,
) -> None:
    # A mortgage names the bank that holds it and has no card at all.
    response = client.post(
        "/financial/accounts",
        json={
            "name": "Hipoteca",
            "kind": "mortgage",
            "currency": "COP",
            "bank": "Bancolombia",
            "opening_balance": "180000000",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["bank"] == "bancolombia"
    assert response.json()["instruments"] == []
    assert response.json()["balance"] == "180000000"


def test_half_an_instrument_is_refused(client: TestClient) -> None:
    # Matching on the kind alone would merge every savings account somebody
    # holds at one bank into a single wrong balance.
    response = client.post(
        "/financial/accounts",
        json={
            "name": "Incompleta",
            "kind": "savings",
            "currency": "COP",
            "bank": "Bancolombia",
            "instrument_kind": "savings_account",
        },
    )

    assert response.status_code == 422


def test_a_movement_entered_by_hand_can_name_its_own_category(
    attributing: tuple[TestClient, FakeDirectory],
) -> None:
    """Otherwise it has no merchant at all, and no breakdown by category ever
    counts it: the automatic path only learns a name from a bank email.
    """
    client, directory = attributing

    entered = _enter(client, counterparty="Panaderia la esquina", category="groceries")

    assert directory.classified == [
        # The movement's own time, not the moment of the call: a merchant this
        # creates was first seen when the spending happened.
        ("Panaderia la esquina", "groceries", PosixTime.from_epoch_seconds(WHEN)),
    ]
    assert entered["merchant"] == {
        "id": "aaaaaaaa-0000-0000-0000-00000000000c",
        "display_name": "Panaderia La Esquina",
        "category": "groceries",
        "needs_review": False,
    }


def test_a_movement_can_name_a_category_its_owner_wrote(
    attributing: tuple[TestClient, FakeDirectory],
) -> None:
    client, directory = attributing

    entered = _enter(client, counterparty="Veterinaria", category="custom:gatos")

    assert directory.classified == [
        ("Veterinaria", "custom:gatos", PosixTime.from_epoch_seconds(WHEN)),
    ]
    assert entered["merchant"] == {
        "id": "aaaaaaaa-0000-0000-0000-00000000000c",
        "display_name": "Veterinaria",
        "category": "custom:gatos",
        "needs_review": False,
    }


def test_a_movement_with_no_category_files_nothing(
    attributing: tuple[TestClient, FakeDirectory],
) -> None:
    # The field is optional, and omitting it leaves the behaviour that was
    # there before: nothing is created, and the merchant shows only if some
    # bank email already taught the system that name.
    client, directory = attributing

    _enter(client, counterparty="Panaderia la esquina")

    assert directory.classified == []


def test_a_merchant_that_cannot_be_filed_never_costs_the_movement(
    attributing: tuple[TestClient, FakeDirectory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The money is already written by the time the merchant is named, so
    anything raised past that point would report a failure for a movement that
    exists — and somebody reading that 500 enters it a second time.
    """
    client, directory = attributing

    def unavailable(**_: object) -> None:
        raise RuntimeError("merchant table unreachable")

    monkeypatch.setattr(directory, "classify", unavailable)

    entered = _enter(client, counterparty="Panaderia la esquina", category="groceries")

    assert entered["amount"] == "50000"
    # The enrichment is what was lost, and only that.
    assert entered["merchant"] is None
    assert client.get("/financial/transactions").json()["total"] == 1


def test_a_category_nobody_has_is_refused_before_the_money_is_written(
    attributing: tuple[TestClient, FakeDirectory],
) -> None:
    """A 422 that has already recorded money is a movement the user has to go
    and find.
    """
    client, directory = attributing

    response = client.post(
        "/financial/transactions",
        json={
            "direction": "outgoing",
            "amount": "50000",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "Panaderia la esquina",
            "category": "custom:mascotas",
        },
    )

    assert response.status_code == 422
    assert directory.classified == []
    assert client.get("/financial/transactions").json()["total"] == 0


def test_a_closed_account_takes_no_manual_movements(client: TestClient) -> None:
    """A closed account keeps its history and stops taking movements.

    The alert path files a late movement unassigned; a manual entry is a
    request somebody just made, so it is refused to their face instead.
    """
    account = _declare(client)
    client.post(f"/financial/accounts/{account['id']}/close")

    response = client.post(
        "/financial/transactions",
        json={
            "direction": "outgoing",
            "amount": "50000",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "TIENDAS ARA",
            "account_id": account["id"],
        },
    )

    assert response.status_code == 409
    assert (
        client.get("/financial/accounts?scope=all").json()["accounts"][0]["balance"]
        == "0"
    )


def test_a_currency_change_is_refused_before_it_reaches_the_ledger(
    client: TestClient,
) -> None:
    """Refused whole, not half-written.

    Saving the row first and discovering afterwards would leave a movement
    the holding account can never take, and every later replay of that
    account would fail on it.
    """
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])

    refused = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"amount": "10", "currency": "USD"},
    )

    assert refused.status_code == 400

    # The movement and the balance are both untouched.
    assert client.get(f"/financial/transactions/{movement['id']}").json()["amount"] == (
        "50000"
    )
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == "50000"
    # And the account still works.
    assert (
        client.patch(
            f"/financial/accounts/{account['id']}",
            json={"name": "Sigue viva"},
        ).status_code
        == 200
    )


def test_a_note_can_be_written_and_cleared(client: TestClient) -> None:
    movement = _enter(client, note="cobro automatico")

    assert movement["note"] == "cobro automatico"

    cleared = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"note": ""},
    )

    assert cleared.status_code == 200
    assert cleared.json()["note"] is None


def test_annotating_a_movement_does_not_claim_the_bank_said_otherwise(
    client: TestClient,
) -> None:
    # `stated` means "what the bank said before somebody corrected it". A note
    # corrects nothing, so writing one must not fabricate that record.
    movement = _enter(client)
    annotated = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"note": "mercado del mes"},
    )

    assert annotated.status_code == 200
    assert annotated.json()["stated"] is None


def test_moving_a_movement_to_the_account_it_is_already_on_changes_nothing(
    client: TestClient,
) -> None:
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])

    again = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"account_id": account["id"]},
    )

    assert again.status_code == 200
    assert again.json()["account_id"] == account["id"]
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == "50000"


def test_two_accounts_cannot_answer_to_one_card(client: TestClient) -> None:
    _declare(client)
    response = client.post(
        "/financial/accounts",
        json={
            "name": "Otra",
            "kind": "credit_card",
            "currency": "COP",
            "bank": "Bancolombia",
            "instrument_kind": "credit_card",
            "last_four": "7653",
        },
    )

    assert response.status_code == 409


def test_a_manual_movement_lands_on_the_account_it_names(client: TestClient) -> None:
    account = _declare(client)
    movement = _enter(client, counterparty="NETFLIX", account_id=account["id"])

    assert movement["origin"] == "manual"
    assert movement["status"] == "assigned"

    listed = client.get("/financial/accounts").json()

    assert listed["accounts"][0]["balance"] == "50000"
    assert listed["net_worth"] == [
        {
            "currency": "COP",
            "assets": "0",
            "liabilities": "50000",
            "total": "-50000",
        },
    ]


def test_a_manual_movement_needs_no_account(client: TestClient) -> None:
    movement = _enter(client, counterparty="ALMUERZO", amount="12000")

    assert movement["status"] == "unassigned"
    assert movement["account_id"] is None


def test_a_movement_cannot_go_on_an_account_in_another_currency(
    client: TestClient,
) -> None:
    account = _declare(client)
    response = client.post(
        "/financial/transactions",
        json={
            "direction": "outgoing",
            "amount": "12",
            "currency": "USD",
            "occurred_at": WHEN,
            "counterparty": "AMAZON",
            "account_id": account["id"],
        },
    )

    assert response.status_code == 400


def test_editing_a_movement_moves_the_balance_with_it(client: TestClient) -> None:
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])

    edited = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"amount": "80000", "currency": "COP"},
    )

    assert edited.status_code == 200
    assert edited.json()["amount"] == "80000"
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == "80000"


def test_detaching_a_movement_takes_it_off_the_balance(client: TestClient) -> None:
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])

    detached = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"detach": True},
    )

    assert detached.status_code == 200
    assert detached.json()["status"] == "unassigned"
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == "0"


# ------------------------------------------------ borrar un movimiento


def test_deleting_a_movement_gives_the_account_its_money_back(
    client: TestClient,
) -> None:
    """Two thousand spent at a restaurant, erased, is two thousand the account
    holds again. The case the endpoint exists for.
    """
    account = _declare(
        client,
        name="Ahorros",
        kind="savings",
        instrument_kind="account",
        last_four="7111",
        opening_balance="1000000",
    )
    purchase = _enter(
        client,
        account_id=account["id"],
        amount="2000",
        counterparty="RESTAURANTE EL LAGO",
    )

    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == (
        "998000"
    )

    response = client.delete(f"/financial/transactions/{purchase['id']}")

    assert response.status_code == 200, response.text
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == (
        "1000000"
    )


def test_the_answer_names_what_was_erased_and_the_balance_now(
    client: TestClient,
) -> None:
    """So a screen redraws the balance from this answer instead of asking
    again — and knows which rows to take off the list.
    """
    account = _declare(
        client,
        name="Ahorros",
        kind="savings",
        instrument_kind="account",
        last_four="7111",
        opening_balance="1000000",
    )
    purchase = _enter(client, account_id=account["id"], amount="2000")

    body = client.delete(f"/financial/transactions/{purchase['id']}").json()

    assert body["erased"] == [purchase["id"]]
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["id"] == account["id"]
    assert body["accounts"][0]["balance"] == "1000000"


def test_a_deleted_movement_can_no_longer_be_read(client: TestClient) -> None:
    """Erased, not detached: nothing is left to ask for."""
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])

    client.delete(f"/financial/transactions/{movement['id']}")

    assert client.get(f"/financial/transactions/{movement['id']}").status_code == 404
    assert client.get("/financial/transactions").json()["total"] == 0


def test_deleting_a_movement_on_no_account_changes_no_balance(
    client: TestClient,
) -> None:
    """The ordinary state for somebody watching only what comes in and goes
    out: the movement is real, and there is no balance to give back to.
    """
    _declare(client)
    movement = _enter(client)

    body = client.delete(f"/financial/transactions/{movement['id']}").json()

    assert body["erased"] == [movement["id"]]
    assert body["accounts"] == []


def test_deleting_a_movement_that_is_not_there_is_reported_as_missing(
    client: TestClient,
) -> None:
    response = client.delete(
        "/financial/transactions/0198f4e4-0000-7000-8000-000000000000",
    )

    assert response.status_code == 404


def test_deleting_the_same_movement_twice_answers_404_the_second_time(
    client: TestClient,
) -> None:
    """And the balance moves once: the second attempt never reaches a replay."""
    account = _declare(
        client,
        name="Ahorros",
        kind="savings",
        instrument_kind="account",
        last_four="7111",
        opening_balance="1000000",
    )
    movement = _enter(client, account_id=account["id"], amount="2000")

    assert client.delete(f"/financial/transactions/{movement['id']}").status_code == 200
    assert client.delete(f"/financial/transactions/{movement['id']}").status_code == 404
    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == (
        "1000000"
    )


def test_an_edit_that_changes_nothing_is_refused(client: TestClient) -> None:
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])
    response = client.patch(f"/financial/transactions/{movement['id']}", json={})

    assert response.status_code == 422


def test_an_account_and_a_detach_at_once_are_refused(client: TestClient) -> None:
    account = _declare(client)
    movement = _enter(client, account_id=account["id"])
    response = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"detach": True, "account_id": account["id"]},
    )

    assert response.status_code == 422


def test_transactions_can_be_filtered_down_to_the_ones_waiting(
    client: TestClient,
) -> None:
    account = _declare(client)
    _enter(client, account_id=account["id"])
    _enter(client, counterparty="ALMUERZO", amount="12000")

    everything = client.get("/financial/transactions").json()
    waiting = client.get("/financial/transactions?unassigned=true").json()

    assert everything["total"] == 2
    assert waiting["total"] == 1
    assert waiting["transactions"][0]["counterparty"] == "ALMUERZO"


def test_history_answers_the_dashboard_in_one_call(client: TestClient) -> None:
    account = _declare(client)
    _enter(client, account_id=account["id"], amount="50000")

    history = client.get("/financial/history", params={"months": 3}).json()

    assert [point["key"] for point in history["months"]][-1] == history["comparison"][
        "key"
    ]
    assert len(history["months"]) == 3
    # Only the month being lived is partial.
    assert [point["partial"] for point in history["months"]] == [False, False, True]
    assert history["comparison"]["previous_key"] != history["comparison"]["key"]


def test_history_replays_a_balance_rather_than_reading_a_snapshot(
    client: TestClient,
) -> None:
    account = _declare(client)
    _enter(
        client,
        account_id=account["id"],
        direction="incoming",
        amount="80000",
        counterparty="NOMINA",
    )

    history = client.get("/financial/history", params={"months": 1}).json()
    current = history["months"][-1]["net_worth"]

    assert current[0]["total"] == "80000"


def test_history_refuses_a_span_it_will_not_serve(client: TestClient) -> None:
    assert client.get("/financial/history", params={"months": 0}).status_code == 422
    assert client.get("/financial/history", params={"months": 999}).status_code == 422


def test_history_refuses_a_timezone_it_cannot_read(client: TestClient) -> None:
    response = client.get("/financial/history", params={"timezone": "Mars/Olympus"})

    assert response.status_code == 400


def test_transactions_can_be_filtered_by_direction(client: TestClient) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="50000")
    _enter(client, direction="incoming", counterparty="PAGO NOMINA", amount="20000")

    incoming = client.get("/financial/transactions?direction=incoming").json()
    outgoing = client.get("/financial/transactions?direction=outgoing").json()

    assert incoming["total"] == 1
    assert incoming["transactions"][0]["counterparty"] == "PAGO NOMINA"
    assert outgoing["total"] == 1
    assert outgoing["transactions"][0]["counterparty"] == "TIENDAS ARA"


def test_the_summary_takes_direction_too(client: TestClient) -> None:
    # The list and the summary share one filter set so a bucket can be opened
    # as the movements behind it. A filter on only one of them breaks that.
    _enter(client, counterparty="TIENDAS ARA", amount="50000")
    _enter(client, direction="incoming", counterparty="PAGO NOMINA", amount="20000")

    summary = client.get(
        "/financial/summary",
        params={"direction": "incoming"},
    ).json()

    assert summary["totals"][0]["incoming"] == "20000"
    assert summary["totals"][0]["outgoing"] == "0"


def test_a_direction_that_is_not_one_is_refused(client: TestClient) -> None:
    response = client.get("/financial/transactions?direction=sideways")

    assert response.status_code == 422


def test_transactions_can_be_searched_by_what_the_bank_wrote(
    client: TestClient,
) -> None:
    _enter(client, counterparty="TIENDAS ARA 123")
    _enter(client, counterparty="EXITO EXPRESS")

    found = client.get("/financial/transactions?search=exito").json()

    assert found["total"] == 1
    assert found["transactions"][0]["counterparty"] == "EXITO EXPRESS"


def test_renaming_an_account_keeps_its_balance(client: TestClient) -> None:
    account = _declare(client)
    _enter(client, account_id=account["id"])

    renamed = client.patch(
        f"/financial/accounts/{account['id']}",
        json={"name": "Tarjeta principal"},
    )

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Tarjeta principal"
    assert renamed.json()["balance"] == "50000"


def test_closing_an_account_keeps_its_history(client: TestClient) -> None:
    account = _declare(client)
    _enter(client, account_id=account["id"])

    closed = client.post(f"/financial/accounts/{account['id']}/close")

    assert closed.status_code == 200
    assert closed.json()["closed_at"] is not None
    # Gone from the default list, still there when asked for.
    assert client.get("/financial/accounts").json()["accounts"] == []
    assert len(client.get("/financial/accounts?scope=all").json()["accounts"]) == 1


def test_linking_a_second_instrument_adopts_what_was_waiting(
    client: TestClient,
) -> None:
    """One real account emails under two names.

    Linking is the owner's call — inferring it would be guessing about
    somebody's money — and it claims the movements already waiting.
    """
    account = _declare(client, name="Cuenta de ahorros", kind="savings")
    linked = client.post(
        f"/financial/accounts/{account['id']}/instruments",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "savings_account",
            "last_four": "1234",
        },
    )

    assert linked.status_code == 200
    assert len(linked.json()["instruments"]) == 2


def test_unlinking_an_instrument_takes_it_off_the_account(
    client: TestClient,
) -> None:
    """A card declared on the wrong account can be taken back off it."""
    account = _declare(client)

    unlinked = client.post(
        f"/financial/accounts/{account['id']}/instruments/unlink",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "credit_card",
            "last_four": "7653",
        },
    )

    assert unlinked.status_code == 200
    assert unlinked.json()["instruments"] == []


def test_a_card_can_be_moved_from_one_account_to_another(
    client: TestClient,
) -> None:
    """Unlink then link: the movements travel with the card.

    The reason the pair exists — until now a card put on the wrong account
    could only be added to, so its movements stayed there for good.
    """
    wrong = _declare(client)
    right = _declare(client, name="Tarjeta correcta", last_four="1111")
    client.post(
        f"/financial/accounts/{wrong['id']}/instruments/unlink",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "credit_card",
            "last_four": "7653",
        },
    )
    moved = client.post(
        f"/financial/accounts/{right['id']}/instruments",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "credit_card",
            "last_four": "7653",
        },
    )

    assert moved.status_code == 200
    assert len(moved.json()["instruments"]) == 2


def test_unlinking_a_card_the_account_never_had_is_a_conflict(
    client: TestClient,
) -> None:
    """Not silence: the card is presumably on another account, and reporting
    "done" would send its owner looking somewhere else.
    """
    account = _declare(client)

    refused = client.post(
        f"/financial/accounts/{account['id']}/instruments/unlink",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "debit_card",
            "last_four": "9999",
        },
    )

    assert refused.status_code == 409


def test_reopening_an_account_lets_it_take_movements_again(
    client: TestClient,
) -> None:
    account = _declare(client)
    client.post(f"/financial/accounts/{account['id']}/close")

    reopened = client.post(f"/financial/accounts/{account['id']}/reopen")

    assert reopened.status_code == 200
    assert reopened.json()["closed_at"] is None
    assert len(client.get("/financial/accounts").json()["accounts"]) == 1

    entered = _enter(client, account_id=account["id"])
    assert entered["status"] == "assigned"


def test_reopening_an_account_that_was_never_closed_is_not_an_error(
    client: TestClient,
) -> None:
    account = _declare(client)

    reopened = client.post(f"/financial/accounts/{account['id']}/reopen")

    assert reopened.status_code == 200
    assert reopened.json()["closed_at"] is None


def test_declaring_an_account_adopts_what_was_waiting(client: TestClient) -> None:
    """Declaring an account is retroactive.

    The movements that arrived under that card before it existed are still
    here, and they belong to it.
    """
    _enter(client, counterparty="EXITO", amount="20000")
    waiting = client.get("/financial/transactions?unassigned=true").json()

    assert waiting["total"] == 1

    # A manual entry carries no fingerprint, so it is *not* adopted: nothing
    # ties it to a card. Only alerts are.
    account = _declare(client)

    assert account["balance"] == "0"
    assert client.get("/financial/transactions?unassigned=true").json()["total"] == 1


def test_a_movement_the_account_cannot_take_is_left_where_it_is(
    client: TestClient,
) -> None:
    """Half-adopting would leave an account that can never be recomputed.

    A movement in another currency must stay unassigned rather than be
    claimed and then fail the replay — the account would already exist, so
    it could not even be declared again.
    """
    account = _declare(client)
    usd = client.post(
        "/financial/transactions",
        json={
            "direction": "outgoing",
            "amount": "12",
            "currency": "USD",
            "occurred_at": WHEN,
            "counterparty": "AMAZON",
        },
    )

    assert usd.status_code == 201
    assert usd.json()["status"] == "unassigned"

    # Linking another instrument resettles the account; the USD movement must
    # not be dragged in and break it.
    linked = client.post(
        f"/financial/accounts/{account['id']}/instruments",
        json={
            "bank": "Bancolombia",
            "instrument_kind": "debit_card",
            "last_four": "9999",
        },
    )

    assert linked.status_code == 200
    assert linked.json()["balance"] == "0"


def test_another_users_account_reads_as_missing_not_forbidden(
    client: TestClient,
) -> None:
    response = client.get(f"/financial/accounts/{AccountId.new().value}")

    assert response.status_code == 404


def test_an_account_id_that_is_not_an_id_is_missing_too(client: TestClient) -> None:
    assert client.get("/financial/accounts/not-a-uuid").status_code == 404


def test_a_movement_nobody_owns_reads_as_missing(client: TestClient) -> None:
    assert client.get("/financial/transactions/deadbeef").status_code == 404


def test_a_movement_carries_the_merchant_behind_its_counterparty(
    client: TestClient,
) -> None:
    movement = _enter(client, counterparty="TIENDAS ARA")

    assert movement["merchant"] == {
        "id": ARA.merchant_id,
        "display_name": "Ara",
        "category": "groceries",
        "needs_review": False,
    }

    listed = client.get("/financial/transactions").json()["transactions"][0]

    assert listed["merchant"] == {
        "id": ARA.merchant_id,
        "display_name": "Ara",
        "category": "groceries",
        "needs_review": False,
    }

    fetched = client.get(f"/financial/transactions/{movement['id']}").json()

    assert fetched["merchant"]["display_name"] == "Ara"


def test_a_movement_no_merchant_owns_reads_with_a_null_merchant(
    client: TestClient,
) -> None:
    # Ordinary while the sighting is still on merchant's queue, permanent for
    # something entered by hand. Never an error.
    _enter(client, counterparty="PAGO NOMINA")

    listed = client.get("/financial/transactions").json()["transactions"][0]

    assert listed["merchant"] is None


def test_movements_can_be_asked_for_by_merchant(client: TestClient) -> None:
    _enter(client, counterparty="TIENDAS ARA")
    _enter(client, counterparty="PAGO NOMINA")

    page = client.get(
        "/financial/transactions",
        params={"merchant_id": ARA.merchant_id},
    ).json()

    assert page["total"] == 1
    assert page["transactions"][0]["counterparty"] == "TIENDAS ARA"


def test_movements_can_be_asked_for_by_category(client: TestClient) -> None:
    _enter(client, counterparty="TIENDAS ARA")
    _enter(client, counterparty="PAGO NOMINA")

    page = client.get(
        "/financial/transactions",
        params={"category": "groceries"},
    ).json()

    assert page["total"] == 1


def test_the_summary_answers_a_month_without_paging_the_ledger(
    client: TestClient,
) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="50000")
    _enter(client, counterparty="PAGO NOMINA", amount="20000")
    _enter(
        client,
        counterparty="SALARIO",
        amount="3000000",
        direction="incoming",
    )

    summary = client.get("/financial/summary").json()

    assert summary["group_by"] == "month"
    assert summary["totals"] == [
        {
            "currency": "COP",
            "incoming": "3000000",
            "outgoing": "70000",
            "net": "2930000",
            "movements": 3,
        },
    ]
    assert len(summary["groups"]) == 1


def test_the_summary_breaks_spending_down_by_category(client: TestClient) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="50000")
    _enter(client, counterparty="PAGO NOMINA", amount="20000")

    summary = client.get(
        "/financial/summary",
        params={"group_by": "category"},
    ).json()
    buckets = {group["key"]: group for group in summary["groups"]}

    assert buckets["groceries"]["totals"][0]["outgoing"] == "50000"
    # What no merchant owns is its own bucket, not a silent omission.
    assert buckets[None]["label"] == "Unattributed"


def test_the_summary_takes_the_filters_the_list_takes(client: TestClient) -> None:
    # This is what makes a bucket openable: the same query against
    # /transactions returns the movements behind it.
    _enter(client, counterparty="TIENDAS ARA", amount="50000")
    _enter(client, counterparty="PAGO NOMINA", amount="20000")

    summary = client.get(
        "/financial/summary",
        params={"merchant_id": ARA.merchant_id},
    ).json()

    assert summary["totals"][0]["outgoing"] == "50000"


def test_the_summary_refuses_a_timezone_it_cannot_read(client: TestClient) -> None:
    # Falling back to UTC would move somebody's late-evening spending into the
    # following month without saying so.
    response = client.get("/financial/summary", params={"timezone": "Mars/Olympus"})

    assert response.status_code == 400


def test_somebody_with_no_movements_gets_an_empty_but_valid_summary(
    client: TestClient,
) -> None:
    summary = client.get("/financial/summary").json()

    assert summary == {
        "group_by": "month",
        "timezone": "America/Bogota",
        "order": "movements",
        "totals": [],
        "groups": [],
        # Nothing was folded and nothing was compared, which is not the same
        # as either having been asked for and come back empty.
        "others": None,
        "folded": 0,
        "previous_totals": None,
        "previous_starts_at": None,
        "previous_ends_at": None,
    }


def test_correcting_a_movement_keeps_its_merchant_in_the_answer(
    client: TestClient,
) -> None:
    # A client refreshing its cache from the PATCH response would otherwise
    # drop the merchant until a full reload, with `null` claiming that nobody
    # owns a spelling that plainly has an owner.
    movement = _enter(client, counterparty="TIENDAS ARA")

    patched = client.patch(
        f"/financial/transactions/{movement['id']}",
        json={"note": "revisado"},
    )

    assert patched.status_code == 200
    assert patched.json()["merchant"]["display_name"] == "Ara"


def test_an_epoch_out_of_range_is_a_bad_request_not_a_crash(
    client: TestClient,
) -> None:
    # Unbounded, the conversion raises OSError from inside datetime and the
    # request answers 500 — which a browser then reports as a CORS failure,
    # because the error middleware sits outside the CORS one.
    for endpoint in ("/financial/transactions", "/financial/summary"):
        response = client.get(endpoint, params={"from": 10**18})

        assert response.status_code == 422, endpoint


def test_a_category_that_names_nothing_is_refused_not_answered_with_zero(
    client: TestClient,
) -> None:
    # An empty page on a money screen reads as "you spent nothing here", which
    # is the one wrong answer worse than an error.
    _enter(client, counterparty="TIENDAS ARA")

    assert (
        client.get(
            "/financial/transactions",
            params={"category": "Groceries"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/financial/summary",
            params={"group_by": "category", "category": "food"},
        ).status_code
        == 422
    )


def test_the_catalog_publishes_what_declaring_an_account_accepts(
    client: TestClient,
) -> None:
    response = client.get("/financial/catalog")

    assert response.status_code == 200

    catalog = response.json()
    kinds = {option["value"]: option["category"] for option in catalog["account_kinds"]}

    # Every kind the payload validates against, and the side of net worth it
    # lands on — derived here rather than restated by a client.
    assert kinds["savings"] == "asset"
    assert kinds["credit_card"] == "liability"
    assert kinds["mortgage"] == "liability"

    # A currency code is not a word: "Cop" would be a typo, not a label.
    assert {"value": "COP", "label": "COP"} in catalog["currencies"]
    assert "outgoing" in [option["value"] for option in catalog["movement_directions"]]
    assert "bank_alert" in [
        option["value"] for option in catalog["transaction_origins"]
    ]
    assert "unassigned" in [
        option["value"] for option in catalog["transaction_statuses"]
    ]
    assert "open" in [option["value"] for option in catalog["account_scopes"]]
    assert "month" in [option["value"] for option in catalog["summary_groupings"]]


def test_every_published_account_kind_is_one_the_api_actually_takes(
    client: TestClient,
) -> None:
    """The catalogue is worth nothing if a value in it is rejected on use."""
    catalog = client.get("/financial/catalog").json()

    for option in catalog["account_kinds"]:
        response = client.post(
            "/financial/accounts",
            json={"name": f"Cuenta {option['value']}", "kind": option["value"]},
        )

        assert response.status_code == 201, (option["value"], response.text)
        assert response.json()["kind"] == option["value"]


def test_declaring_an_account_with_an_account_kind_as_its_instrument_is_refused(
    client: TestClient,
) -> None:
    """The exact mistake that cost five movements in a real run.

    `savings` is what the owner calls the account; the alerts it sends name
    the instrument `account`. This used to be accepted and then match nothing,
    forever, reporting nothing anywhere.
    """
    response = client.post(
        "/financial/accounts",
        json={
            "name": "Cuenta de ahorros",
            "kind": "savings",
            "bank": "bancolombia",
            "instrument_kind": "savings",
            "last_four": "5261",
        },
    )

    assert response.status_code == 422


def test_the_catalog_offers_only_instruments_the_api_accepts(
    client: TestClient,
) -> None:
    catalog = client.get("/financial/catalog").json()

    for option in catalog["instrument_kinds"]:
        response = client.post(
            "/financial/accounts",
            json={
                "name": f"Cuenta {option['value']}",
                "kind": "checking",
                "bank": "bancolombia",
                "instrument_kind": option["value"],
                "last_four": "5261",
            },
        )

        assert response.status_code == 201, (option["value"], response.text)


def test_a_card_reports_its_limit_and_what_is_left_of_it(
    client: TestClient,
) -> None:
    account = client.post(
        "/financial/accounts",
        json={
            "name": "Tarjeta",
            "kind": "credit_card",
            # Spent so far, not the limit. The two are different numbers.
            "opening_balance": "200000",
            "credit_limit": "12000000",
        },
    ).json()

    assert account["credit_limit"] == "12000000"
    assert account["available"] == "11800000"


def test_an_asset_is_refused_a_credit_limit(client: TestClient) -> None:
    response = client.post(
        "/financial/accounts",
        json={"name": "Ahorros", "kind": "savings", "credit_limit": "12000000"},
    )

    assert response.status_code == 422


def test_a_limit_can_be_restated_and_cleared(client: TestClient) -> None:
    account = client.post(
        "/financial/accounts",
        json={"name": "Tarjeta", "kind": "credit_card", "credit_limit": "1000000"},
    ).json()

    raised = client.put(
        f"/financial/accounts/{account['id']}/credit-limit",
        json={"credit_limit": "3000000"},
    )

    assert raised.status_code == 200
    assert raised.json()["available"] == "3000000"

    cleared = client.put(
        f"/financial/accounts/{account['id']}/credit-limit",
        json={"credit_limit": None},
    )

    assert cleared.status_code == 200
    assert cleared.json()["credit_limit"] is None
    # Not zero: zero would read as "no credit left", a different fact.
    assert cleared.json()["available"] is None


def test_a_card_over_its_limit_says_so_rather_than_reporting_zero(
    client: TestClient,
) -> None:
    account = client.post(
        "/financial/accounts",
        json={
            "name": "Tarjeta",
            "kind": "credit_card",
            "opening_balance": "120000",
            "credit_limit": "100000",
        },
    ).json()

    assert account["available"] == "-20000"


def test_restating_a_balance_keeps_every_movement_counted(client: TestClient) -> None:
    # The case this exists for: somebody declares an account, cannot remember
    # what it held before the alerts already on record, and states what their
    # bank shows today instead.
    account = _declare(client, name="Ahorros", kind="savings", currency="COP")
    account_id = str(account["id"])
    _enter(client, amount="50000", direction="outgoing", account_id=account_id)
    _enter(client, amount="30000", direction="incoming", account_id=account_id)

    response = client.put(
        f"/financial/accounts/{account_id}/balance",
        json={"balance": "1200000"},
    )

    assert response.status_code == 200, response.text
    restated = response.json()
    assert restated["balance"] == "1200000"
    # Solved backwards: the two movements net to -20000.
    assert restated["opening_balance"] == "1220000"
    assert restated["movements_applied"] == 2

    # No movement was touched to make the number come out.
    listed = client.get("/financial/transactions", params={"account_id": account_id})
    assert listed.json()["total"] == 2


def test_a_movement_after_a_restatement_lands_on_top_of_it(
    client: TestClient,
) -> None:
    account = _declare(client, name="Ahorros", kind="savings")
    account_id = str(account["id"])
    client.put(
        f"/financial/accounts/{account_id}/balance",
        json={"balance": "1200000"},
    )

    _enter(client, amount="200000", direction="outgoing", account_id=account_id)

    current = client.get(f"/financial/accounts/{account_id}").json()
    assert current["balance"] == "1000000"


def test_a_restated_balance_moves_net_worth(client: TestClient) -> None:
    account = _declare(client, name="Ahorros", kind="savings")

    client.put(
        f"/financial/accounts/{account['id']}/balance",
        json={"balance": "1200000"},
    )

    figures = client.get("/financial/net-worth").json()
    assert [figure["total"] for figure in figures] == ["1200000"]


def test_restating_a_card_recomputes_what_is_available(client: TestClient) -> None:
    card = _declare(client, credit_limit="12000000")

    restated = client.put(
        f"/financial/accounts/{card['id']}/balance",
        json={"balance": "450000"},
    ).json()

    # On a liability the stated figure is what is owed, so the limit is that
    # much further away.
    assert restated["balance"] == "450000"
    assert restated["available"] == "11550000"


def test_a_balance_can_be_restated_below_zero(client: TestClient) -> None:
    account = _declare(client, name="Ahorros", kind="savings")

    restated = client.put(
        f"/financial/accounts/{account['id']}/balance",
        json={"balance": "-40000"},
    ).json()

    assert restated["balance"] == "-40000"


def test_restating_an_account_nobody_owns_is_a_404(client: TestClient) -> None:
    response = client.put(
        "/financial/accounts/8f14e45f-ceea-467a-9c1b-000000000000/balance",
        json={"balance": "1200000"},
    )

    assert response.status_code == 404


def test_a_balance_beyond_what_the_table_can_hold_is_refused(
    client: TestClient,
) -> None:
    """Refused at the boundary, not by boto3.

    Unbounded, `1E+200` reaches DynamoDB's `N` — which tops out far below it
    — and the answer is a 500 with a stack trace instead of the plain
    refusal every other money field on this router gives.
    """
    account = _declare(client, name="Ahorros", kind="savings")

    response = client.put(
        f"/financial/accounts/{account['id']}/balance",
        json={"balance": "1E+200"},
    )

    assert response.status_code == 422


# --------------------------------------------------------------- traslados


def _pay_a_card(ledger: InMemoryLedger, *, amount: str = "3540258") -> None:
    """Both sides of a card payment, put in the ledger the way the worker
    leaves them."""
    source, destination = Transaction.as_transfer(
        user_id=USER_ID,
        bank="bancolombia",
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(WHEN),
        source_instrument_kind=InstrumentKind.ACCOUNT.value,
        source_last_four="5261",
        destination_instrument_kind=InstrumentKind.CREDIT_CARD.value,
        destination_last_four="7653",
    )
    ledger.save(source)
    ledger.save(destination)


def test_a_transfer_leg_reports_the_other_side_instead_of_a_merchant(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """Structured, so a client says «pago a tu tarjeta ···· 7653» without
    parsing the counterparty text."""
    client, ledger = wired
    _pay_a_card(ledger)

    body = client.get("/financial/transactions").json()
    legs = [row for row in body["transactions"] if row["transfer"] is not None]

    assert len(legs) == 2
    roles = {leg["transfer"]["role"] for leg in legs}
    assert roles == {"source", "destination"}
    assert {leg["transfer"]["id"] for leg in legs} == {legs[0]["transfer"]["id"]}


def test_deleting_one_side_of_a_transfer_erases_both(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """Two rows state one movement of money. Left half-erased, the survivor
    claims a payment to a movement that is no longer there.
    """
    client, ledger = wired
    _pay_a_card(ledger)
    rows = client.get("/financial/transactions").json()["transactions"]

    body = client.delete(f"/financial/transactions/{rows[0]['id']}").json()

    assert set(body["erased"]) == {row["id"] for row in rows}
    assert client.get("/financial/transactions").json()["total"] == 0


def test_either_side_of_a_transfer_erases_the_pair(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """Whichever row the owner is looking at is the one they press delete on."""
    client, ledger = wired
    _pay_a_card(ledger)
    rows = client.get("/financial/transactions").json()["transactions"]

    response = client.delete(f"/financial/transactions/{rows[1]['id']}")

    assert response.status_code == 200
    assert len(response.json()["erased"]) == 2


def test_each_side_points_at_the_other_row(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    client, ledger = wired
    _pay_a_card(ledger)

    rows = client.get("/financial/transactions").json()["transactions"]
    by_id = {row["id"]: row for row in rows}

    for row in rows:
        counterpart = by_id[row["transfer"]["counterpart_movement_id"]]
        assert counterpart["id"] != row["id"]
        assert counterpart["transfer"]["counterpart_movement_id"] == row["id"]


# --------------------------------------- un traslado desde fuera de la app


def _pay_from_outside(client: TestClient, **overrides: object) -> dict[str, Any]:
    """A card paid from a bank, wallet or pocket this app does not hold."""
    payload: dict[str, object] = {
        "role": "destination",
        "amount": "3540258",
        "currency": "COP",
        "occurred_at": WHEN,
        "counterparty": "Nequi",
    }
    payload.update(overrides)
    response = client.post("/financial/transactions/transfer", json=payload)

    assert response.status_code == 201, response.text

    return response.json()


def test_a_payment_from_outside_is_recorded_as_a_transfer(
    client: TestClient,
) -> None:
    card = _declare(client)

    body = _pay_from_outside(client, account_id=card["id"])

    assert body["transfer"] is not None
    assert body["transfer"]["role"] == "destination"
    assert body["direction"] == "incoming"
    assert body["origin"] == "manual"


def test_its_counterpart_is_reported_as_external(client: TestClient) -> None:
    """A client reads one boolean rather than null-checking three fields."""
    card = _declare(client)

    leg = _pay_from_outside(client, account_id=card["id"])["transfer"]

    assert leg["external"] is True
    assert leg["counterpart_movement_id"] is None
    assert leg["counterpart_instrument_kind"] is None
    assert leg["counterpart_last_four"] is None


def test_a_payment_from_outside_lowers_the_debt(client: TestClient) -> None:
    card = _declare(client, opening_balance="3540258")

    _pay_from_outside(client, account_id=card["id"])

    after = client.get(f"/financial/accounts/{card['id']}").json()
    assert Decimal(after["balance"]) == Decimal("0")


def test_a_payment_from_outside_is_not_income(client: TestClient) -> None:
    """The reason the endpoint exists. Entered as an ordinary movement this
    would be the month's largest income.

    A real expense sits beside it so the assertion cannot pass by the summary
    simply being empty: what is counted is counted, and the leg is not.
    """
    card = _declare(client)
    _enter(client, account_id=card["id"], amount="50000")
    _pay_from_outside(client, account_id=card["id"])

    totals = client.get("/financial/summary").json()["totals"]

    assert len(totals) == 1
    assert Decimal(totals[0]["outgoing"]) == Decimal("50000")
    assert Decimal(totals[0]["incoming"]) == Decimal("0")
    assert totals[0]["movements"] == 1


def test_paying_a_card_elsewhere_is_not_an_expense(client: TestClient) -> None:
    """The mirror: money leaving a tracked account towards a card that is
    not here."""
    savings = _declare(
        client,
        kind="savings",
        instrument_kind="account",
        last_four="5261",
        opening_balance="5000000",
    )
    _enter(client, account_id=savings["id"], amount="50000")
    _pay_from_outside(client, account_id=savings["id"], role="source")

    totals = client.get("/financial/summary").json()["totals"]

    assert len(totals) == 1
    assert Decimal(totals[0]["outgoing"]) == Decimal("50000")


def test_the_leg_is_still_listed_among_the_movements(client: TestClient) -> None:
    """Excluded from totals, never hidden: it is what explains the fall."""
    card = _declare(client)
    entered = _pay_from_outside(client, account_id=card["id"])

    listed = client.get("/financial/transactions").json()["transactions"]

    assert [row["id"] for row in listed] == [entered["id"]]


def test_the_leg_can_be_asked_for_on_its_own(client: TestClient) -> None:
    card = _declare(client)
    _enter(client, account_id=card["id"])
    entered = _pay_from_outside(client, account_id=card["id"])

    body = client.get("/financial/transactions", params={"transfers": "only"}).json()

    assert [row["id"] for row in body["transactions"]] == [entered["id"]]


def test_the_source_role_records_money_leaving(client: TestClient) -> None:
    savings = _declare(
        client,
        kind="savings",
        instrument_kind="account",
        last_four="5261",
        opening_balance="5000000",
    )

    body = _pay_from_outside(client, account_id=savings["id"], role="source")

    assert body["direction"] == "outgoing"


def test_a_leg_without_an_account_is_refused(client: TestClient) -> None:
    """It asserts a balance moved; there is no balance to move without one,
    and nothing would ever adopt it later."""
    response = client.post(
        "/financial/transactions/transfer",
        json={
            "role": "destination",
            "amount": "100",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "Nequi",
        },
    )

    assert response.status_code == 422


def test_a_leg_on_an_account_that_does_not_exist_is_refused(
    client: TestClient,
) -> None:
    response = client.post(
        "/financial/transactions/transfer",
        json={
            "role": "destination",
            "amount": "100",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "Nequi",
            "account_id": "8f14e45f-ceea-467a-9c2b-6a2c0a1b1111",
        },
    )

    assert response.status_code == 404


def test_a_role_the_api_does_not_know_is_refused(client: TestClient) -> None:
    card = _declare(client)
    response = client.post(
        "/financial/transactions/transfer",
        json={
            "role": "sideways",
            "amount": "100",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "Nequi",
            "account_id": card["id"],
        },
    )

    assert response.status_code == 422


def test_a_leg_with_no_amount_is_refused(client: TestClient) -> None:
    card = _declare(client)
    response = client.post(
        "/financial/transactions/transfer",
        json={
            "role": "destination",
            "amount": "0",
            "currency": "COP",
            "occurred_at": WHEN,
            "counterparty": "Nequi",
            "account_id": card["id"],
        },
    )

    assert response.status_code == 422


def test_a_leg_entered_from_outside_can_still_be_corrected(
    client: TestClient,
) -> None:
    """Allowed where a paired leg is refused: there is no second row to leave
    out of step."""
    card = _declare(client, opening_balance="3540258")
    entered = _pay_from_outside(client, account_id=card["id"], amount="3000000")

    response = client.patch(
        f"/financial/transactions/{entered['id']}",
        json={"amount": "3540258", "currency": "COP"},
    )

    assert response.status_code == 200
    assert response.json()["transfer"]["external"] is True


def test_correcting_it_moves_the_balance_with_it(client: TestClient) -> None:
    card = _declare(client, opening_balance="3540258")
    entered = _pay_from_outside(client, account_id=card["id"], amount="3000000")

    client.patch(
        f"/financial/transactions/{entered['id']}",
        json={"amount": "3540258", "currency": "COP"},
    )

    after = client.get(f"/financial/accounts/{card['id']}").json()
    assert Decimal(after["balance"]) == Decimal("0")


def test_pressing_the_button_twice_records_one_payment(
    client: TestClient,
) -> None:
    """Both answer 201 with the same movement, and the debt falls once. A
    second row here would halve a debt that was only paid once."""
    card = _declare(client, opening_balance="3540258")

    first = _pay_from_outside(client, account_id=card["id"])
    second = _pay_from_outside(client, account_id=card["id"])

    assert first["id"] == second["id"]
    after = client.get(f"/financial/accounts/{card['id']}").json()
    assert Decimal(after["balance"]) == Decimal("0")
    listed = client.get("/financial/transactions").json()
    assert listed["total"] == 1


def test_paying_the_card_again_later_is_a_second_payment(
    client: TestClient,
) -> None:
    """The guard is about one payment typed twice, not about paying twice."""
    card = _declare(client, opening_balance="3540258")

    _pay_from_outside(client, account_id=card["id"], amount="1000000")
    _pay_from_outside(
        client, account_id=card["id"], amount="1000000", occurred_at=WHEN + 86400
    )

    after = client.get(f"/financial/accounts/{card['id']}").json()
    assert Decimal(after["balance"]) == Decimal("1540258")
    assert client.get("/financial/transactions").json()["total"] == 2


def test_a_leg_entered_from_outside_can_be_deleted(client: TestClient) -> None:
    """It has no second row to take with it, and the debt it paid off goes
    back up by exactly what it took.
    """
    card = _declare(client, opening_balance="3540258")
    leg = _pay_from_outside(client, account_id=card["id"])

    assert client.get("/financial/accounts").json()["accounts"][0]["balance"] == "0"

    body = client.delete(f"/financial/transactions/{leg['id']}").json()

    assert body["erased"] == [leg["id"]]
    assert body["accounts"][0]["balance"] == "3540258"


def test_a_leg_entered_from_outside_cannot_be_detached(
    client: TestClient,
) -> None:
    """409, and the balance stays where the payment left it: detached, the
    row would say a payment happened while no balance shows one, and nothing
    would ever adopt it back."""
    card = _declare(client, opening_balance="3540258")
    entered = _pay_from_outside(client, account_id=card["id"])

    response = client.patch(
        f"/financial/transactions/{entered['id']}",
        json={"detach": True},
    )

    assert response.status_code == 409
    after = client.get(f"/financial/accounts/{card['id']}").json()
    assert Decimal(after["balance"]) == Decimal("0")


def test_a_leg_entered_from_outside_can_be_moved_to_another_account(
    client: TestClient,
) -> None:
    """Moving is what a leg on the wrong card needs, and it stays allowed."""
    card = _declare(client, opening_balance="3540258")
    other = _declare(
        client,
        name="Otra tarjeta",
        last_four="9090",
        opening_balance="1000000",
    )
    entered = _pay_from_outside(client, account_id=card["id"], amount="500000")

    response = client.patch(
        f"/financial/transactions/{entered['id']}",
        json={"account_id": other["id"]},
    )

    assert response.status_code == 200
    assert Decimal(
        client.get(f"/financial/accounts/{card['id']}").json()["balance"]
    ) == Decimal("3540258")
    assert Decimal(
        client.get(f"/financial/accounts/{other['id']}").json()["balance"]
    ) == Decimal("500000")


def test_the_catalog_publishes_the_transfer_roles(client: TestClient) -> None:
    body = client.get("/financial/catalog").json()

    assert {option["value"] for option in body["transfer_roles"]} == {
        "source",
        "destination",
    }


def test_an_ordinary_movement_reports_no_transfer(client: TestClient) -> None:
    _enter(client)

    body = client.get("/financial/transactions").json()

    assert body["transactions"][0]["transfer"] is None


def test_the_list_shows_both_sides_by_default(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    client, ledger = wired
    _pay_a_card(ledger)
    _enter(client)

    body = client.get("/financial/transactions").json()

    assert body["total"] == 3


def test_the_list_can_be_asked_to_hide_them(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    client, ledger = wired
    _pay_a_card(ledger)
    _enter(client)

    body = client.get("/financial/transactions", params={"transfers": "exclude"}).json()

    assert body["total"] == 1
    assert body["transactions"][0]["transfer"] is None


def test_the_summary_leaves_transfers_out_without_being_asked(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """The default that keeps a card payment from reading as the month's
    largest expense."""
    client, ledger = wired
    _pay_a_card(ledger)
    _enter(client)

    body = client.get("/financial/summary", params={"group_by": "month"}).json()

    assert body["totals"][0]["outgoing"] == "50000"
    assert body["totals"][0]["incoming"] == "0"


def test_the_summary_can_be_asked_for_the_transfers_alone(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    client, ledger = wired
    _pay_a_card(ledger)
    _enter(client)

    body = client.get(
        "/financial/summary",
        params={"group_by": "month", "transfers": "only"},
    ).json()

    assert body["totals"][0]["outgoing"] == "3540258"
    assert body["totals"][0]["incoming"] == "3540258"


def test_a_transfers_value_the_api_does_not_know_is_refused(
    client: TestClient,
) -> None:
    """An enum, not free text: a typo must not silently mean «include»."""
    response = client.get("/financial/transactions", params={"transfers": "maybe"})

    assert response.status_code == 422


def test_the_catalog_publishes_the_transfer_views(client: TestClient) -> None:
    body = client.get("/financial/catalog").json()

    assert [option["value"] for option in body["transfer_views"]] == [
        "include",
        "exclude",
        "only",
    ]


def test_one_side_of_a_transfer_cannot_be_corrected_on_its_own(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """A refusal rather than a half-applied correction: the two sides state one
    movement, and the other side lives on another balance.

    409 rather than 400 or 422, the same way a duplicate account answers: the
    body is well formed and the movement exists — what refuses it is that this
    row is half of one fact, and no rewording of the request would help.

    The amount travels with its currency on purpose: without it the payload
    validator rejects the request first, and this test would pass while the
    domain rule went unexercised.
    """
    client, ledger = wired
    _pay_a_card(ledger)
    leg = client.get("/financial/transactions").json()["transactions"][0]

    response = client.patch(
        f"/financial/transactions/{leg['id']}",
        json={"amount": "100", "currency": "COP"},
    )

    assert response.status_code == 409, response.text
    assert "transfer" in response.json()["detail"]


def test_moving_one_side_of_a_transfer_in_time_is_refused_too(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    client, ledger = wired
    _pay_a_card(ledger)
    leg = client.get("/financial/transactions").json()["transactions"][0]

    response = client.patch(
        f"/financial/transactions/{leg['id']}",
        json={"occurred_at": WHEN + 3600},
    )

    assert response.status_code == 409, response.text


def test_a_note_can_still_be_written_on_a_transfer_leg(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """An annotation is not a claim about the movement — it is how somebody
    records why they paid the card early."""
    client, ledger = wired
    _pay_a_card(ledger)
    leg = client.get("/financial/transactions").json()["transactions"][0]

    response = client.patch(
        f"/financial/transactions/{leg['id']}",
        json={"note": "pago anticipado"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["note"] == "pago anticipado"


def test_a_transfer_leg_can_still_be_moved_to_the_right_account(
    wired: tuple[TestClient, InMemoryLedger],
) -> None:
    """Routing is not the money: putting a side on the account that actually
    holds it has to stay possible."""
    client, ledger = wired
    _pay_a_card(ledger)
    account = _declare(client, name="Ahorros", kind="savings")
    leg = next(
        row
        for row in client.get("/financial/transactions").json()["transactions"]
        if row["transfer"]["role"] == "source"
    )

    response = client.patch(
        f"/financial/transactions/{leg['id']}",
        json={"account_id": account["id"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == account["id"]


# ------------------------------------------------------------- reporting


# WHEN is 2026-08-21 in Bogotá. These sit around it in the same local month.
AUGUST_START = 1_785_560_400  # 2026-08-01 00:00 Bogotá
SEPTEMBER_START = 1_788_238_800  # 2026-09-01 00:00 Bogotá
JULY_MIDDAY = 1_784_900_000  # 2026-07-22


def test_a_summary_can_be_bucketed_by_day_and_by_weekday(
    client: TestClient,
) -> None:
    _enter(client, counterparty="TIENDAS ARA", occurred_at=WHEN)

    day = client.get("/financial/summary", params={"group_by": "day"}).json()
    weekday = client.get("/financial/summary", params={"group_by": "weekday"}).json()

    assert [group["key"] for group in day["groups"]] == ["2026-08-23"]
    # 2026-08-23 is a Sunday, and the label is English for the client to
    # translate, like every other label this API returns.
    assert [(g["key"], g["label"]) for g in weekday["groups"]] == [("7", "Sunday")]


def test_a_summary_can_be_ranked_by_amount_once_a_currency_is_pinned(
    client: TestClient,
) -> None:
    _enter(client, counterparty="UBER", amount="10000")
    _enter(client, counterparty="UBER", amount="10000")
    _enter(client, counterparty="TIENDAS ARA", amount="500000")

    ranked = client.get(
        "/financial/summary",
        params={
            "group_by": "merchant",
            "currency": "COP",
            "order": "amount",
        },
    ).json()

    assert ranked["order"] == "amount"
    # Ara is one movement against Uber's two, and still the larger bucket.
    assert ranked["groups"][0]["label"] == "Ara"


def test_ranking_by_amount_without_a_currency_is_refused(
    client: TestClient,
) -> None:
    response = client.get("/financial/summary", params={"order": "amount"})

    assert response.status_code == 400
    assert "currency" in response.json()["detail"]


def test_the_tail_of_a_breakdown_folds_into_a_keyless_remainder(
    client: TestClient,
) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="500000")
    _enter(client, counterparty="UBER", amount="10000")
    _enter(client, counterparty="OTRO", amount="5000")

    folded = client.get(
        "/financial/summary",
        params={
            "group_by": "merchant",
            "currency": "COP",
            "order": "amount",
            "top": 1,
        },
    ).json()

    # Two buckets, not three: the directory owns `TIENDAS ARA` and the other
    # two spellings share the one bucket nothing has been attributed to.
    assert len(folded["groups"]) == 1
    assert folded["folded"] == 1
    # No key, because unlike a real bucket it cannot be reopened as a list.
    assert folded["others"]["key"] is None
    assert folded["others"]["totals"][0]["outgoing"] == "15000"


def test_folding_a_stretch_of_time_is_refused(client: TestClient) -> None:
    response = client.get(
        "/financial/summary",
        params={"group_by": "month", "top": 3},
    )

    assert response.status_code == 400
    assert "does not apply" in response.json()["detail"]


def test_a_summary_can_be_compared_against_the_window_before_it(
    client: TestClient,
) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="80000", occurred_at=WHEN)
    _enter(client, counterparty="TIENDAS ARA", amount="50000", occurred_at=JULY_MIDDAY)

    compared = client.get(
        "/financial/summary",
        params={
            "group_by": "category",
            "from": AUGUST_START,
            "to": SEPTEMBER_START,
            "compare": "true",
        },
    ).json()

    assert compared["totals"][0]["outgoing"] == "80000"
    assert compared["previous_totals"][0]["outgoing"] == "50000"
    assert compared["previous_starts_at"] == AUGUST_START - (
        SEPTEMBER_START - AUGUST_START
    )
    assert compared["previous_ends_at"] == AUGUST_START
    assert compared["groups"][0]["previous_totals"][0]["outgoing"] == "50000"


def test_comparing_without_a_window_is_refused(client: TestClient) -> None:
    response = client.get("/financial/summary", params={"compare": "true"})

    assert response.status_code == 400
    assert "since" in response.json()["detail"]


def test_a_trend_answers_dense_buckets_with_one_point_each(
    client: TestClient,
) -> None:
    """A client zips `series[].points` against `buckets` by index, so every
    series has exactly one point per bucket including the empty ones.
    """
    _enter(client, counterparty="TIENDAS ARA", amount="80000", occurred_at=WHEN)

    trend = client.get(
        "/financial/trends",
        params={"interval": "month", "dimension": "category", "periods": 6},
    ).json()

    assert len(trend["buckets"]) == 6
    assert trend["dimension"] == "category"

    for series in trend["series"]:
        assert [point["bucket"] for point in series["points"]] == [
            bucket["key"] for bucket in trend["buckets"]
        ]

    # Only the period being lived is partial.
    assert [bucket["partial"] for bucket in trend["buckets"]].count(True) == 1


def test_an_undivided_trend_carries_both_directions_in_one_band(
    client: TestClient,
) -> None:
    _enter(client, counterparty="NOMINA", amount="3000000", direction="incoming")
    _enter(client, counterparty="TIENDAS ARA", amount="80000")

    trend = client.get(
        "/financial/trends",
        params={"dimension": "none", "periods": 2},
    ).json()
    moved = next(point for point in trend["series"][0]["points"] if point["totals"])

    assert len(trend["series"]) == 1
    assert trend["series"][0]["label"] == "Total"
    assert moved["totals"][0]["incoming"] == "3000000"
    assert moved["totals"][0]["outgoing"] == "80000"


def test_a_trend_folds_the_bands_a_chart_cannot_stack(client: TestClient) -> None:
    _enter(client, counterparty="TIENDAS ARA", amount="500000")
    _enter(client, counterparty="UBER", amount="10000")

    trend = client.get(
        "/financial/trends",
        params={
            "dimension": "merchant",
            "currency": "COP",
            "order": "amount",
            "series": 1,
            "periods": 2,
        },
    ).json()

    assert trend["folded"] == 1
    assert trend["others"]["key"] is None
    # Folded and all, the remainder is still one point per bucket.
    assert len(trend["others"]["points"]) == len(trend["buckets"])


def test_a_range_too_wide_to_chart_is_refused(client: TestClient) -> None:
    response = client.get(
        "/financial/trends",
        params={
            "interval": "day",
            "from": AUGUST_START - 3 * 365 * 86_400,
            "to": SEPTEMBER_START,
        },
    )

    assert response.status_code == 400
    assert "shorter one" in response.json()["detail"]


def test_movements_can_be_asked_for_largest_first(client: TestClient) -> None:
    _enter(client, counterparty="PEQUENO", amount="1000")
    _enter(client, counterparty="GRANDE", amount="900000")
    _enter(client, counterparty="MEDIANO", amount="40000")

    page = client.get(
        "/financial/transactions",
        params={"sort": "amount", "currency": "COP", "limit": 2},
    ).json()

    assert [row["counterparty"] for row in page["transactions"]] == [
        "GRANDE",
        "MEDIANO",
    ]


def test_ordering_movements_by_size_without_a_currency_is_refused(
    client: TestClient,
) -> None:
    response = client.get("/financial/transactions", params={"sort": "amount"})

    assert response.status_code == 400
    assert "currency" in response.json()["detail"]


def test_the_catalog_offers_every_new_reporting_vocabulary(
    client: TestClient,
) -> None:
    """A form must not be able to offer what the API rejects."""
    catalog = client.get("/financial/catalog").json()

    assert {option["value"] for option in catalog["summary_groupings"]} == {
        "day",
        "week",
        "month",
        "weekday",
        "category",
        "merchant",
        "account",
    }
    assert {option["value"] for option in catalog["summary_orders"]} == {
        "movements",
        "amount",
    }
    assert {option["value"] for option in catalog["trend_intervals"]} == {
        "day",
        "week",
        "month",
    }
    assert {option["value"] for option in catalog["trend_dimensions"]} == {
        "none",
        "category",
        "merchant",
        "account",
    }
    assert {option["value"] for option in catalog["transaction_sorts"]} == {
        "date",
        "amount",
    }


# ------------------------------------------------------------ financiación


def _declare_mortgage(client: TestClient, *, balance: str = "60000000") -> str:
    created = client.post(
        "/financial/accounts",
        json={
            "name": "Hipoteca",
            "kind": "mortgage",
            "currency": "COP",
            "opening_balance": balance,
            "bank": "Bancolombia",
        },
    )

    assert created.status_code == 201

    return str(created.json()["id"])


def _loan_terms(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "rate": {"value": "0.1956", "basis": "effective_annual"},
        "disbursed_on": "2026-01-15",
        "term_months": 60,
        "statement_day": 15,
        "payment_day": 20,
        "principal": "60000000",
        "installment": "2000000",
        "installment_covers_charges": True,
        "charges": [
            {
                "name": "Seguro de vida deudores",
                "basis": "outstanding_balance",
                "rate": "0.000345",
            },
        ],
        "accrue_from": "2026-01-15",
    }
    payload.update(overrides)

    return payload


def test_declaring_loan_terms_answers_with_the_rate_converted(
    client: TestClient,
) -> None:
    account_id = _declare_mortgage(client)

    response = client.put(
        f"/financial/accounts/{account_id}/loan",
        json=_loan_terms(),
    )

    assert response.status_code == 200
    loan = response.json()["loan"]
    assert loan["rate"]["basis"] == "effective_annual"
    # The same rate a bank would print beside it, so a screen never converts.
    assert loan["rate"]["monthly"].startswith("0.01499871")
    assert loan["statement_day"] == 15
    assert loan["payment_day"] == 20
    assert loan["matures_on"] == "2031-01-15"
    assert response.json()["accrued_through"] == "2026-01-15"


def test_a_savings_account_is_refused_loan_terms(client: TestClient) -> None:
    created = client.post(
        "/financial/accounts",
        json={"name": "Ahorros", "kind": "savings", "currency": "COP"},
    )
    account_id = created.json()["id"]

    response = client.put(
        f"/financial/accounts/{account_id}/loan",
        json=_loan_terms(),
    )

    assert response.status_code == 400
    assert "loan terms" in response.json()["detail"]


def test_a_rate_typed_as_a_percentage_is_refused(client: TestClient) -> None:
    account_id = _declare_mortgage(client)

    response = client.put(
        f"/financial/accounts/{account_id}/loan",
        json=_loan_terms(rate={"value": "19.56", "basis": "effective_annual"}),
    )

    assert response.status_code == 422


def test_accruing_posts_the_month_and_moves_the_balance(client: TestClient) -> None:
    account_id = _declare_mortgage(client)
    client.put(f"/financial/accounts/{account_id}/loan", json=_loan_terms())

    response = client.post(
        f"/financial/accounts/{account_id}/accrue",
        json={"through": "2026-02-20", "timezone": "America/Bogota"},
    )

    assert response.status_code == 200
    body = response.json()
    assert [row["counterparty"] for row in body["posted"]] == [
        "Intereses",
        "Seguro de vida deudores",
    ]
    assert body["account"]["balance"] == "60920622.87"
    assert body["accrued_through"] == "2026-02-15"
    # And each one reads as what it is, so a movements list can say so.
    assert {row["origin"] for row in body["posted"]} == {"accrual"}


def test_accruing_twice_charges_the_month_once(client: TestClient) -> None:
    account_id = _declare_mortgage(client)
    client.put(f"/financial/accounts/{account_id}/loan", json=_loan_terms())
    client.post(
        f"/financial/accounts/{account_id}/accrue",
        json={"through": "2026-02-20"},
    )

    again = client.post(
        f"/financial/accounts/{account_id}/accrue",
        json={"through": "2026-02-20"},
    )

    assert again.status_code == 200
    assert again.json()["posted"] == []
    assert again.json()["account"]["balance"] == "60920622.87"


def test_the_sweep_brings_every_financed_account_up_to_date(client: TestClient) -> None:
    account_id = _declare_mortgage(client)
    client.put(f"/financial/accounts/{account_id}/loan", json=_loan_terms())
    client.post(
        "/financial/accounts",
        json={"name": "Ahorros", "kind": "savings", "currency": "COP"},
    )

    response = client.post("/financial/accrue", json={"through": "2026-02-20"})

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["account"]["id"] == account_id


def test_the_schedule_shows_what_the_instalment_is_actually_made_of(
    client: TestClient,
) -> None:
    account_id = _declare_mortgage(client)
    client.put(f"/financial/accounts/{account_id}/loan", json=_loan_terms())

    response = client.get(
        f"/financial/accounts/{account_id}/financing",
        params={"periods": 3, "as_of": "2026-01-15"},
    )

    assert response.status_code == 200
    body = response.json()
    first = body["schedule"]["payments"][0]
    assert first["interest"] == "899922.87"
    assert first["charges"][0]["amount"] == "20700.00"
    assert first["principal"] == "1079377.13"
    assert first["due"] == "2000000.00"
    assert first["closing_balance"] == "58920622.87"
    assert first["due_on"] == "2026-02-20"
    assert body["schedule"]["negatively_amortizing"] is False


def test_a_schedule_warns_when_the_instalment_does_not_cover_the_interest(
    client: TestClient,
) -> None:
    account_id = _declare_mortgage(client)
    client.put(
        f"/financial/accounts/{account_id}/loan",
        json=_loan_terms(installment="500000"),
    )

    response = client.get(
        f"/financial/accounts/{account_id}/financing",
        params={"periods": 3, "as_of": "2026-01-15"},
    )

    assert response.json()["schedule"]["negatively_amortizing"] is True


def test_an_account_stating_no_terms_answers_409(client: TestClient) -> None:
    account_id = _declare_mortgage(client)

    response = client.get(f"/financial/accounts/{account_id}/financing")

    assert response.status_code == 409


def test_clearing_the_terms_stops_the_future_and_keeps_the_past(
    client: TestClient,
) -> None:
    account_id = _declare_mortgage(client)
    client.put(f"/financial/accounts/{account_id}/loan", json=_loan_terms())
    client.post(
        f"/financial/accounts/{account_id}/accrue",
        json={"through": "2026-02-20"},
    )

    cleared = client.delete(f"/financial/accounts/{account_id}/financing")

    assert cleared.status_code == 200
    assert cleared.json()["loan"] is None
    # The rows the month charged are still there, and so is the balance.
    assert cleared.json()["balance"] == "60920622.87"


def test_stating_what_a_fund_is_worth_records_the_difference(
    client: TestClient,
) -> None:
    created = client.post(
        "/financial/accounts",
        json={
            "name": "Fondo de inversión",
            "kind": "investment",
            "currency": "COP",
            "opening_balance": "10000000",
        },
    )
    account_id = created.json()["id"]

    response = client.post(
        f"/financial/accounts/{account_id}/value",
        json={"market_value": "10450000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["balance"] == "10450000"
    assert body["posted"][0]["counterparty"] == "Valoración"
    assert body["posted"][0]["amount"] == "450000"
    assert body["posted"][0]["direction"] == "incoming"


def test_stating_the_value_it_already_has_records_nothing(client: TestClient) -> None:
    created = client.post(
        "/financial/accounts",
        json={
            "name": "Fondo de inversión",
            "kind": "investment",
            "currency": "COP",
            "opening_balance": "10000000",
        },
    )
    account_id = created.json()["id"]

    response = client.post(
        f"/financial/accounts/{account_id}/value",
        json={"market_value": "10000000"},
    )

    assert response.status_code == 200
    assert response.json()["posted"] == []
    assert response.json()["reason"] == "the value has not changed"


def test_a_value_the_fund_held_earlier_today_is_recorded_again(
    client: TestClient,
) -> None:
    """The answer used to say 200 over a balance that had not moved.

    11M up to 15M, back to 11M, up to 15M again, in one sitting. The last call
    repeats the first move exactly, and reporting success while leaving the
    fund at 11 million is worse than refusing it.
    """
    created = client.post(
        "/financial/accounts",
        json={
            "name": "Fondo de inversión",
            "kind": "investment",
            "currency": "COP",
            "opening_balance": "11000000",
        },
    )
    account_id = created.json()["id"]

    for value in ("15000000", "11000000"):
        client.post(
            f"/financial/accounts/{account_id}/value",
            json={"market_value": value},
        )

    response = client.post(
        f"/financial/accounts/{account_id}/value",
        json={"market_value": "15000000"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["balance"] == "15000000"
    assert body["posted"][0]["amount"] == "4000000"
    assert body["posted"][0]["direction"] == "incoming"


def test_a_debt_cannot_be_revalued(client: TestClient) -> None:
    account_id = _declare_mortgage(client)

    response = client.post(
        f"/financial/accounts/{account_id}/value",
        json={"market_value": "100"},
    )

    assert response.status_code == 400


def test_a_cdt_projects_what_it_will_be_worth_net_of_the_withholding(
    client: TestClient,
) -> None:
    created = client.post(
        "/financial/accounts",
        json={
            "name": "CDT",
            "kind": "investment",
            "currency": "COP",
            "opening_balance": "20000000",
        },
    )
    account_id = created.json()["id"]
    client.put(
        f"/financial/accounts/{account_id}/investment",
        json={
            "opened_on": "2026-01-10",
            "statement_day": 10,
            "rate": {"value": "0.105", "basis": "effective_annual"},
            "matures_on": "2026-04-10",
            "charges": [
                {
                    "name": "Retención en la fuente",
                    "basis": "earnings",
                    "rate": "0.04",
                },
            ],
            "accrue_from": "2026-01-10",
        },
    )

    response = client.get(
        f"/financial/accounts/{account_id}/financing",
        params={"periods": 12, "as_of": "2026-01-10"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schedule"] is None
    # Nothing past the vencimiento: the money stopped being invested.
    assert len(body["projection"]["periods"]) == 3
    assert body["projection"]["periods"][0]["earned"] == "167103.11"
    assert body["projection"]["periods"][0]["charges"][0]["amount"] == "6684.12"
    assert body["performance"]["contributed"] == "0"


def test_the_catalog_publishes_the_vocabularies_a_loan_form_needs(
    client: TestClient,
) -> None:
    catalog = client.get("/financial/catalog").json()

    assert {option["value"] for option in catalog["rate_bases"]} == {
        "effective_annual",
        "nominal_annual",
        "monthly",
    }
    assert {option["value"] for option in catalog["charge_bases"]} == {
        "fixed",
        "outstanding_balance",
        "original_principal",
        "insured_value",
        "earnings",
    }
    assert {option["value"] for option in catalog["amortization_styles"]} == {
        "french",
        "constant_principal",
        "interest_only",
    }
    assert {option["value"] for option in catalog["transaction_origins"]} == {
        "bank_alert",
        "manual",
        "accrual",
        "scheduled",
    }
