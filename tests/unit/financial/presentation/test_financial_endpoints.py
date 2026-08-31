"""The surface a frontend calls, exercised end to end over fakes."""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import MerchantAttribution
from personal_finance.contexts.financial.application.queries import (
    GetAccountUseCase,
    GetTransactionUseCase,
    ListAccountsUseCase,
    ListTransactionsUseCase,
    ReadFinancialHistoryUseCase,
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
    get_list_accounts_use_case,
    get_list_transactions_use_case,
    get_manage_accounts_use_case,
    get_manage_transactions_use_case,
    get_merchant_directory,
    get_read_history_use_case,
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

    def categories(self) -> frozenset[str]:
        return frozenset({"groceries", "transport", "subscriptions", "uncategorized"})


@pytest.fixture
def client() -> TestClient:
    app, _ = _build()

    return app


@pytest.fixture
def wired() -> tuple[TestClient, InMemoryLedger]:
    """The same app, plus the ledger behind it.

    Transfers reach the ledger from the bus and there is no endpoint that
    writes one, so a test about how the API *reads* them has to put the pair
    in place itself.
    """
    return _build()


def _build() -> tuple[TestClient, InMemoryLedger]:
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

    return TestClient(app), ledger


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
        "totals": [],
        "groups": [],
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
