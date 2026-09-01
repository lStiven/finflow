"""Drive two users end to end and check that nothing of one reaches the other.

    just verify                        # against the local emulator
    just verify .env.development       # against the dev account

This is the answer to "does the data actually hold together?". It walks the
whole chain for two people who bank at the same institution — register, approve
the bank's sender, forward alerts, drain the three workers, declare the
accounts that retroactively adopt those alerts, rename and recategorize a
merchant, enter what never emailed — and then asserts two families of
properties over the result:

**Integrity.** A balance equals the movements behind it. Net worth equals
assets minus liabilities. A movement assigned to an account matches one of the
fingerprints that account answers to. The summary's buckets add up to its
totals. Every record names the user it belongs to.

**Isolation.** Neither user can read the other's accounts, movements or
notifications — by listing, by searching, or by asking for a known id
directly, which must answer 404 rather than 403: whether somebody else's
account exists is not something this API tells a stranger.

Alerts enter through ingestion's own use case rather than the local-only
webhook, because that webhook is unmounted anywhere but `ENVIRONMENT=local`
and this has to run against a real environment. It is the same use case the
ingest worker calls after reading a message off IMAP.

Re-running is safe: the alerts carry fixed message ids and are deduplicated,
and accounts are looked up before they are declared.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime
from decimal import Decimal
import os
import sys
from typing import Protocol, cast
from zoneinfo import ZoneInfo

from fastapi import status
from fastapi.testclient import TestClient
import httpx2

from personal_finance.api.main import create_app
from personal_finance.contexts.financial.presentation.cli.run_financial_worker import (
    build_worker as build_financial_worker,
)
from personal_finance.contexts.identity.presentation.cli.verification_tickets import (
    issue_registration_ticket,
)
from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.contexts.ingestion.presentation.cli.run_parse_worker import (
    build_worker as build_parse_worker,
)
from personal_finance.contexts.ingestion.presentation.http.router import (
    get_use_case as get_intake_use_case,
)
from personal_finance.contexts.merchant.presentation.cli.run_merchant_worker import (
    build_worker as build_merchant_worker,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.config.settings import (
    Environment,
    get_aws_settings,
    get_financial_settings,
    get_identity_settings,
    get_ingestion_settings,
    get_merchant_settings,
)


# JSON off the wire. `object` rather than `Any` so every field still has to
# be narrowed where it is read.
type Json = dict[str, object]


BANK = "Bancolombia"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"

BOGOTA = ZoneInfo("America/Bogota")
MAX_POLLS = 30

PASSWORD = "una frase larga de verdad"


def _local(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BOGOTA)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Alert:
    message_id: str
    subject: str
    body: str
    received_at: datetime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Account:
    name: str
    kind: str
    opening_balance: str | None = None
    bank: str | None = None
    instrument_kind: str | None = None
    last_four: str | None = None
    also_answers_to: tuple[tuple[str, str], ...] = ()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Entry:
    direction: str
    amount: str
    counterparty: str
    occurred_at: datetime
    account_name: str | None
    note: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Person:
    """One user and everything that should end up belonging to them."""

    label: str
    email: str
    alerts: tuple[Alert, ...]
    accounts: tuple[Account, ...]
    entries: tuple[Entry, ...]
    # (what the bank writes, what the user renames it to, the category)
    merchant_edit: tuple[str, str, str]


# Two people at the same bank, deliberately: different cards, overlapping
# merchants. Sharing `EXITO` and `RAPPI` is what makes the merchant questions
# answerable at all — with disjoint counterparties, isolation would look
# perfect for the wrong reason.
ANA = Person(
    label="Ana",
    email="ana@finflow.local",
    merchant_edit=("EXITO SUPERINTER CALI", "Éxito", "groceries"),
    alerts=(
        # No last four anywhere in this template — the bank names the account
        # type and nothing else — so it lands unassigned and is placed by hand
        # further down. That is the documented behaviour, not a gap.
        Alert(
            message_id="<verify-ana-payroll@finflow.local>",
            subject="Bancolombia le informa",
            body=(
                "Bancolombia: Recibiste un pago de NOMINA de ACME SAS por "
                "$4.500.000 en tu cuenta de AHORROS el 03/08/2026 a las 07:30"
            ),
            received_at=_local(2026, 8, 3, 7, 31),
        ),
        Alert(
            message_id="<verify-ana-market@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $45.000 en EXITO SUPERINTER CALI con "
                "tu T.Cred *1234, el 05/08/2026 a las 10:15"
            ),
            received_at=_local(2026, 8, 5, 10, 16),
        ),
        Alert(
            message_id="<verify-ana-market2@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $67.300 en EXITO EXPRESS POBLADO con "
                "tu T.Cred *1234, el 09/08/2026 a las 19:02"
            ),
            received_at=_local(2026, 8, 9, 19, 3),
        ),
        Alert(
            message_id="<verify-ana-delivery@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $23.900 en RAPPI COLOMBIA con tu "
                "T.Cred *1234, el 11/08/2026 a las 20:42"
            ),
            received_at=_local(2026, 8, 11, 20, 43),
        ),
        Alert(
            message_id="<verify-ana-pharmacy@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $18.500 en DROGUERIA LA REBAJA con tu "
                "T.Deb *5261, el 14/08/2026 a las 09:05"
            ),
            received_at=_local(2026, 8, 14, 9, 6),
        ),
        Alert(
            message_id="<verify-ana-qr@finflow.local>",
            subject="Pago por QR",
            body=(
                "Bancolombia: Ana pagaste $12.000 por codigo QR desde tu "
                "cuenta *5261 a la llave 3001234567 el 17/08/2026 a las 13:20"
            ),
            received_at=_local(2026, 8, 17, 13, 21),
        ),
        Alert(
            message_id="<verify-ana-transfer@finflow.local>",
            subject="Transferencia realizada",
            body=(
                "Bancolombia: Transferiste $250.000 desde tu cuenta *5261 a "
                "la cuenta *9988 el 22/08/2026 a las 08:00"
            ),
            received_at=_local(2026, 8, 22, 8, 1),
        ),
    ),
    accounts=(
        Account(
            name="Tarjeta Ana",
            kind="credit_card",
            bank=BANK,
            instrument_kind="credit_card",
            last_four="1234",
        ),
        Account(
            name="Ahorros Ana",
            kind="savings",
            opening_balance="1200000",
            bank=BANK,
            # One real account, two names its alerts arrive under.
            instrument_kind="account",
            last_four="5261",
            also_answers_to=(("debit_card", "5261"),),
        ),
        Account(name="Efectivo Ana", kind="cash", opening_balance="200000"),
    ),
    entries=(
        Entry(
            direction="outgoing",
            amount="89900",
            counterparty="NETFLIX",
            occurred_at=_local(2026, 8, 23, 9, 0),
            account_name="Tarjeta Ana",
            note="cobro automatico, sin correo",
        ),
    ),
)

BRUNO = Person(
    label="Bruno",
    email="bruno@finflow.local",
    merchant_edit=("RAPPI COLOMBIA", "Rappi", "restaurants"),
    alerts=(
        Alert(
            message_id="<verify-bruno-payroll@finflow.local>",
            subject="Bancolombia le informa",
            body=(
                "Bancolombia: Recibiste un pago de NOMINA de OTRA EMPRESA SAS "
                "por $2.800.000 en tu cuenta de AHORROS el 04/08/2026 a las 06:10"
            ),
            received_at=_local(2026, 8, 4, 6, 11),
        ),
        Alert(
            message_id="<verify-bruno-market@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $32.000 en EXITO SUPERINTER BOGOTA con "
                "tu T.Cred *7788, el 06/08/2026 a las 11:40"
            ),
            received_at=_local(2026, 8, 6, 11, 41),
        ),
        Alert(
            message_id="<verify-bruno-delivery@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $54.900 en RAPPI COLOMBIA con tu "
                "T.Cred *7788, el 08/08/2026 a las 21:05"
            ),
            received_at=_local(2026, 8, 8, 21, 6),
        ),
        Alert(
            message_id="<verify-bruno-pharmacy@finflow.local>",
            subject="Notificación de compra",
            body=(
                "Bancolombia: Compraste $15.000 en FARMATODO CHICO con tu "
                "T.Deb *4410, el 12/08/2026 a las 08:30"
            ),
            received_at=_local(2026, 8, 12, 8, 31),
        ),
        Alert(
            message_id="<verify-bruno-qr@finflow.local>",
            subject="Pago por QR",
            body=(
                "Bancolombia: Bruno pagaste $8.500 por codigo QR desde tu "
                "cuenta *4410 a la llave 3009876543 el 16/08/2026 a las 12:05"
            ),
            received_at=_local(2026, 8, 16, 12, 6),
        ),
        Alert(
            message_id="<verify-bruno-transfer@finflow.local>",
            subject="Transferencia realizada",
            body=(
                "Bancolombia: Transferiste $120.000 desde tu cuenta *4410 a "
                "la cuenta *1122 el 20/08/2026 a las 17:45"
            ),
            received_at=_local(2026, 8, 20, 17, 46),
        ),
    ),
    accounts=(
        Account(
            name="Tarjeta Bruno",
            kind="credit_card",
            bank=BANK,
            instrument_kind="credit_card",
            last_four="7788",
        ),
        Account(
            name="Ahorros Bruno",
            kind="savings",
            opening_balance="800000",
            bank=BANK,
            instrument_kind="account",
            last_four="4410",
            also_answers_to=(("debit_card", "4410"),),
        ),
    ),
    entries=(
        Entry(
            direction="outgoing",
            amount="42000",
            counterparty="GASOLINA",
            occurred_at=_local(2026, 8, 24, 7, 20),
            account_name="Ahorros Bruno",
            note="efectivo, sin correo",
        ),
    ),
)

PEOPLE = (ANA, BRUNO)


class PollResult(Protocol):
    @property
    def received(self) -> int: ...

    @property
    def handled(self) -> int: ...

    @property
    def rejected(self) -> int: ...


class Worker(Protocol):
    def poll_once(self, *, wait_seconds: int = 20) -> PollResult: ...


class Report:
    """Every assertion this run made, and whether it held.

    Collected rather than raised, so one failure does not hide the twenty
    checks after it — the point is to see the whole picture in one pass.
    """

    def __init__(self) -> None:
        self.checks: list[tuple[bool, str, str]] = []

    def check(self, passed: bool, what: str, detail: str = "") -> bool:
        self.checks.append((passed, what, detail))

        return passed

    def equal(self, actual: object, expected: object, what: str) -> bool:
        return self.check(
            actual == expected,
            what,
            "" if actual == expected else f"got {actual!r}, expected {expected!r}",
        )

    @property
    def failures(self) -> list[tuple[bool, str, str]]:
        return [entry for entry in self.checks if not entry[0]]

    def render(self) -> None:
        for passed, what, detail in self.checks:
            mark = "ok  " if passed else "FAIL"
            suffix = f"  — {detail}" if detail else ""
            print(f"  [{mark}] {what}{suffix}")


@dataclasses.dataclass(slots=True, kw_only=True)
class Session:
    """One authenticated user, plus what the run learned about them."""

    person: Person
    token: str
    user_id: str
    address: str
    accounts: dict[str, str] = dataclasses.field(default_factory=dict[str, str])

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _expect(response: httpx2.Response, *expected: int) -> httpx2.Response:
    if response.status_code not in expected:
        raise SystemExit(
            f"{response.request.method} {response.request.url} answered "
            f"{response.status_code}, expected {expected}: {response.text}",
        )

    return response


def _build_client() -> TestClient:
    """The real application, driven in process.

    `expose_local_only_routes=False` on purpose: the bank-notification webhook
    is a local testing seam and this has to work against a real environment,
    so alerts go in through ingestion's use case instead — the same one the
    ingest worker calls.
    """
    return TestClient(create_app(expose_local_only_routes=False))


def _register(client: TestClient, person: Person) -> Session:
    response = client.post(
        "/identity/register",
        json={
            "email": person.email,
            "password": PASSWORD,
            # Nobody reads these addresses, so the ticket is written straight
            # into the table. See `scripts/registration.py`.
            "verification_token": issue_registration_ticket(person.email),
            "allowed_domains": [BANK_DOMAIN],
        },
    )

    if response.status_code == status.HTTP_409_CONFLICT:
        response = _expect(
            client.post(
                "/identity/login",
                json={"email": person.email, "password": PASSWORD},
            ),
            status.HTTP_200_OK,
        )

    else:
        _expect(response, status.HTTP_201_CREATED)

    token = str(response.json()["access_token"])
    headers = {"Authorization": f"Bearer {token}"}
    me = _expect(client.get("/identity/me", headers=headers), status.HTTP_200_OK)
    # Approved on every run, not only after a registration: an inbox that
    # predates this run may approve nobody, and then every alert below would be
    # dropped exactly as the filter is meant to drop them.
    inbox = _expect(
        client.patch(
            "/identity/inbox",
            json={"allowed_domains": [BANK_DOMAIN], "allowed_addresses": []},
            headers=headers,
        ),
        status.HTTP_200_OK,
    )

    return Session(
        person=person,
        token=token,
        user_id=str(me.json()["user_id"]),
        address=str(inbox.json()["address"]),
    )


def _forward(session: Session) -> None:
    """Hand the alerts to ingestion the way the ingest worker does."""
    use_case = get_intake_use_case()

    for alert in session.person.alerts:
        use_case.execute(
            ReceiveBankNotificationCommand(
                recipient=EmailAddress(session.address),
                message_id=EmailMessageId(alert.message_id),
                sender=EmailAddress(BANK_SENDER),
                subject=alert.subject,
                raw_content=alert.body,
                received_at=PosixTime.from_datetime(alert.received_at),
            ),
        )


def _drain(worker: Worker, *, label: str) -> str:
    handled = 0
    rejected = 0

    for _ in range(MAX_POLLS):
        result = worker.poll_once(wait_seconds=0)

        if not result.received:
            break

        handled += result.handled
        rejected += result.rejected
    else:
        return f"{label}: still had messages after {MAX_POLLS} polls"

    return f"{label}: {handled} handled" + (
        f", {rejected} rejected" if rejected else ""
    )


def _drain_workers() -> list[str]:
    # Parse first; merchant and financial both subscribe to what it publishes,
    # on their own queues, so neither waits for the other.
    return [
        _drain(build_parse_worker(), label="parse"),
        _drain(build_merchant_worker(), label="merchant"),
        _drain(build_financial_worker(), label="financial"),
    ]


def _declare_accounts(client: TestClient, session: Session) -> None:
    """Declare after the alerts arrived, so adoption is retroactive.

    That order is the point: the movements are already in the ledger,
    unassigned, and declaring the account is what claims them.
    """
    existing = {
        str(account["name"]): str(account["id"])
        for account in _expect(
            client.get(
                "/financial/accounts",
                params={"scope": "all"},
                headers=session.headers,
            ),
            status.HTTP_200_OK,
        ).json()["accounts"]
    }

    for account in session.person.accounts:
        if account.name in existing:
            session.accounts[account.name] = existing[account.name]

            continue

        created = _expect(
            client.post(
                "/financial/accounts",
                json={
                    "name": account.name,
                    "kind": account.kind,
                    "currency": "COP",
                    "opening_balance": account.opening_balance,
                    "bank": account.bank,
                    "instrument_kind": account.instrument_kind,
                    "last_four": account.last_four,
                },
                headers=session.headers,
            ),
            status.HTTP_201_CREATED,
        ).json()
        session.accounts[account.name] = str(created["id"])

        for instrument_kind, last_four in account.also_answers_to:
            _expect(
                client.post(
                    f"/financial/accounts/{created['id']}/instruments",
                    json={
                        "bank": account.bank,
                        "instrument_kind": instrument_kind,
                        "last_four": last_four,
                    },
                    headers=session.headers,
                ),
                status.HTTP_200_OK,
            )


def _assign_incoming(client: TestClient, session: Session) -> str | None:
    """Place the payroll alert on the savings account by hand.

    Its template names the account type and no digits, so nothing can match it
    automatically. Somebody doing this in the app is the intended path, and it
    is the one that has to move a balance upward.
    """
    savings = next(
        (name for name in session.accounts if name.startswith("Ahorros")),
        None,
    )

    if savings is None:
        return None

    unassigned = _expect(
        client.get(
            "/financial/transactions",
            params={"unassigned": True, "limit": 200},
            headers=session.headers,
        ),
        status.HTTP_200_OK,
    ).json()["transactions"]
    incoming = next(
        (row for row in unassigned if row["direction"] == "incoming"),
        None,
    )

    if incoming is None:
        return None

    _expect(
        client.patch(
            f"/financial/transactions/{incoming['id']}",
            json={"account_id": session.accounts[savings]},
            headers=session.headers,
        ),
        status.HTTP_200_OK,
    )

    return str(incoming["id"])


def _enter_manually(client: TestClient, session: Session) -> None:
    """Money that never emailed. Looked up first: a manual entry's identity is
    random, so a second run would otherwise record it twice.
    """
    existing = {
        str(row["counterparty"])
        for row in _expect(
            client.get(
                "/financial/transactions",
                params={"origin": "manual", "limit": 200},
                headers=session.headers,
            ),
            status.HTTP_200_OK,
        ).json()["transactions"]
    }

    for entry in session.person.entries:
        if entry.counterparty in existing:
            continue

        _expect(
            client.post(
                "/financial/transactions",
                json={
                    "direction": entry.direction,
                    "amount": entry.amount,
                    "currency": "COP",
                    "occurred_at": int(entry.occurred_at.timestamp()),
                    "counterparty": entry.counterparty,
                    "account_id": (
                        session.accounts.get(entry.account_name)
                        if entry.account_name
                        else None
                    ),
                    "note": entry.note,
                },
                headers=session.headers,
            ),
            status.HTTP_201_CREATED,
        )


def _edit_merchant(client: TestClient, session: Session) -> str | None:
    """Rename and recategorize the merchant behind one of the alerts.

    A user's correction, which is what the merchant context exists for — and
    what the read-time join then has to reflect on every past movement.
    """
    spelling, display_name, category = session.person.merchant_edit
    found = _expect(
        client.get(
            "/merchants",
            params={"search": spelling, "limit": 5},
            headers=session.headers,
        ),
        status.HTTP_200_OK,
    ).json()["merchants"]

    if not found:
        return None

    merchant_id = str(found[0]["id"])
    _expect(
        client.patch(
            f"/merchants/{merchant_id}",
            json={"display_name": display_name, "category": category},
            headers=session.headers,
        ),
        status.HTTP_200_OK,
    )

    return merchant_id


def _json(response: httpx2.Response) -> Json:
    """The one place a response stops being untyped."""
    return cast("Json", response.json())


def _rows(payload: Json, key: str) -> list[Json]:
    """The list under `key`, narrowed once so nothing below reads `object`."""
    rows = payload.get(key)

    if not isinstance(rows, list):
        raise SystemExit(f"expected a list under {key!r}, got {rows!r}")

    return cast("list[Json]", rows)


def _text(row: Json, key: str) -> str:
    return str(row.get(key))


def _money(row: Json, key: str) -> Decimal:
    return Decimal(_text(row, key))


def _strings(row: Json, key: str) -> list[str]:
    value = row.get(key)

    if not isinstance(value, list):
        return []

    return [str(item) for item in cast("list[object]", value)]


def _attribution_is_whole(row: Json) -> bool:
    """A movement either names no merchant or names one completely."""
    merchant = row.get("merchant")

    if merchant is None:
        return True

    return isinstance(merchant, dict) and bool(cast("Json", merchant).get("id"))


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountsView:
    accounts: list[Json]
    net_worth: list[Json]


def _movements(client: TestClient, session: Session) -> list[Json]:
    return _rows(
        _json(
            _expect(
                client.get(
                    "/financial/transactions",
                    params={"limit": 200},
                    headers=session.headers,
                ),
                status.HTTP_200_OK,
            ),
        ),
        "transactions",
    )


def _accounts(client: TestClient, session: Session) -> AccountsView:
    payload = _json(
        _expect(
            client.get(
                "/financial/accounts",
                params={"scope": "all"},
                headers=session.headers,
            ),
            status.HTTP_200_OK,
        ),
    )

    return AccountsView(
        accounts=_rows(payload, "accounts"),
        net_worth=_rows(payload, "net_worth"),
    )


def _check_integrity(
    client: TestClient,
    session: Session,
    report: Report,
    *,
    incoming_id: str | None,
) -> None:
    """Does the data hold together for one user, on its own terms?"""
    label = session.person.label
    view = _accounts(client, session)
    accounts = view.accounts
    movements = _movements(client, session)

    report.equal(
        len(movements),
        len(session.person.alerts) + len(session.person.entries),
        f"{label}: every alert and manual entry became exactly one movement",
    )
    report.check(
        all(_attribution_is_whole(row) for row in movements),
        f"{label}: every attribution names a merchant",
    )

    # The balance a movement produces depends on the account's side: spending
    # on a credit card raises what it holds, because what it holds is debt.
    by_account: dict[str, Decimal] = {}

    for row in movements:
        account_id = row["account_id"]

        if account_id is None:
            continue

        amount = _money(row, "amount")
        by_account[str(account_id)] = by_account.get(str(account_id), Decimal(0)) + (
            amount if row["direction"] == "incoming" else -amount
        )

    for account in accounts:
        account_id = str(account["id"])
        opening = _money(account, "opening_balance")
        signed_movements = by_account.get(account_id, Decimal(0))
        # An asset's balance moves with the money; a liability's moves against
        # it, which is what makes a purchase raise what a card owes.
        direction = 1 if account["category"] == "asset" else -1
        expected = opening + direction * signed_movements

        report.equal(
            _money(account, "balance"),
            expected,
            f"{label}: {account['name']} balance equals the movements behind it",
        )
        report.equal(
            account["movements_applied"],
            sum(1 for row in movements if row["account_id"] == account_id),
            f"{label}: {account['name']} counts the movements it holds",
        )

    # An assigned movement must have landed on an account that answers to its
    # bank and instrument, not on whichever one happened to be created first.
    instruments = {
        _text(account, "id"): _strings(account, "instruments") for account in accounts
    }

    for row in movements:
        if row["account_id"] is None or row["origin"] != "bank_alert":
            continue

        report.check(
            bool(instruments.get(str(row["account_id"]))),
            f"{label}: alert for {row['counterparty']} sits on an account with "
            "instruments",
        )

    net_worth = view.net_worth
    assets = sum(
        _money(account, "balance")
        for account in accounts
        if account["category"] == "asset"
    )
    liabilities = sum(
        _money(account, "balance")
        for account in accounts
        if account["category"] == "liability"
    )

    if report.check(len(net_worth) == 1, f"{label}: one net-worth figure (COP)"):
        report.equal(
            _money(net_worth[0], "total"),
            assets - liabilities,
            f"{label}: net worth is assets minus liabilities",
        )

    if incoming_id is not None:
        placed = _expect(
            client.get(
                f"/financial/transactions/{incoming_id}",
                headers=session.headers,
            ),
            status.HTTP_200_OK,
        ).json()
        report.equal(
            placed["status"],
            "assigned",
            f"{label}: the incoming payroll was placed on an account",
        )
        report.equal(
            placed["direction"],
            "incoming",
            f"{label}: the payroll is money coming in",
        )

    # The summary is a second computation over the same rows; if it disagrees
    # with them, one of the two is wrong.
    summary = _expect(
        client.get(
            "/financial/summary",
            params={"group_by": "month"},
            headers=session.headers,
        ),
        status.HTTP_200_OK,
    ).json()
    outgoing = sum(
        _money(row, "amount") for row in movements if row["direction"] == "outgoing"
    )
    report.equal(
        Decimal(str(summary["totals"][0]["outgoing"])),
        outgoing,
        f"{label}: the summary totals what the ledger holds",
    )
    report.equal(
        sum(Decimal(str(g["totals"][0]["outgoing"])) for g in summary["groups"]),
        outgoing,
        f"{label}: the summary's buckets add up to its total",
    )


def _check_isolation(
    client: TestClient,
    mine: Session,
    theirs: Session,
    report: Report,
) -> None:
    """Can one user reach anything of the other's?

    Missing rather than forbidden is the expected answer throughout: whether
    somebody else's account exists is not something this API tells a stranger,
    so a 403 here would itself be the leak.
    """
    label = f"{mine.person.label} vs {theirs.person.label}"

    their_movements = _movements(client, theirs)
    their_accounts = _accounts(client, theirs).accounts
    my_movements = _movements(client, mine)
    my_accounts = _accounts(client, mine).accounts

    my_movement_ids = {str(row["id"]) for row in my_movements}
    their_movement_ids = {str(row["id"]) for row in their_movements}
    my_account_ids = {str(account["id"]) for account in my_accounts}
    their_account_ids = {str(account["id"]) for account in their_accounts}

    report.check(
        not (my_movement_ids & their_movement_ids),
        f"{label}: the two ledgers share no movement",
    )
    report.check(
        not (my_account_ids & their_account_ids),
        f"{label}: the two share no account",
    )
    report.check(
        all(
            str(row["account_id"]) in my_account_ids | {"None"} for row in my_movements
        ),
        f"{label}: every movement sits on an account its own owner declared",
    )

    # Asking for a known id directly, which is the interesting case: listing
    # could be scoped correctly while a detail read is not.
    for account_id in sorted(their_account_ids):
        report.equal(
            client.get(
                f"/financial/accounts/{account_id}",
                headers=mine.headers,
            ).status_code,
            status.HTTP_404_NOT_FOUND,
            f"{label}: their account {account_id[:8]} reads as missing",
        )

    for movement_id in sorted(their_movement_ids):
        report.equal(
            client.get(
                f"/financial/transactions/{movement_id}",
                headers=mine.headers,
            ).status_code,
            status.HTTP_404_NOT_FOUND,
            f"{label}: their movement {movement_id[:8]} reads as missing",
        )

    # And that a write is refused too — reading being scoped says nothing
    # about whether somebody can edit what they cannot see.
    for movement_id in sorted(their_movement_ids)[:1]:
        report.equal(
            client.patch(
                f"/financial/transactions/{movement_id}",
                json={"note": "escrito por otra persona"},
                headers=mine.headers,
            ).status_code,
            status.HTTP_404_NOT_FOUND,
            f"{label}: their movement cannot be edited",
        )

    for account_id in sorted(their_account_ids)[:1]:
        report.equal(
            client.patch(
                f"/financial/accounts/{account_id}",
                json={"name": "renombrada por otra persona"},
                headers=mine.headers,
            ).status_code,
            status.HTTP_404_NOT_FOUND,
            f"{label}: their account cannot be renamed",
        )

    # Searching is the other way a list can leak: the filter runs over what was
    # loaded, so a repository scoped wrongly would surface here.
    their_counterparties = {str(row["counterparty"]) for row in their_movements}

    for counterparty in sorted(
        their_counterparties - {str(row["counterparty"]) for row in my_movements}
    ):
        found = _expect(
            client.get(
                "/financial/transactions",
                params={"search": counterparty, "limit": 200},
                headers=mine.headers,
            ),
            status.HTTP_200_OK,
        ).json()
        report.equal(
            found["total"],
            0,
            f"{label}: searching {counterparty!r} finds nothing of theirs",
        )

    # Notifications carry sender, subject and status for somebody's bank mail.
    their_notifications = _expect(
        client.get(
            "/ingestion/notifications",
            params={"limit": 200},
            headers=theirs.headers,
        ),
        status.HTTP_200_OK,
    ).json()
    mine_notifications = _expect(
        client.get(
            "/ingestion/notifications",
            params={"limit": 200},
            headers=mine.headers,
        ),
        status.HTTP_200_OK,
    ).json()
    their_ids = {str(row["id"]) for row in their_notifications["notifications"]}
    my_ids = {str(row["id"]) for row in mine_notifications["notifications"]}
    report.check(
        not (their_ids & my_ids),
        f"{label}: the two see different notifications",
    )
    report.equal(
        mine_notifications["total"],
        len(mine.person.alerts),
        f"{label}: {mine.person.label} sees only their own alerts",
    )

    # The forwarding address is what attributes an email to a person; two
    # users sharing one would put somebody's bank mail in the wrong ledger.
    report.check(
        mine.address != theirs.address,
        f"{label}: the two forward to different addresses",
    )
    report.check(
        mine.user_id != theirs.user_id,
        f"{label}: the two are different users",
    )


def _describe_merchants(client: TestClient, sessions: list[Session]) -> list[str]:
    """What each user sees in the merchant context, reported rather than
    asserted: whether merchants are shared is a product decision, not an
    invariant this script gets to pick.
    """
    lines: list[str] = []

    for session in sessions:
        page = _expect(
            client.get(
                "/merchants",
                params={"limit": 200, "sort": "name"},
                headers=session.headers,
            ),
            status.HTTP_200_OK,
        ).json()
        names = ", ".join(str(row["display_name"]) for row in page["merchants"])
        lines.append(f"{session.person.label}: {page['total']} — {names}")

    return lines


def _refuse_production(*, override: bool) -> None:
    """Never register these users in production without being told twice.

    The two accounts below are created with a password written into a tracked
    file, so pointing this at production would put a principal with a
    published credential in the real users table — and 13 synthetic
    notifications and fifteen ledger rows beside somebody's real history,
    which nothing here can delete afterwards. `just verify .env.production` is
    one word away from `just verify .env.development`, which is exactly the
    kind of mistake worth making impossible rather than unlikely.
    """
    if get_aws_settings().environment is not Environment.PRODUCTION or override:
        return

    raise SystemExit(
        "Refusing to run against ENVIRONMENT=production: this registers two "
        "users with a password committed to this repository and writes "
        "synthetic movements that cannot be deleted. Point it at "
        ".env.development, or pass --yes-really-production if you mean it.",
    )


def _environment_banner() -> None:
    aws = get_aws_settings()
    ingestion = get_ingestion_settings()
    endpoint = aws.endpoint_url or "real AWS"
    print(f"Verifying {aws.environment.value} against {endpoint} ({aws.region})\n")
    print("Resources")
    tables = ", ".join(
        (
            ingestion.notifications_table,
            ingestion.user_inboxes_table,
            get_identity_settings().users_table,
            get_merchant_settings().merchants_table,
            get_financial_settings().accounts_table,
        ),
    )
    print(f"  tables    {tables}")
    print(f"  bus       {ingestion.event_bus_name}")
    print(f"  queue     {ingestion.parse_queue_name}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-llm",
        action="store_true",
        help=(
            "leave the configured model wired. Off by default: every alert "
            "here is one the deterministic parser reads, so the run costs "
            "nothing and works offline."
        ),
    )
    parser.add_argument(
        "--yes-really-production",
        action="store_true",
        help=(
            "run against ENVIRONMENT=production anyway. Creates two users "
            "with a password committed to this repository."
        ),
    )

    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    _refuse_production(override=bool(arguments.yes_really_production))

    if not arguments.keep_llm:
        os.environ["LLM_API_KEY"] = ""
        os.environ.pop("GEMINI_API_KEY", None)

    _environment_banner()
    client = _build_client()
    report = Report()
    sessions: list[Session] = []
    incoming_ids: dict[str, str | None] = {}

    print("Flow")

    for person in PEOPLE:
        session = _register(client, person)
        sessions.append(session)
        _forward(session)
        print(
            f"  {person.label:<6} registered, {len(person.alerts)} alerts "
            f"forwarded to {session.address}",
        )

    for line in _drain_workers():
        print(f"  {line}")

    for session in sessions:
        _declare_accounts(client, session)
        incoming_ids[session.person.label] = _assign_incoming(client, session)
        _enter_manually(client, session)
        merchant_id = _edit_merchant(client, session)
        renamed = session.person.merchant_edit[1]
        print(
            f"  {session.person.label:<6} {len(session.accounts)} accounts declared, "
            f"payroll placed, manual entry recorded"
            + (f", merchant renamed to {renamed}" if merchant_id else ""),
        )

    print("\nIntegrity")

    for session in sessions:
        _check_integrity(
            client,
            session,
            report,
            incoming_id=incoming_ids[session.person.label],
        )

    integrity_failures = len(report.failures)
    report.render()

    print("\nIsolation")
    isolation_start = len(report.checks)

    for mine in sessions:
        for theirs in sessions:
            if mine is not theirs:
                _check_isolation(client, mine, theirs, report)

    for passed, what, detail in report.checks[isolation_start:]:
        mark = "ok  " if passed else "FAIL"
        suffix = f"  — {detail}" if detail else ""
        print(f"  [{mark}] {what}{suffix}")

    print("\nBalances")

    for session in sessions:
        view = _accounts(client, session)

        for account in view.accounts:
            print(
                f"  {session.person.label:<6} {account['name']!s:<16} "
                f"{account['balance']!s:>12} {account['currency']!s}  "
                f"({account['movements_applied']} movements)",
            )

        for figure in view.net_worth:
            print(
                f"  {session.person.label:<6} {'net worth':<16} "
                f"{figure['total']!s:>12} {figure['currency']!s}",
            )

    print("\nMerchants (reported, not asserted — see the note below)")

    for line in _describe_merchants(client, sessions):
        print(f"  {line}")

    failures = report.failures
    print(
        f"\n{len(report.checks) - len(failures)}/{len(report.checks)} checks passed"
        f" ({integrity_failures} integrity failures)"
        if failures
        else f"\n{len(report.checks)} checks passed",
    )

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
