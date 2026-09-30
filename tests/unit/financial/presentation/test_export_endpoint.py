"""Movements out as a CSV or a workbook: the same list the screen shows,
in a file no spreadsheet can turn against its owner.
"""

from collections.abc import Mapping, Sequence
import csv
from decimal import Decimal
import io
import re
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application import export as export_module
from personal_finance.contexts.financial.application.export import (
    ExportTransactionsUseCase,
)
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_export_transactions_use_case,
    get_merchant_directory,
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
STRANGER = UserId.from_string("22222222-2222-2222-2222-222222222222")

AUGUST_MIDDAY = 1_787_500_000  # 2026-08-23 10:46 Bogotá
AUGUST_LAST_NIGHT = 1_788_224_400  # 2026-08-31 20:00 Bogotá, already September UTC
JULY_MIDDAY = 1_784_900_000


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

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        del fingerprint

        return self.list_unassigned(user_id)

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


class InMemoryAccounts:
    def __init__(self) -> None:
        self.by_id: dict[AccountId, Account] = {}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(account_id)

        return account if account is not None and account.user_id == user_id else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        del user_id, fingerprint

        return None

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account for account in self.by_id.values() if account.user_id == user_id
        ]

    def save(self, account: Account) -> None:
        self.by_id[account.id] = account

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


class FakeDirectory:
    def __init__(self, known: Mapping[str, MerchantAttribution]) -> None:
        self._known = known

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

        # Shipped ones arrive with the API's English label, a custom one with
        # its owner's own name.
        return {
            "groceries": "Groceries",
            "transport": "Transport",
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
        del user_id, counterparty, category, occurred_at

        return None


EXITO = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Éxito",
    category="groceries",
    needs_review=False,
)
VET = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000002",
    display_name="Veterinaria Pelos",
    category="custom:gatos",
    needs_review=False,
)


@pytest.fixture
def ledger() -> InMemoryLedger:
    return InMemoryLedger()


@pytest.fixture
def accounts() -> InMemoryAccounts:
    return InMemoryAccounts()


@pytest.fixture
def client(ledger: InMemoryLedger, accounts: InMemoryAccounts) -> TestClient:
    directory = FakeDirectory({"COMPRA EXITO 123": EXITO, "VET PELOS": VET})
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_export_transactions_use_case] = lambda: (
        ExportTransactionsUseCase(
            ledger=ledger,
            accounts=accounts,
            merchants=directory,
            categories=directory,
        )
    )
    app.dependency_overrides[get_merchant_directory] = lambda: directory

    return TestClient(app)


def _spend(
    ledger: InMemoryLedger,
    *,
    counterparty: str,
    amount: str = "50000",
    when: int = AUGUST_MIDDAY,
    direction: MovementDirection = MovementDirection.OUTGOING,
    account_id: AccountId | None = None,
    user_id: UserId = USER_ID,
    note: str | None = None,
) -> Transaction:
    movement = Transaction.enter_manually(
        user_id=user_id,
        direction=direction,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(when),
        counterparty=counterparty,
        account_id=account_id,
        note=note,
    )
    ledger.save(movement)

    return movement


def _declare(accounts: InMemoryAccounts, name: str) -> Account:
    account = Account.open(
        user_id=USER_ID,
        name=name,
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(JULY_MIDDAY),
    )
    accounts.save(account)

    return account


def _csv(response_body: bytes) -> list[dict[str, str]]:
    text = response_body.decode("utf-8")
    assert text.startswith("﻿"), "Excel needs the BOM to read UTF-8"

    return list(csv.DictReader(io.StringIO(text.removeprefix("﻿"))))


# --------------------------------------------------------------------- CSV


def test_the_csv_names_what_a_file_cannot_look_up_later(
    client: TestClient,
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    savings = _declare(accounts, "Ahorros Bancolombia")
    _spend(
        ledger,
        counterparty="COMPRA EXITO 123",
        amount="84300.50",
        account_id=savings.id,
        note="mercado del mes",
    )

    response = client.get("/financial/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    [row] = _csv(response.content)
    assert row == {
        "Fecha": "2026-08-23 10:46",
        "Tipo": "Gasto",
        "Monto": "84300.50",
        "Moneda": "COP",
        "Comercio": "Éxito",
        "Categoría": "Mercado",
        "Texto del banco": "COMPRA EXITO 123",
        "Banco": "",
        "Cuenta": "Ahorros Bancolombia",
        "Origen": "Manual",
        "Nota": "mercado del mes",
        "ID": row["ID"],
    }
    assert row["ID"]


def test_a_custom_category_keeps_the_name_its_owner_gave_it(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="VET PELOS")

    [row] = _csv(client.get("/financial/export").content)

    assert row["Categoría"] == "Gatos"


def test_a_movement_no_merchant_owns_is_exported_with_blank_merchant_cells(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="ALGO QUE NADIE VIO")

    [row] = _csv(client.get("/financial/export").content)

    assert row["Comercio"] == ""
    assert row["Categoría"] == ""
    assert row["Texto del banco"] == "ALGO QUE NADIE VIO"


def test_dates_are_written_in_the_owners_timezone_not_in_utc(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="COMPRA EXITO 123", when=AUGUST_LAST_NIGHT)

    [row] = _csv(client.get("/financial/export").content)

    assert row["Fecha"] == "2026-08-31 20:00"


def test_income_and_transfers_are_never_called_spending(
    client: TestClient,
    ledger: InMemoryLedger,
    accounts: InMemoryAccounts,
) -> None:
    savings = _declare(accounts, "Ahorros")
    _spend(ledger, counterparty="NOMINA", direction=MovementDirection.INCOMING)
    paid = _spend(
        ledger,
        counterparty="PAGO TARJETA OTRO BANCO",
        when=JULY_MIDDAY,
        account_id=savings.id,
    )
    paid.declare_transfer()

    rows = _csv(client.get("/financial/export").content)

    assert [row["Tipo"] for row in rows] == ["Ingreso", "Traslado (sale)"]


def test_rows_come_newest_first_and_all_of_them_not_a_page(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    for index in range(260):
        _spend(ledger, counterparty=f"COMPRA {index}", when=JULY_MIDDAY + index * 60)

    rows = _csv(client.get("/financial/export").content)

    assert len(rows) == 260
    assert rows[0]["Texto del banco"] == "COMPRA 259"
    assert rows[-1]["Texto del banco"] == "COMPRA 0"


@pytest.mark.parametrize(
    "hostile",
    ['=HYPERLINK("http://evil","x")', "+57 300", "-2+3", "@SUM(A1)"],
)
def test_text_a_spreadsheet_would_evaluate_is_written_to_be_displayed(
    client: TestClient,
    ledger: InMemoryLedger,
    hostile: str,
) -> None:
    _spend(ledger, counterparty=hostile, note=hostile)

    [row] = _csv(client.get("/financial/export").content)

    assert row["Texto del banco"] == "'" + hostile
    assert row["Nota"] == "'" + hostile


def test_the_amount_is_a_number_even_though_it_could_start_with_a_digit(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="COMPRA EXITO 123", amount="1000000")

    [row] = _csv(client.get("/financial/export").content)

    assert row["Monto"] == "1000000"


# ------------------------------------------------------------------ filters


def test_the_same_filters_as_the_list_narrow_the_file(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="COMPRA EXITO 123")
    _spend(ledger, counterparty="VET PELOS")
    _spend(ledger, counterparty="NOMINA", direction=MovementDirection.INCOMING)

    spending = _csv(
        client.get("/financial/export", params={"direction": "outgoing"}).content,
    )
    groceries = _csv(
        client.get("/financial/export", params={"category": "groceries"}).content,
    )

    assert {row["Texto del banco"] for row in spending} == {
        "COMPRA EXITO 123",
        "VET PELOS",
    }
    assert [row["Texto del banco"] for row in groceries] == ["COMPRA EXITO 123"]


def test_the_period_is_half_open_like_the_list(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="JULIO", when=JULY_MIDDAY)
    _spend(ledger, counterparty="AGOSTO", when=AUGUST_MIDDAY)

    rows = _csv(
        client.get(
            "/financial/export",
            params={"from": AUGUST_MIDDAY, "to": AUGUST_MIDDAY + 1},
        ).content,
    )

    assert [row["Texto del banco"] for row in rows] == ["AGOSTO"]


def test_nobody_elses_movements_reach_the_file(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="MIO")
    _spend(ledger, counterparty="AJENO", user_id=STRANGER)

    rows = _csv(client.get("/financial/export").content)

    assert [row["Texto del banco"] for row in rows] == ["MIO"]


def test_somebody_with_no_movements_gets_a_file_with_only_the_header(
    client: TestClient,
) -> None:
    response = client.get("/financial/export")

    assert response.status_code == 200
    assert _csv(response.content) == []
    assert response.content.decode("utf-8").startswith("﻿Fecha,Tipo,Monto")


def test_an_unknown_category_is_refused_rather_than_exporting_nothing(
    client: TestClient,
) -> None:
    response = client.get("/financial/export", params={"category": "nope"})

    assert response.status_code == 422
    # Not the ceiling's refusal: a client must not ask for fewer dates here.
    assert response.json()["detail"] == "Unknown category: 'nope'"


def test_an_unknown_timezone_is_refused(client: TestClient) -> None:
    response = client.get("/financial/export", params={"timezone": "Mars/Base"})

    assert response.status_code == 400


def test_an_unknown_format_is_refused(client: TestClient) -> None:
    response = client.get("/financial/export", params={"format": "pdf"})

    assert response.status_code == 422


def test_past_the_ceiling_the_export_is_refused_rather_than_cut(
    client: TestClient,
    ledger: InMemoryLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_module, "MAX_EXPORT_ROWS", 3)

    for index in range(4):
        _spend(ledger, counterparty=f"COMPRA {index}", when=JULY_MIDDAY + index)

    response = client.get("/financial/export")

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "export_too_large"
    assert detail["matched"] == 4
    assert detail["limit"] == 3


def test_exactly_at_the_ceiling_the_export_goes_through(
    client: TestClient,
    ledger: InMemoryLedger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_module, "MAX_EXPORT_ROWS", 3)

    for index in range(3):
        _spend(ledger, counterparty=f"COMPRA {index}", when=JULY_MIDDAY + index)

    response = client.get("/financial/export")

    assert response.status_code == 200
    assert len(_csv(response.content)) == 3


def test_the_file_is_an_attachment_nobody_in_between_may_keep(
    client: TestClient,
) -> None:
    response = client.get("/financial/export", params={"format": "xlsx"})

    assert response.headers["cache-control"] == "no-store"
    assert re.fullmatch(
        r'attachment; filename="finflow-movimientos-\d{4}-\d{2}-\d{2}\.xlsx"',
        response.headers["content-disposition"],
    )


# -------------------------------------------------------------------- XLSX


def _sheet(response_body: bytes) -> tuple[str, str]:
    with zipfile.ZipFile(io.BytesIO(response_body)) as archive:
        return (
            archive.read("xl/worksheets/sheet1.xml").decode("utf-8"),
            archive.read("xl/sharedStrings.xml").decode("utf-8"),
        )


def test_the_workbook_is_a_real_xlsx_with_the_amount_as_an_exact_number(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty="COMPRA EXITO 123", amount="84300.55")

    response = client.get("/financial/export", params={"format": "xlsx"})

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    sheet, strings = _sheet(response.content)
    assert "<v>84300.55</v>" in sheet
    assert "Éxito" in strings
    assert "Mercado" in strings


def test_the_workbook_never_writes_a_formula_whatever_the_bank_wrote(
    client: TestClient,
    ledger: InMemoryLedger,
) -> None:
    _spend(ledger, counterparty='=HYPERLINK("http://evil","x")', note="=1+1")

    sheet, strings = _sheet(
        client.get("/financial/export", params={"format": "xlsx"}).content,
    )

    assert "<f>" not in sheet
    assert "HYPERLINK" in strings
