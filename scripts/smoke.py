"""Drive a deployed Finflow over HTTP and check that it answers correctly.

    just smoke https://abc123.lambda-url.us-east-1.on.aws
    just smoke https://abc123.lambda-url.us-east-1.on.aws --with-pipeline
    just smoke https://abc123.lambda-url.us-east-1.on.aws --read-only

`verify_flow.py` answers "does the data hold together?" and drives the
application **in process**. This answers a different question — "is what we
just deployed alive and correct?" — and every call here crosses the network:
the Function URL, the API function's cold start, the token it signs with the
secret it resolved from Parameter Store, the tables it writes through its own
role. None of that is exercised by a `TestClient`.

Three depths, because the environments can afford different things:

* **Default.** One throwaway user per run: register, declare two accounts,
  enter a movement by hand, read it back. Touches no queue. It does need AWS
  credentials, for one reason: registering now requires a ticket that only
  whoever read the mailed code can get, and the canary's address
  (`@finflow.local`) is not one any mail server would deliver to. So the
  ticket is written straight into the challenges table and spent through the
  real endpoint — see
  `identity/presentation/cli/verification_tickets.py`.
* **`--with-pipeline`.** Also forwards one bank alert and waits for the
  movement to surface. This is what proves the worker functions are alive,
  and the only depth that needs AWS credentials: the alert enters through
  ingestion's own use case, exactly as the ingest worker enters it. Nothing
  here drains a queue — the deployed functions do, which is the whole point.
* **`--read-only`.** Health and the auth guard, no writes. What production
  gets after a deploy.

Every run uses a fresh address and a random password, so runs never collide
and nothing carries a credential that is written down anywhere. Writes are
refused when the deployment says it is production and
`--yes-really-production` was not passed: each one leaves a user behind and
no endpoint can delete one. That question is put to `/health`, not to local
settings — `just smoke` loads `.env.development` whatever URL follows it, so
a guard reading the env file would wave a production address straight
through.

Exit status is 0 only when every check passed, so CI can gate on it.
"""

from __future__ import annotations

import argparse
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
import secrets
import time
from typing import cast
import uuid
from zoneinfo import ZoneInfo

import httpx2

from personal_finance.contexts.identity.presentation.cli.verification_tickets import (
    issue_registration_ticket,
)
from personal_finance.shared.infrastructure.config.settings import (
    Environment,
    get_aws_settings,
)


# JSON off the wire. `object` rather than `Any` so every field still has to be
# narrowed where it is read.
type Json = dict[str, object]


BANK = "Bancolombia"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"
BOGOTA = ZoneInfo("America/Bogota")

CHECKING_NAME = "Smoke ahorros"
CARD_NAME = "Smoke tarjeta"
CARD_LAST_FOUR = "1234"

OPENING_BALANCE = Decimal("100000")
MANUAL_AMOUNT = Decimal("12345")
ALERT_AMOUNT = Decimal("45000")
ALERT_MERCHANT = "EXITO SUPERINTER CALI"

# A container image on a cold start is the slowest first request this system
# ever makes, and it is the one this script always hits.
FIRST_REQUEST_TIMEOUT = 60.0
REQUEST_TIMEOUT = 30.0

# The pipeline is three queues deep, and every hop can retry.
PIPELINE_POLL_SECONDS = 10.0


@dataclasses.dataclass(slots=True)
class Report:
    """What passed, what did not, and whether to fail the build."""

    checks: list[tuple[bool, str, str]] = dataclasses.field(
        default_factory=list[tuple[bool, str, str]],
    )

    def record(self, *, ok: bool, name: str, detail: str = "") -> None:
        self.checks.append((ok, name, detail))
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    @property
    def failures(self) -> int:
        return sum(1 for ok, _, _ in self.checks if not ok)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Canary:
    """A user that exists only for this run.

    Random rather than fixed: a smoke test that logs in as a known address
    with a known password is a published credential on every environment it
    ever touches.
    """

    email: str
    password: str


@dataclasses.dataclass(slots=True, kw_only=True)
class Session:
    canary: Canary
    token: str
    user_id: str
    address: str = ""
    accounts: dict[str, str] = dataclasses.field(default_factory=dict[str, str])

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _new_canary() -> Canary:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")

    return Canary(
        email=f"smoke-{stamp}-{secrets.token_hex(3)}@finflow.local",
        password=secrets.token_urlsafe(24),
    )


def _expect(response: httpx2.Response, *expected: int) -> httpx2.Response:
    """A structural step that failed makes every check after it meaningless."""
    if response.status_code not in expected:
        raise SystemExit(
            f"{response.request.method} {response.request.url} answered "
            f"{response.status_code}, expected {expected}: {response.text[:400]}",
        )

    return response


def _json(response: httpx2.Response) -> Json:
    return cast("Json", response.json())


def _rows(payload: Json, key: str) -> list[Json]:
    rows = payload.get(key)

    if not isinstance(rows, list):
        raise SystemExit(f"expected a list under {key!r}, got {rows!r}")

    return cast("list[Json]", rows)


def _text(row: Json, key: str) -> str:
    return str(row.get(key))


def _money(row: Json, key: str) -> Decimal:
    return Decimal(_text(row, key))


# ----------------------------------------------------------------------
# Reachability
# ----------------------------------------------------------------------


def _target_environment(client: httpx2.Client, base_url: str) -> str:
    """Ask the deployment which environment it is, before writing to it.

    The env file this process loaded says nothing about the URL it was handed:
    `just smoke` reads `.env.development` whatever address follows it, so a
    guard built on local settings would wave a production URL straight
    through. `/health` names the deployment, so every decision below is made
    about the thing actually being driven.

    Also the first request, so an unreachable deployment — the likeliest
    finding this script ever reports — comes back as one line instead of a
    stack trace.
    """
    try:
        response = client.get("/health", timeout=FIRST_REQUEST_TIMEOUT)
    except httpx2.RequestError as error:
        raise SystemExit(
            f"Cannot reach {base_url}: {error}. Check the URL "
            "(`just deploy-outputs` prints it) and that the stack is deployed.",
        ) from error

    if response.status_code != 200:
        raise SystemExit(
            f"{base_url}/health answered {response.status_code}, not 200: "
            f"{response.text[:200]}",
        )

    return str(_json(response).get("environment", ""))


def _check_health(client: httpx2.Client, report: Report) -> None:
    started = time.monotonic()
    response = _expect(
        client.get("/health", timeout=FIRST_REQUEST_TIMEOUT),
        200,
    )
    elapsed = time.monotonic() - started
    report.record(
        ok=_json(response).get("status") == "ok",
        name="health answers ok",
        detail=f"{elapsed:.1f}s (cold start included)",
    )


def _check_auth_guard(client: httpx2.Client, report: Report) -> None:
    """No token is 401, and somebody else's id is 404 rather than 403."""
    report.record(
        ok=client.get("/financial/accounts").status_code == 401,
        name="unauthenticated read is refused",
    )


# ----------------------------------------------------------------------
# The write path, over HTTP
# ----------------------------------------------------------------------


def _register(client: httpx2.Client, canary: Canary, report: Report) -> Session:
    created = _json(
        _expect(
            client.post(
                "/identity/register",
                json={
                    "email": canary.email,
                    "password": canary.password,
                    # Nothing could ever read mail at `@finflow.local`, so the
                    # ticket is issued through the operator path rather than
                    # the code exchange. Spending it here still exercises the
                    # real endpoint and the real conditional consume.
                    "verification_token": issue_registration_ticket(canary.email),
                    "allowed_domains": [BANK_DOMAIN],
                },
            ),
            201,
        ),
    )
    session = Session(
        canary=canary,
        token=_text(created, "access_token"),
        user_id=_text(created, "user_id"),
    )

    # The token the deployment signed has to verify in the same deployment:
    # this is the check that the signing secret resolved from Parameter Store
    # at all, and it fails loudly rather than at somebody's first login.
    me = _json(_expect(client.get("/identity/me", headers=session.headers), 200))
    report.record(
        ok=_text(me, "user_id") == session.user_id,
        name="issued token verifies against /identity/me",
    )

    logged_in = _json(
        _expect(
            client.post(
                "/identity/login",
                json={"email": canary.email, "password": canary.password},
            ),
            200,
        ),
    )
    report.record(
        ok=_text(logged_in, "user_id") == session.user_id,
        name="password round-trips through login",
    )

    inbox = _json(_expect(client.get("/identity/inbox", headers=session.headers), 200))
    session.address = _text(inbox, "address")
    report.record(
        ok="+" in session.address and "@" in session.address,
        name="registration derived a forwarding address",
        detail=session.address,
    )

    return session


def _declare_accounts(
    client: httpx2.Client,
    session: Session,
    report: Report,
) -> None:
    """One plain account for the manual entry, one card for the alert to find."""
    checking = _json(
        _expect(
            client.post(
                "/financial/accounts",
                json={
                    "name": CHECKING_NAME,
                    "kind": "savings",
                    "currency": "COP",
                    "opening_balance": str(OPENING_BALANCE),
                    "bank": BANK,
                },
                headers=session.headers,
            ),
            201,
        ),
    )
    session.accounts[CHECKING_NAME] = _text(checking, "id")

    card = _json(
        _expect(
            client.post(
                "/financial/accounts",
                json={
                    "name": CARD_NAME,
                    "kind": "credit_card",
                    "currency": "COP",
                    "credit_limit": "2000000",
                    "bank": BANK,
                    "instrument_kind": "credit_card",
                    "last_four": CARD_LAST_FOUR,
                },
                headers=session.headers,
            ),
            201,
        ),
    )
    session.accounts[CARD_NAME] = _text(card, "id")

    report.record(
        ok=_money(checking, "balance") == OPENING_BALANCE,
        name="declared account opens at its opening balance",
        detail=_text(checking, "balance"),
    )


def _enter_manually(
    client: httpx2.Client,
    session: Session,
    report: Report,
) -> None:
    """Money that never emailed, and the balance it has to move."""
    entered = _json(
        _expect(
            client.post(
                "/financial/transactions",
                json={
                    "direction": "outgoing",
                    "amount": str(MANUAL_AMOUNT),
                    "currency": "COP",
                    "occurred_at": int(datetime.now(tz=UTC).timestamp()),
                    "counterparty": "SMOKE MANUAL",
                    "account_id": session.accounts[CHECKING_NAME],
                    "note": "smoke test",
                },
                headers=session.headers,
            ),
            201,
        ),
    )

    listed = _json(
        _expect(
            client.get(
                "/financial/transactions",
                params={"limit": 50},
                headers=session.headers,
            ),
            200,
        ),
    )
    ids = {_text(row, "id") for row in _rows(listed, "transactions")}
    report.record(
        ok=_text(entered, "id") in ids,
        name="manual movement is readable back",
    )

    account = _json(
        _expect(
            client.get(
                f"/financial/accounts/{session.accounts[CHECKING_NAME]}",
                headers=session.headers,
            ),
            200,
        ),
    )
    expected = OPENING_BALANCE - MANUAL_AMOUNT
    report.record(
        ok=_money(account, "balance") == expected,
        name="balance moved by exactly the amount",
        detail=f"{_text(account, 'balance')} (expected {expected})",
    )


def _check_reads(client: httpx2.Client, session: Session, report: Report) -> None:
    """The endpoints a frontend opens on first paint."""
    for path in (
        "/financial/catalog",
        "/ingestion/catalog",
        "/merchants/catalog",
        "/merchants/categories",
        "/financial/net-worth",
        "/financial/summary",
        "/merchants",
        "/ingestion/notifications",
    ):
        ok = client.get(path, headers=session.headers).status_code == 200
        report.record(ok=ok, name=f"GET {path}")

    # Somebody else's account is missing, never forbidden: whether it exists
    # is not something this API tells a stranger.
    unknown = client.get(
        f"/financial/accounts/{uuid.uuid4()}",
        headers=session.headers,
    )
    report.record(
        ok=unknown.status_code == 404,
        name="unknown account is 404, not 403",
        detail=str(unknown.status_code),
    )


# ----------------------------------------------------------------------
# The pipeline, driven by the deployed functions
# ----------------------------------------------------------------------


def _forward_alert(session: Session, message_id: str) -> None:
    """Hand one alert to ingestion the way the ingest worker does.

    Imported here rather than at module scope so the default depth stays
    genuinely credential-free: a base URL is all it needs.
    """
    from personal_finance.contexts.ingestion.application.commands import (
        ReceiveBankNotificationCommand,
    )
    from personal_finance.contexts.ingestion.domain.value_objects import (
        EmailAddress,
        EmailMessageId,
    )
    from personal_finance.contexts.ingestion.presentation.http.router import (
        get_use_case,
    )
    from personal_finance.shared.domain.value_objects import PosixTime

    occurred = datetime.now(tz=BOGOTA)
    get_use_case().execute(
        ReceiveBankNotificationCommand(
            recipient=EmailAddress(session.address),
            message_id=EmailMessageId(message_id),
            sender=EmailAddress(BANK_SENDER),
            subject="Notificación de compra",
            raw_content=(
                f"Bancolombia: Compraste $45.000 en {ALERT_MERCHANT} con tu "
                f"T.Cred *{CARD_LAST_FOUR}, el "
                f"{occurred.strftime('%d/%m/%Y a las %H:%M')}"
            ),
            received_at=PosixTime.from_datetime(occurred),
        ),
    )


def _wait_for_movement(
    client: httpx2.Client,
    session: Session,
    report: Report,
    *,
    timeout: float,
) -> None:
    """Poll the API until the deployed workers have done their job.

    Deliberately no draining here. Against a deployment the event source
    mappings are the consumers; a second consumer in this process would race
    them for the same messages and make the result depend on who won.
    """
    deadline = time.monotonic() + timeout
    movement: Json | None = None

    while time.monotonic() < deadline:
        rows = _rows(
            _json(
                _expect(
                    client.get(
                        "/financial/transactions",
                        params={"origin": "bank_alert", "limit": 50},
                        headers=session.headers,
                    ),
                    200,
                ),
            ),
            "transactions",
        )
        movement = next(
            (row for row in rows if _money(row, "amount") == ALERT_AMOUNT),
            None,
        )

        if movement is not None:
            break

        time.sleep(PIPELINE_POLL_SECONDS)

    waited = timeout - max(deadline - time.monotonic(), 0.0)

    if movement is None:
        report.record(
            ok=False,
            name="forwarded alert reached the ledger",
            detail=f"nothing after {waited:.0f}s — check the parse and financial logs",
        )

        return

    report.record(
        ok=True,
        name="forwarded alert reached the ledger",
        detail=f"after {waited:.0f}s",
    )
    # The card was declared before the alert arrived, and its fingerprint is
    # the card the alert names — so adoption is automatic or it is broken.
    report.record(
        ok=_text(movement, "account_id") == session.accounts[CARD_NAME],
        name="alert was adopted by the account whose card it names",
    )
    # Attribution is the one soft check: it depends on the model being
    # configured, and a movement without a merchant is still a correct ledger.
    report.record(
        ok=movement.get("merchant") is not None,
        name="movement carries its merchant (needs LLM_API_KEY)",
        detail="unattributed" if movement.get("merchant") is None else "",
    )


def _check_notification(
    client: httpx2.Client,
    session: Session,
    report: Report,
    *,
    message_id: str,
) -> None:
    rows = _rows(
        _json(
            _expect(
                client.get(
                    "/ingestion/notifications",
                    params={"limit": 50},
                    headers=session.headers,
                ),
                200,
            ),
        ),
        "notifications",
    )
    mine = next(
        (row for row in rows if _text(row, "message_id") == message_id),
        None,
    )
    report.record(
        ok=mine is not None,
        name="forwarded alert is visible in ingestion",
        detail="" if mine is None else _text(mine, "status"),
    )


# ----------------------------------------------------------------------


def _refuse_production(*, target: str, read_only: bool, override: bool) -> None:
    """Every write here leaves a user behind, and nothing can delete one.

    Judged on what the deployment said it is, never on local settings — and
    fails closed when it said nothing, because "I could not tell what I am
    pointed at" is not a reason to start writing.
    """
    if read_only or override:
        return

    if target == Environment.PRODUCTION.value:
        raise SystemExit(
            f"Refusing to write against {target}: this registers a user that "
            "no endpoint can delete. Use --read-only for a deployed "
            "production check, or --yes-really-production if you mean it.",
        )

    if not target:
        raise SystemExit(
            "This deployment does not say which environment it is, so there "
            "is no way to know whether writing to it is safe. Deploy a build "
            "whose /health names its environment, or use --read-only.",
        )


def _require_matching_environment(target: str) -> None:
    """`--with-pipeline` writes through local settings; they must agree.

    The alert is published by this process, into the queue named by ENV_FILE,
    while the movement is looked for through the API at `base_url`. Point
    those at different environments and the run reports a missing movement
    that is sitting, correctly processed, in the other one.
    """
    local = get_aws_settings().environment.value

    if local == target:
        return

    raise SystemExit(
        f"--with-pipeline would publish the alert into {local} (ENV_FILE) "
        f"while polling a {target} API. Point ENV_FILE at {target}, or drop "
        "--with-pipeline.",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "base_url",
        help="the deployment to drive, e.g. https://abc.lambda-url.us-east-1.on.aws",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="health and the auth guard only. What production gets.",
    )
    parser.add_argument(
        "--with-pipeline",
        action="store_true",
        help=(
            "also forward one alert and wait for the movement. Needs AWS "
            "credentials for the environment named by ENV_FILE."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="seconds to wait for the pipeline (default: 300)",
    )
    parser.add_argument(
        "--yes-really-production",
        action="store_true",
        help="write against ENVIRONMENT=production anyway.",
    )

    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    read_only = bool(arguments.read_only)
    with_pipeline = bool(arguments.with_pipeline)

    if read_only and with_pipeline:
        # Silently skipping it would end in "2/2 checks passed" on a run whose
        # whole point was the workers, which is worse than not running at all.
        raise SystemExit(
            "--read-only and --with-pipeline contradict each other: the "
            "pipeline depth forwards an alert, which is a write.",
        )

    base_url = str(arguments.base_url).rstrip("/")
    report = Report()

    print(f"Smoking {base_url}\n")
    print("Reachability")

    with httpx2.Client(
        base_url=base_url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "finflow-smoke"},
    ) as client:
        target = _target_environment(client, base_url)
        print(f"  environment: {target or 'unnamed'}")
        _refuse_production(
            target=target,
            read_only=read_only,
            override=bool(arguments.yes_really_production),
        )

        if with_pipeline:
            _require_matching_environment(target)

        _check_health(client, report)
        _check_auth_guard(client, report)

        if not read_only:
            canary = _new_canary()
            print(f"\nIdentity — {canary.email}")
            session = _register(client, canary, report)

            print("\nAccounts and movements")
            _declare_accounts(client, session, report)
            _enter_manually(client, session, report)

            print("\nReads")
            _check_reads(client, session, report)

            if with_pipeline:
                print("\nPipeline")
                message_id = f"<smoke-{secrets.token_hex(6)}@finflow.local>"
                _forward_alert(session, message_id)
                print(f"  forwarded {message_id} to {session.address}")
                _wait_for_movement(
                    client,
                    session,
                    report,
                    timeout=float(arguments.timeout),
                )
                _check_notification(
                    client,
                    session,
                    report,
                    message_id=message_id,
                )

    passed = len(report.checks) - report.failures
    print(f"\n{passed}/{len(report.checks)} checks passed")

    if report.failures:
        raise SystemExit(f"{report.failures} check(s) failed")


if __name__ == "__main__":
    main()
