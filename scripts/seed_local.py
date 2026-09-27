"""Fill the local emulator with a reproducible dataset.

    just seed                # the demo user, its alerts, its accounts
    just seed --with-llm     # leave the configured model wired while seeding

moto keeps everything in memory, so every restart begins from an empty
environment. Rather than making that data durable, this makes it cheap to
recreate: one command walks the whole chain — register, approve both banks'
domains, mark the forwarding Google would have confirmed, forward the alerts,
drain the three workers, declare the accounts that adopt them, and enter by
hand what no bank emails — and leaves an account somebody can log into and
browse, with its setup already finished.

The dataset is meant to cover the cases the app actually has, not to be
large: two banks with deterministic parsers, a card payment inside one bank
(two linked rows from one alert), a card paid from outside it (one row whose
other side this app never sees), ordinary purchases, incoming pay, cash, and
a transfer to somebody else — which is spending, unlike the two above it.

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
from personal_finance.contexts.identity.presentation.cli.verification_tickets import (
    issue_registration_ticket,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.contexts.ingestion.presentation.cli.run_parse_worker import (
    build_worker as build_parse_worker,
)
from personal_finance.contexts.merchant.presentation.cli.run_merchant_worker import (
    build_worker as build_merchant_worker,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_aws_settings,
    get_ingestion_settings,
)


DEFAULT_EMAIL = "demo@finflow.local"
DEFAULT_NAME = "Demo"
# Long enough for the password policy, and printed at the end: this account
# exists to be logged into by hand.
DEFAULT_PASSWORD = "una frase larga de verdad"

BANK = "Bancolombia"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"

# The second bank with a deterministic parser. Seeded so the local
# environment exercises more than one template set — and so an account whose
# alerts word themselves completely differently is there to look at.
LULO_BANK = "Lulo bank"
LULO_DOMAIN = "lulobank.com"
LULO_SENDER = f"notificaciones@{LULO_DOMAIN}"

# Every Lulo alert carries it, and the templates have to match with it behind
# them. Abridged from the real one; nothing here reads it.
LULO_FOOTER = (
    "© 2026. Lulo bank, Bogotá, Colombia. En Lulo bank, nunca te pediremos "
    "datos como claves o usuarios mediante correo electrónico."
)

# Alerts state a local wall clock with no zone marker, and so does this file.
BOGOTA = ZoneInfo("America/Bogota")

# A drained queue answers an empty receive; the cap is only there so a message
# nothing can handle cannot spin this forever.
MAX_POLLS = 20

# When Google would have confirmed the forwarding rule: the day before the
# first alert below, so the guide reads in a coherent order. Fixed like every
# other instant here, for the same reason.
FORWARDING_CONFIRMED_AT = datetime(2026, 8, 14, 9, 0, tzinfo=BOGOTA)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Alert:
    """One email, as the user's bank would have forwarded it."""

    message_id: str
    subject: str
    body: str
    received_at: datetime
    # Which bank sent it. Defaulted because most of these are Bancolombia's,
    # and the domain has to be approved or the filter drops the alert exactly
    # as it is meant to.
    sender: str = BANK_SENDER


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
class SeedTransferLeg:
    """A payment between the owner's own balances, only one side of which is
    here: a card paid from another bank, a wallet, or cash.

    No alert can announce these — the one that would name both instruments
    comes from a bank that only knows its own half — so they are entered by
    hand and marked as transfers, which is what keeps them out of every total.
    """

    role: str
    amount: str
    counterparty: str
    occurred_at: datetime
    account_name: str
    note: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SeedBill:
    """A charge the demo user declared, that no bank will ever email about.

    Unlike everything else here, a bill is about the future: what it shows on
    a screen depends on the day it is read. So `starts_on` is always in the
    past and the calendar walks forward from it — a date chosen relative to
    today would make the seed produce a different month every day, and a date
    in the future would leave the screen empty until it arrived.
    """

    name: str
    amount: str
    currency: str
    cadence: str
    starts_on: str
    direction: str = "outgoing"
    account_name: str | None = None
    #: One of the values `GET /merchants/categories` answers. Decides the icon
    #: on the card, and the category the charge will carry once confirming
    #: exists. There is no category for rent, so it lands in `other`.
    category: str | None = None
    #: Declared and then paused, so the screen has one of those to show.
    paused: bool = False
    #: What to do with this month's charge, so the screen has every state on
    #: it at once rather than a column of identical rows. `"pay"` writes a real
    #: movement and moves a balance — the only thing under `BILLS` that does.
    settle: str | None = None


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
        message_id="<seed-card-payment@finflow.local>",
        subject="Pago a tarjeta de credito",
        body=(
            "Bancolombia: Pagaste $100.000 en la tarjeta de credito *1234 "
            "desde la cuenta *5261, el 24/08/2026 16:30"
        ),
        received_at=_local(2026, 8, 24, 16, 31),
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
    # Two more purchases, on other days and at other merchants, so the
    # reporting screens have more than one bar to draw and a weekday
    # breakdown that is not a single column.
    Alert(
        message_id="<seed-fuel@finflow.local>",
        subject="Notificación de compra",
        body=(
            "Bancolombia: Compraste $120.000 en ESTACION TEXACO NORTE con tu "
            "T.Cred *1234, el 26/08/2026 a las 07:50"
        ),
        received_at=_local(2026, 8, 26, 7, 51),
    ),
    Alert(
        message_id="<seed-restaurant@finflow.local>",
        subject="Notificación de compra",
        body=(
            "Bancolombia: Compraste $67.400 en CREPES & WAFFLES 45 con tu "
            "T.Deb *5261, el 29/08/2026 a las 20:10"
        ),
        received_at=_local(2026, 8, 29, 20, 11),
    ),
    # Lulo bank, whose alerts word the same facts nothing like Bancolombia's:
    # labelled legs separated by bullets, a Spanish long date and a 12-hour
    # clock. Its own leg is the one chosen by direction — `Destino` on money
    # arriving, `Origen` on money leaving.
    Alert(
        message_id="<seed-lulo-breb-in@finflow.local>",
        subject="Conoce el detalle de la transacción",
        body=(
            "Conoce el detalle de la transacción "
            "Recibiste $150,000 de CARLOS MEJIA "
            "Origen cuenta • 5261 Destino ahorro • 4407 "
            "ID. transacción • 155682205 "
            "Fecha 16 de agosto de 2026 Hora 11:20 a.m. " + LULO_FOOTER
        ),
        received_at=_local(2026, 8, 16, 11, 21),
        sender=LULO_SENDER,
    ),
    Alert(
        message_id="<seed-lulo-breb-out@finflow.local>",
        subject="Conoce el detalle de la transacción",
        body=(
            "Conoce el detalle de la transacción "
            "Realizaste una transferencia a MARIA GOMEZ por $80,000 "
            "Origen cuenta • 4407 Llave • 3005557788 "
            "ID. transacción • 155682311 "
            "Fecha 21 de agosto de 2026 Hora 6:40 p.m. " + LULO_FOOTER
        ),
        received_at=_local(2026, 8, 21, 18, 41),
        sender=LULO_SENDER,
    ),
    Alert(
        message_id="<seed-lulo-incoming@finflow.local>",
        subject="Recibiste dinero en tu cuenta",
        body=(
            "Recibiste dinero en tu cuenta "
            "Recibiste de OMNIPRO COLOMBIA $1.250.000. "
            "Origen ahorro • 7291 BANCO DAVIVIENDA "
            "Destino cuenta • 4407 Lulo Bank "
            "ID. transacción • 998877 "
            "Fecha 25 de agosto de 2026 Hora 9:15 a.m. " + LULO_FOOTER
        ),
        received_at=_local(2026, 8, 25, 9, 16),
        sender=LULO_SENDER,
    ),
    # A card at another bank, paid from Bancolombia. The alert names the
    # account and the institution, never the card, so it lands as spending;
    # the movement's screen then proposes "¿Fue un pago a tu Tarjeta AV
    # Villas?", because that card is declared below with this bank. AV Villas'
    # own receipt is not here on purpose: the model refuses it, as it should.
    Alert(
        message_id="<seed-avvillas-payment@finflow.local>",
        subject="Alertas y Notificaciones",
        body=(
            "Bancolombia: Pagaste $480,000.00 a BANCO COMERCIAL AV VILLAS desde "
            "tu producto *5261 el 30/08/2026 11:17:03. ¿Dudas? Llamanos al "
            "6045109095. Estamos cerca."
        ),
        received_at=_local(2026, 8, 30, 11, 18),
    ),
    # The same wording towards Lulo, and this time the other bank *does*
    # email: two movements, one spending and one income, for money that
    # never left. The screen offers to pair them — writing a side on Lulo
    # instead would count the money twice there.
    Alert(
        message_id="<seed-lulo-topup-out@finflow.local>",
        subject="Alertas y Notificaciones",
        body=(
            "Bancolombia: Pagaste $200,000.00 a LULO BANK S A desde tu producto "
            "5261 el 30/08/2026 16:05:18. ¿Dudas? Llamanos al 6045109095. "
            "Estamos cerca"
        ),
        received_at=_local(2026, 8, 30, 16, 6),
    ),
    Alert(
        message_id="<seed-lulo-topup-in@finflow.local>",
        subject="Recibiste dinero en tu cuenta",
        body=(
            "Recibiste dinero en tu cuenta "
            "Recibiste de JUAN PEREZ $200.000. "
            "Origen cuenta • 5261 BANCOLOMBIA "
            "Destino cuenta • 4407 Lulo Bank "
            "ID. transacción • 998901 "
            "Fecha 30 de agosto de 2026 Hora 4:06 p.m. " + LULO_FOOTER
        ),
        received_at=_local(2026, 8, 30, 16, 7),
        sender=LULO_SENDER,
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
        name="Ahorros Lulo",
        kind="savings",
        opening_balance="400000",
        bank=LULO_BANK,
        # Lulo calls the same account `ahorro` in one alert and `cuenta` in
        # the next, so its parser files every leg as `account` and there is
        # only ever one key to declare.
        instrument_kind="account",
        last_four="4407",
    ),
    SeedAccount(
        name="Efectivo",
        kind="cash",
        opening_balance="200000",
    ),
    # A card nothing emails about, so it answers to no instrument: its bank
    # is what lets a movement's screen recognise "BANCO COMERCIAL AV VILLAS"
    # as a payment to it. Declared owing exactly the payment above, so
    # accepting the proposal leaves it at zero.
    SeedAccount(
        name="Tarjeta AV Villas",
        kind="credit_card",
        opening_balance="480000",
        bank="AV Villas",
    ),
)

TRANSFER_LEGS: tuple[SeedTransferLeg, ...] = (
    # The card paid from a wallet this app does not hold. Recorded as an
    # ordinary movement it would be the month's largest *income*.
    SeedTransferLeg(
        role="destination",
        amount="150000",
        counterparty="Nequi",
        occurred_at=_local(2026, 8, 27, 10, 0),
        account_name="Tarjeta Bancolombia",
        note="pago de la tarjeta desde Nequi",
    ),
    # The mirror, and the one that would otherwise inflate a month's spending:
    # money leaving a tracked account towards a card at a bank that is not
    # here, so only the outgoing side can ever be known.
    SeedTransferLeg(
        role="source",
        amount="150000",
        counterparty="Tarjeta Nu",
        occurred_at=_local(2026, 8, 28, 15, 30),
        account_name="Ahorros Lulo",
        note="pago de la tarjeta Nu, que no esta declarada",
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


BILLS: tuple[SeedBill, ...] = (
    # The rent: the biggest fixed charge most people have, on the 1st.
    SeedBill(
        name="Arriendo",
        amount="1850000",
        currency="COP",
        cadence="monthly",
        starts_on="2026-01-01",
        account_name="Ahorros Bancolombia",
        category="other",
    ),
    # The case the whole feature was asked for: a gym that stopped emailing
    # because it is domiciled.
    SeedBill(
        name="Gimnasio",
        amount="120000",
        currency="COP",
        cadence="monthly",
        starts_on="2026-01-04",
        account_name="Ahorros Bancolombia",
        category="health",
        # Confirmed, so the local screen shows what a paid charge looks like —
        # and so the one movement bills can write is there to be looked at.
        settle="pay",
    ),
    # Anchored on the 31st, which is the rule most easily got wrong: it lands
    # on the 28th in February and is back on the 31st in March.
    SeedBill(
        name="Administración",
        amount="310000",
        currency="COP",
        cadence="monthly",
        starts_on="2026-01-31",
        account_name="Ahorros Bancolombia",
        category="fees",
        # The month it was not charged. Writes nothing and leaves both of the
        # month's figures.
        settle="skip",
    ),
    # Another currency, so the screen has to show the totals apart instead of
    # adding pesos to dollars.
    SeedBill(
        name="Dominio",
        amount="14",
        currency="USD",
        cadence="annual",
        starts_on="2026-02-18",
        category="subscriptions",
    ),
    # Declared income. It is listed and it is never netted off what the month
    # costs — a total that subtracted it would report a month costing less
    # than it costs.
    SeedBill(
        name="Nómina",
        amount="4200000",
        currency="COP",
        cadence="biweekly",
        starts_on="2026-01-15",
        direction="incoming",
        account_name="Ahorros Bancolombia",
        category="income",
    ),
    # Cancelled, and kept: a paused bill predicts nothing and adds nothing,
    # but still answers what it used to cost.
    SeedBill(
        name="Revista",
        amount="24900",
        currency="COP",
        cadence="monthly",
        starts_on="2026-01-09",
        account_name="Tarjeta Bancolombia",
        paused=True,
        category="entertainment",
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
            # Seeded addresses are invented and nobody reads them, so the
            # ticket is written straight into the table rather than mailed.
            # See `scripts/registration.py`.
            "verification_token": issue_registration_ticket(email),
            "name": DEFAULT_NAME,
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
    """Approve both banks' domains and return the forwarding address.

    Sent on every run, not only after a registration: an inbox that predates
    this seed may approve nobody, and then every alert below would be dropped
    exactly as the filter is meant to drop them.
    """
    domains = [BANK_DOMAIN, LULO_DOMAIN]
    response = _expect(
        client.patch(
            "/identity/inbox",
            json={"allowed_domains": domains, "allowed_addresses": []},
            headers=_authorization(token),
        ),
        status.HTTP_200_OK,
    )
    address = str(response.json()["address"])
    print(f"  inbox     {address} (approved: {', '.join(domains)})")

    return address


def _confirm_forwarding(address: str) -> None:
    """Record the one step nothing local can produce.

    In a real setup this mark is written by the ingest worker when Google's
    confirmation email lands in the shared mailbox and is followed. There is
    no such email here — no Gmail account forwards to the emulator — so the
    demo account would sit forever with one step open and its guide unable to
    show what a finished connection looks like, which is the whole point of
    seeding one.

    Written through the repository rather than an endpoint on purpose: there
    is no endpoint, and there must not be one. This is a verified fact, and a
    client that could claim it would turn a proof into an assertion. `main`
    refuses to run outside local, which is what keeps that true.

    First write wins, so a second run is a no-op rather than a new date.
    """
    settings = get_ingestion_settings()
    repository = DynamoDBUserInboxRepository(
        client=get_dynamodb_client(),
        table_name=settings.user_inboxes_table,
    )
    marked = repository.mark_forwarding_confirmed(
        address=EmailAddress(address),
        confirmed_at=PosixTime.from_datetime(FORWARDING_CONFIRMED_AT),
    )
    when = FORWARDING_CONFIRMED_AT.date().isoformat()
    state = f"confirmed {when}" if marked else "already confirmed"
    print(f"  forwarding {state}")


def _forward_alerts(client: TestClient, *, address: str) -> Counter[str]:
    outcomes: Counter[str] = Counter()

    for alert in ALERTS:
        response = _expect(
            client.post(
                "/ingestion/bank-notifications",
                json={
                    "recipient": address,
                    "message_id": alert.message_id,
                    "sender": alert.sender,
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


def _declare_bills(
    client: TestClient,
    *,
    token: str,
    accounts: dict[str, str],
) -> None:
    """Declare what is going to be charged, and pause the one that was cancelled.

    Looked up by name before writing, for the same reason the manual entries
    are: a bill's identity is random, so nothing in the domain would stop a
    second run from declaring the rent twice.

    Declaring touches nothing: after the declarations the balances are exactly
    what they were before them, which is the rule the feature rests on.

    **Confirming a charge does**, and one bill is confirmed on purpose — the
    gym, which is the case the whole feature was asked for. Without it the
    local screen would only ever show unpaid charges, and the state most worth
    looking at while building this would be the one nobody ever sees. It is
    re-runnable like everything else here, and not because this checks: the
    charge's id comes from the bill and the period, so the table refuses the
    second write on its own.
    """
    headers = _authorization(token)
    listed = _expect(
        client.get("/financial/bills", headers=headers),
        status.HTTP_200_OK,
    ).json()
    already_there = {str(bill["name"]) for bill in listed["bills"]}
    declared = 0

    for bill in BILLS:
        if bill.name in already_there:
            continue

        payload: dict[str, str | None] = {
            "name": bill.name,
            "amount": bill.amount,
            "currency": bill.currency,
            "cadence": bill.cadence,
            "starts_on": bill.starts_on,
            "direction": bill.direction,
            "category": bill.category,
        }

        if bill.account_name is not None:
            payload["account_id"] = accounts[bill.account_name]

        created = _expect(
            client.post("/financial/bills", json=payload, headers=headers),
            status.HTTP_201_CREATED,
        ).json()

        if bill.paused:
            _expect(
                client.post(
                    f"/financial/bills/{created['id']}/pause",
                    headers=headers,
                ),
                status.HTTP_200_OK,
            )

        declared += 1

    already = len(BILLS) - declared
    note = f", {already} already there" if already else ""
    print(f"  bills     {declared} declared{note}")

    settled = _settle_this_months_charges(client, headers=headers)
    print(f"  cobros    {settled} of this month's charges answered for")


def _settle_this_months_charges(
    client: TestClient,
    *,
    headers: dict[str, str],
) -> int:
    """Answer for this month's charge on every bill that asks for one.

    A pass of its own rather than a step inside the declaration loop, so it
    runs on **every** seed and not only the first: the bills survive a re-run,
    and a confirmation that only happened once would leave every later local
    database showing nothing but unpaid charges.

    Re-running is safe without checking anything. A confirmation's id comes
    from the bill and the period, so the table refuses the second write; a skip
    is the same answer written twice.

    The period is read back from the listing rather than computed here. The
    calendar is the server's — a bill anchored on the 31st lands on the 28th in
    February — and a second implementation of it in a seed script would be a
    second answer, drifting from the first.
    """
    wanted = {bill.name: bill.settle for bill in BILLS if bill.settle is not None}
    view = _expect(
        client.get("/financial/bills", headers=headers),
        status.HTTP_200_OK,
    ).json()
    ids = {
        str(bill["id"]): wanted[str(bill["name"])]
        for bill in view["bills"]
        if str(bill["name"]) in wanted
    }
    done = 0

    for charge in view["occurrences"]:
        action = ids.pop(str(charge["bill_id"]), None)

        if action is None:
            continue

        _expect(
            client.post(
                f"/financial/bills/{charge['bill_id']}"
                f"/occurrences/{charge['due_on']}/{action}",
                json={} if action == "pay" else None,
                headers=headers,
            ),
            status.HTTP_200_OK,
        )
        done += 1

    return done


def _enter_transfer_legs(
    client: TestClient,
    *,
    token: str,
    accounts: dict[str, str],
) -> None:
    """Record the payments between the owner's own balances that no alert can
    announce, because only one of the two banks involved is this one.

    Looked up before writing for the same reason the manual entries are: the
    identity is random, so nothing in the domain stops a second run from
    doubling them.
    """
    headers = _authorization(token)
    listed = _expect(
        client.get(
            "/financial/transactions",
            params={"origin": "manual", "transfers": "only", "limit": 200},
            headers=headers,
        ),
        status.HTTP_200_OK,
    ).json()
    already_there = {
        (str(movement["counterparty"]), int(movement["occurred_at"]))
        for movement in listed["transactions"]
    }
    entered = 0

    for leg in TRANSFER_LEGS:
        occurred_at = int(leg.occurred_at.timestamp())

        if (leg.counterparty, occurred_at) in already_there:
            continue

        _expect(
            client.post(
                "/financial/transactions/transfer",
                json={
                    "role": leg.role,
                    "amount": leg.amount,
                    "currency": "COP",
                    "occurred_at": occurred_at,
                    "counterparty": leg.counterparty,
                    "account_id": accounts[leg.account_name],
                    "note": leg.note,
                },
                headers=headers,
            ),
            status.HTTP_201_CREATED,
        )
        entered += 1

    skipped = len(TRANSFER_LEGS) - entered
    note = f", {skipped} already there" if skipped else ""
    print(f"  traslados {entered} entered{note}")


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
    # Exercises the movement/merchant join as well as the aggregate: a
    # breakdown by category can only be built by reading every movement's
    # counterparty back through merchant.
    by_category = _expect(
        client.get(
            "/financial/summary",
            params={"group_by": "category"},
            headers=headers,
        ),
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
    bills = _expect(
        client.get("/financial/bills", headers=headers),
        status.HTTP_200_OK,
    ).json()

    confirmed = sum(1 for charge in bills["occurrences"] if charge["state"] == "paid")
    print(
        f"\nBills       {len(bills['bills'])} declared, "
        f"{confirmed} of this month's charges confirmed",
    )

    for total in bills["totals"]:
        print(
            f"  this month {total['expected']!s:>14} {total['currency']!s}"
            f"  (still to pay {total['outstanding']})",
        )

    print("\nSpending by category")

    for group in by_category["groups"]:
        for figure in group["totals"]:
            print(
                f"  {group['label']!s:<22} {figure['outgoing']!s:>12} "
                f"{figure['currency']!s}  ({group['movements']} movements)",
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
    _confirm_forwarding(address)
    _forward_alerts(client, address=address)
    _drain_workers()
    accounts = _declare_accounts(client, token=token)
    _enter_manual(client, token=token, accounts=accounts)
    _enter_transfer_legs(client, token=token, accounts=accounts)
    _declare_bills(client, token=token, accounts=accounts)
    _summarize(client, token=token, email=args.email, password=args.password)


if __name__ == "__main__":
    main()
