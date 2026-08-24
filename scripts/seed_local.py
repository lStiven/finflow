"""Fill the local emulator with a reproducible dataset.

    just seed                # the demo user, its alerts, its accounts
    just seed --with-llm     # leave the configured model wired while seeding

moto keeps everything in memory, so every restart begins from an empty
environment. Rather than making that data durable, this makes it cheap to
recreate: one command walks the whole chain — register, approve the bank's
domain, forward six alerts, drain the three workers, declare the accounts
that adopt them — and leaves an account somebody can log into and browse.

The application is driven in process, so nothing needs to be running except
the emulator (`just aws-init`), and the workers built here are the ones
`just parse-worker` and friends run. Reaching across four contexts is what a
composition root does, and this is the one development tooling gets.

Re-running is safe. The alerts carry fixed message ids and ingestion
deduplicates them; accounts and manual entries are looked up before they are
written, because a manual entry's identity is random and would otherwise
double on every pass.

The model stays unwired unless `--with-llm`: every alert below is one the
deterministic parser reads, so seeding costs nothing and works offline.
Otherwise merchant's advisor would be asked about each new name.
"""

from __future__ import annotations

import argparse
from collections import Counter
import dataclasses
from datetime import datetime
import os
from typing import Protocol
from zoneinfo import ZoneInfo

from fastapi import status
from fastapi.testclient import TestClient
import httpx2

from personal_finance.api.main import create_app
from personal_finance.contexts.financial.presentation.cli.run_financial_worker import (
    build_worker as build_financial_worker,
)
from personal_finance.contexts.ingestion.presentation.cli.run_parse_worker import (
    build_worker as build_parse_worker,
)
from personal_finance.contexts.merchant.presentation.cli.run_merchant_worker import (
    build_worker as build_merchant_worker,
)
from personal_finance.shared.infrastructure.config.settings import get_aws_settings


DEFAULT_EMAIL = "demo@finflow.local"
# Long enough for the password policy, and printed at the end: this account
# exists to be logged into by hand.
DEFAULT_PASSWORD = "una frase larga de verdad"

BANK = "Bancolombia"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"

# Alerts state a local wall clock with no zone marker, and so does this file.
BOGOTA = ZoneInfo("America/Bogota")

# A drained queue answers an empty receive; the cap is only there so a message
# nothing can handle cannot spin this forever.
MAX_POLLS = 20


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Alert:
    """One email, as the user's bank would have forwarded it."""

    message_id: str
    subject: str
    body: str
    received_at: datetime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SeedAccount:
    """An account the demo user declares, after its alerts already arrived."""

    name: str
    kind: str
    opening_balance: str | None = None
    bank: str | None = None
    instrument_kind: str | None = None
    last_four: str | None = None
    # Other (instrument kind, last four) pairs whose alerts land here too —
    # the debit card and the account it draws on are two names for one thing.
    also_answers_to: tuple[tuple[str, str], ...] = ()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SeedEntry:
    """Money that never emailed, entered by hand."""

    direction: str
    amount: str
    counterparty: str
    occurred_at: datetime
    account_name: str | None
    note: str


def _local(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BOGOTA)


# Fixed instants rather than offsets from today: a balance nobody can predict
# is a balance nobody can check a change against.
ALERTS: tuple[Alert, ...] = (
    Alert(
        message_id="<seed-payroll@finflow.local>",
        subject="Bancolombia le informa",
        body=(
            "Bancolombia: Recibiste un pago de NOMINA de ACME SAS por "
            "$4.500.000 en tu cuenta de AHORROS el 15/08/2026 a las 07:30"
        ),
        received_at=_local(2026, 8, 15, 7, 31),
    ),
    Alert(
        message_id="<seed-market@finflow.local>",
        subject="Notificación de compra",
        body=(
            "Bancolombia: Compraste $45.000 en EXITO SUPERINTER CALI con tu "
            "T.Cred *1234, el 18/08/2026 a las 10:15"
        ),
        received_at=_local(2026, 8, 18, 10, 16),
    ),
    Alert(
        message_id="<seed-delivery@finflow.local>",
        subject="Notificación de compra",
        body=(
            "Bancolombia: Compraste $23.900 en RAPPI COLOMBIA con tu T.Cred "
            "*1234, el 19/08/2026 a las 20:42"
        ),
        received_at=_local(2026, 8, 19, 20, 43),
    ),
    Alert(
        message_id="<seed-pharmacy@finflow.local>",
        subject="Notificación de compra",
        body=(
            "Bancolombia: Compraste $18.500 en DROGUERIA LA REBAJA con tu "
            "T.Deb *5261, el 20/08/2026 a las 09:05"
        ),
        received_at=_local(2026, 8, 20, 9, 6),
    ),
    Alert(
        message_id="<seed-qr@finflow.local>",
        subject="Pago por QR",
        body=(
            "Bancolombia: Juan pagaste $12.000 por codigo QR desde tu cuenta "
            "*5261 a la llave 3001234567 el 21/08/2026 a las 13:20"
        ),
        received_at=_local(2026, 8, 21, 13, 21),
    ),
    Alert(
        message_id="<seed-transfer@finflow.local>",
        subject="Transferencia realizada",
        body=(
            "Bancolombia: Transferiste $250.000 desde tu cuenta *5261 a la "
            "cuenta *9988 el 22/08/2026 a las 08:00"
        ),
        received_at=_local(2026, 8, 22, 8, 1),
    ),
)

ACCOUNTS: tuple[SeedAccount, ...] = (
    SeedAccount(
        name="Tarjeta Bancolombia",
        kind="credit_card",
        bank=BANK,
        instrument_kind="credit_card",
        last_four="1234",
    ),
    SeedAccount(
        name="Ahorros Bancolombia",
        kind="savings",
        opening_balance="1200000",
        bank=BANK,
        # QR payments and transfers name the account itself; the card that
        # draws on it arrives under its own kind, hence the second key.
        instrument_kind="account",
        last_four="5261",
        also_answers_to=(("debit_card", "5261"),),
    ),
    SeedAccount(
        name="Efectivo",
        kind="cash",
        opening_balance="200000",
    ),
)

ENTRIES: tuple[SeedEntry, ...] = (
    SeedEntry(
        direction="outgoing",
        amount="89900",
        counterparty="NETFLIX",
        occurred_at=_local(2026, 8, 23, 9, 0),
        account_name="Tarjeta Bancolombia",
        note="cobro automatico, sin correo",
    ),
    SeedEntry(
        direction="outgoing",
        amount="35000",
        counterparty="ALMUERZO",
        occurred_at=_local(2026, 8, 23, 13, 15),
        account_name="Efectivo",
        note="efectivo",
    ),
)


class PollResult(Protocol):
    """What one pass over a queue did. Read-only, so the workers' own frozen
    results satisfy it.
    """

    @property
    def received(self) -> int: ...

    @property
    def handled(self) -> int: ...

    @property
    def rejected(self) -> int: ...


class Worker(Protocol):
    """The one method the three SQS workers have in common."""

    def poll_once(self, *, wait_seconds: int = 20) -> PollResult: ...


def _expect(response: httpx2.Response, *expected: int) -> httpx2.Response:
    if response.status_code not in expected:
        request = response.request
        raise SystemExit(
            f"{request.method} {request.url.path} answered "
            f"{response.status_code}: {response.text}",
        )

    return response


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _build_client() -> TestClient:
    """The real application, driven in process rather than over a socket.

    Same routes, same validation, same authentication as `just dev` — it only
    skips the server, so seeding needs no second terminal.
    """
    return TestClient(create_app(expose_local_only_routes=True))


def _authenticate(client: TestClient, *, email: str, password: str) -> str:
    response = client.post(
        "/identity/register",
        json={
            "email": email,
            "password": password,
            "allowed_domains": [BANK_DOMAIN],
        },
    )

    if response.status_code == status.HTTP_409_CONFLICT:
        # A second run against the same emulator: the account is already
        # there, so take a token the way its owner would.
        response = _expect(
            client.post(
                "/identity/login",
                json={"email": email, "password": password},
            ),
            status.HTTP_200_OK,
        )
        print(f"  user      {email} (already registered)")
    else:
        _expect(response, status.HTTP_201_CREATED)
        print(f"  user      {email} (registered)")

    return str(response.json()["access_token"])


def _approve_sender(client: TestClient, *, token: str) -> str:
    """Approve the bank's domain and return the forwarding address.

    Sent on every run, not only after a registration: an inbox that predates
    this seed may approve nobody, and then every alert below would be dropped
    exactly as the filter is meant to drop them.
    """
    response = _expect(
        client.patch(
            "/identity/inbox",
            json={"allowed_domains": [BANK_DOMAIN], "allowed_addresses": []},
            headers=_authorization(token),
        ),
        status.HTTP_200_OK,
    )
    address = str(response.json()["address"])
    print(f"  inbox     {address} (approved: {BANK_DOMAIN})")

    return address


def _forward_alerts(client: TestClient, *, address: str) -> Counter[str]:
    outcomes: Counter[str] = Counter()

    for alert in ALERTS:
        response = _expect(
            client.post(
                "/ingestion/bank-notifications",
                json={
                    "recipient": address,
                    "message_id": alert.message_id,
                    "sender": BANK_SENDER,
                    "subject": alert.subject,
                    "raw_content": alert.body,
                    "received_at": alert.received_at.isoformat(),
                },
            ),
            status.HTTP_202_ACCEPTED,
        )
        outcomes[str(response.json()["outcome"])] += 1

    summary = ", ".join(f"{count} {outcome}" for outcome, count in outcomes.items())
    print(f"  alerts    {len(ALERTS)} forwarded ({summary})")

    return outcomes


def _drain(worker: Worker, *, label: str) -> None:
    """Empty one queue. No long polling: an empty receive is the answer."""
    handled = 0
    rejected = 0

    for _ in range(MAX_POLLS):
        result = worker.poll_once(wait_seconds=0)

        if not result.received:
            break

        handled += result.handled
        rejected += result.rejected
    else:
        print(f"  {label:<9} still had messages after {MAX_POLLS} polls")

    note = f", {rejected} rejected" if rejected else ""
    print(f"  {label:<9} {handled} handled{note}")


def _drain_workers() -> None:
    _drain(build_parse_worker(), label="parse")
    # Both subscribe to the same event on their own queues, so neither one
    # waits for the other.
    _drain(build_merchant_worker(), label="merchant")
    _drain(build_financial_worker(), label="financial")


def _declare_accounts(client: TestClient, *, token: str) -> dict[str, str]:
    """Declare what the alerts were waiting for, and return the ids by name.

    Deliberately after the alerts: adoption is retroactive, and a seed that
    declared accounts first would never exercise it.
    """
    headers = _authorization(token)
    listed = _expect(
        client.get("/financial/accounts", params={"scope": "all"}, headers=headers),
        status.HTTP_200_OK,
    ).json()
    known = {str(account["name"]): str(account["id"]) for account in listed["accounts"]}
    accounts: dict[str, str] = {}

    for fixture in ACCOUNTS:
        existing = known.get(fixture.name)

        if existing is not None:
            accounts[fixture.name] = existing
            print(f"  account   {fixture.name} (already declared)")
            continue

        # Sent whole, nulls included: every optional field here is optional in
        # the payload too, and omitting them would say the same thing.
        payload: dict[str, str | None] = {
            "name": fixture.name,
            "kind": fixture.kind,
            "opening_balance": fixture.opening_balance,
            "bank": fixture.bank,
            "instrument_kind": fixture.instrument_kind,
            "last_four": fixture.last_four,
        }

        account = _expect(
            client.post("/financial/accounts", json=payload, headers=headers),
            status.HTTP_201_CREATED,
        ).json()
        account_id = str(account["id"])
        accounts[fixture.name] = account_id

        # Only on the run that created it: linking a key an account already
        # holds is not something to ask for twice. Each link adopts in turn,
        # so the last answer is the one that has everything.
        for instrument_kind, last_four in fixture.also_answers_to:
            account = _expect(
                client.post(
                    f"/financial/accounts/{account_id}/instruments",
                    json={
                        "bank": fixture.bank,
                        "instrument_kind": instrument_kind,
                        "last_four": last_four,
                    },
                    headers=headers,
                ),
                status.HTTP_200_OK,
            ).json()

        print(
            f"  account   {fixture.name} declared, "
            f"{account['movements_applied']} movements adopted",
        )

    return accounts


def _enter_manual(
    client: TestClient,
    *,
    token: str,
    accounts: dict[str, str],
) -> None:
    """Record what no bank announces.

    A manual entry gets a random identity and is written unconditionally, so
    nothing in the domain would stop a second run from doubling it. That is
    what this lookup is for.
    """
    headers = _authorization(token)
    listed = _expect(
        client.get(
            "/financial/transactions",
            params={"origin": "manual", "limit": 200},
            headers=headers,
        ),
        status.HTTP_200_OK,
    ).json()
    already_there = {
        (str(movement["counterparty"]), int(movement["occurred_at"]))
        for movement in listed["transactions"]
    }
    entered = 0

    for entry in ENTRIES:
        occurred_at = int(entry.occurred_at.timestamp())

        if (entry.counterparty, occurred_at) in already_there:
            continue

        payload: dict[str, str | int] = {
            "direction": entry.direction,
            "amount": entry.amount,
            "currency": "COP",
            "occurred_at": occurred_at,
            "counterparty": entry.counterparty,
            "note": entry.note,
        }

        if entry.account_name is not None:
            payload["account_id"] = accounts[entry.account_name]

        _expect(
            client.post("/financial/transactions", json=payload, headers=headers),
            status.HTTP_201_CREATED,
        )
        entered += 1

    skipped = len(ENTRIES) - entered
    note = f", {skipped} already there" if skipped else ""
    print(f"  manual    {entered} entered{note}")


def _summarize(client: TestClient, *, token: str, email: str, password: str) -> None:
    headers = _authorization(token)
    accounts = _expect(
        client.get("/financial/accounts", headers=headers),
        status.HTTP_200_OK,
    ).json()
    unassigned = _expect(
        client.get(
            "/financial/transactions",
            params={"unassigned": True, "limit": 1},
            headers=headers,
        ),
        status.HTTP_200_OK,
    ).json()
    everything = _expect(
        client.get("/financial/transactions", params={"limit": 1}, headers=headers),
        status.HTTP_200_OK,
    ).json()
    merchants = _expect(
        client.get("/merchants", params={"limit": 1}, headers=headers),
        status.HTTP_200_OK,
    ).json()

    print("\nAccounts")

    for account in accounts["accounts"]:
        print(
            f"  {account['name']!s:<22} {account['balance']!s:>12} "
            f"{account['currency']!s}  "
            f"({account['movements_applied']} movements)",
        )

    print("\nNet worth")

    for figure in accounts["net_worth"]:
        print(
            f"  {figure['currency']!s}  {figure['total']!s:>14}  "
            f"(assets {figure['assets']}, liabilities {figure['liabilities']})",
        )

    print(
        f"\nLedger      {everything['total']} movements, "
        f"{unassigned['total']} still unassigned",
    )
    print(
        f"Merchants   {merchants['total']} known, "
        f"{merchants['needs_review']} awaiting review",
    )
    print(f"\nLog in with {email} / {password}")
    print(f"Bearer      {token}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help=(
            "leave the configured model wired. Off by default: every alert "
            "here is one the deterministic parser reads, and the merchant "
            "advisor would otherwise be billed for each new name."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = get_aws_settings()

    if not settings.is_local:
        raise SystemExit(
            f"Refusing to seed: ENVIRONMENT is {settings.environment.value}, not "
            "local. This writes a demo user, alerts and accounts — never into "
            "a real account.",
        )

    if not args.with_llm:
        # Set before anything reads the settings, which cache on first read.
        # An empty key is what `configured` calls unconfigured, so the
        # workers wire no model at all.
        os.environ["LLM_API_KEY"] = ""

    endpoint = settings.endpoint_url or "the configured AWS account"
    print(f"Seeding {endpoint} ({settings.region})\n")

    client = _build_client()
    token = _authenticate(client, email=args.email, password=args.password)
    address = _approve_sender(client, token=token)
    _forward_alerts(client, address=address)
    _drain_workers()
    accounts = _declare_accounts(client, token=token)
    _enter_manual(client, token=token, accounts=accounts)
    _summarize(client, token=token, email=args.email, password=args.password)


if __name__ == "__main__":
    main()
