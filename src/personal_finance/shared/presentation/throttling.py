"""Who is knocking, how often, and when to stop answering.

The counting rule lives in `shared/domain/throttling.py` and the counter in
`shared/infrastructure/throttling`; this is the part that knows about HTTP —
which request an attempt belongs to, and what a refusal looks like on the
wire.

**Two dimensions, and they answer different questions.** The *subject* is the
thing being guessed at: an address, a chat, a reset link. Limiting that is
what stops credential stuffing, and it cannot produce a false positive for
anybody else, because it is scoped to one account. The *address* is where the
guesses come from. Limiting that is what stops somebody spraying one password
across a thousand addresses — the attack the first dimension cannot see, since
each address is only tried once.

**The address dimension is the one that can hurt an innocent**, because
addresses are shared: a house, an office, a carrier putting a whole city
behind one NAT. Three things keep that safe, and all three matter:

* only refusals are counted, so people who get in cost nothing;
* the address budget is set where a shared connection cannot plausibly reach
  it by accident, and well under what an attack needs;
* and when the address cannot be established, the address limit is skipped
  rather than guessed at — see `client_ip`.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import ipaddress
import json
import logging
from typing import cast

from fastapi import HTTPException, Request, status

from personal_finance.shared.application.ports import AttemptCounter
from personal_finance.shared.domain.throttling import RateLimit
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)

#: What the Lambda Web Adapter puts the Function URL's request context in.
#: It is built from the event AWS delivers, so a client cannot send it: the
#: adapter overwrites whatever arrived with the same name.
REQUEST_CONTEXT_HEADER = "x-amzn-request-context"
FORWARDED_FOR_HEADER = "x-forwarded-for"


@dataclasses.dataclass(frozen=True, slots=True)
class Door:
    """One guarded surface, and what it allows.

    `subject` is optional because some doors have nothing to scope to: a
    registration is not an attempt against an existing account, so the address
    is all there is to count.
    """

    name: str
    address: RateLimit
    subject: RateLimit | None = None


class Guard:
    """Applies a door's limits to the request in front of it.

    Three verbs, and which one an endpoint uses is the whole design:

    * `spend` — count this request, then judge. For doors where the request
      *is* the cost: sending mail, creating an account.
    * `require` — judge without counting. For doors where the cost is only
      paid by a wrong answer, called before the attempt is made.
    * `charge` — count one against the budget without judging, after the
      fact. A wrong password on the way to a 401, or an account that was
      actually created.

    And `forgive`, which is not a verb about limits at all: it is what a
    correct password does to the failures before it.
    """

    def __init__(self, counter: AttemptCounter, *, trust_proxy: bool) -> None:
        self._counter = counter
        self._trust_proxy = trust_proxy

    def spend(
        self,
        door: Door,
        request: Request,
        *,
        subject: str | None = None,
    ) -> None:
        """Count this attempt and refuse it if the budget is gone.

        Counted *before* judging, deliberately: the attempt that goes over the
        line is itself an attempt, and not counting it would let somebody sit
        exactly on the limit for ever.
        """
        self._judge(door, request, subject=subject, count=True)

    def require(
        self,
        door: Door,
        request: Request,
        *,
        subject: str | None = None,
    ) -> None:
        """Refuse if the budget is already gone, without spending any of it."""
        self._judge(door, request, subject=subject, count=False)

    def charge(
        self,
        door: Door,
        request: Request,
        *,
        subject: str | None = None,
    ) -> None:
        """Count one against the budget, after the fact. Never raises.

        For the attempts whose cost is only known once they are over: a wrong
        password on the way to a 401, an account that really was created. The
        request has already been answered correctly by the time this runs, and
        a counter that cannot be written must not turn that answer into a 500.
        """
        now = PosixTime.now()

        for limit, bucket in self._buckets(door, request, subject=subject, now=now):
            self._counter.record(bucket=bucket, expires_at=limit.window_ends_at(now))

    def forgive(self, door: Door, *, subject: str) -> None:
        """Wipe this subject's failures. The address keeps its own.

        Only the subject, and on purpose: an address is shared, so one
        person's success says nothing about the fifty failures somebody else
        made from behind the same NAT.
        """
        if door.subject is None:
            return

        self._counter.clear(_subject_bucket(door, subject, now=PosixTime.now()))

    def _judge(
        self,
        door: Door,
        request: Request,
        *,
        subject: str | None,
        count: bool,
    ) -> None:
        now = PosixTime.now()

        for limit, bucket in self._buckets(door, request, subject=subject, now=now):
            # Both branches answer the same question — *which* attempt is
            # this one — from two directions. Counting returns the total
            # including it; reading returns what came before, so this one is
            # added.
            attempt = (
                self._counter.record(
                    bucket=bucket,
                    expires_at=limit.window_ends_at(now),
                )
                if count
                else self._counter.spent(bucket) + 1
            )
            verdict = limit.judge(attempt=attempt, now=now)

            if not verdict.allowed:
                _logger.warning(
                    "refused an attempt: too many",
                    extra={"door": door.name, "scope": bucket.split("|", 2)[1]},
                )

                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Demasiados intentos. Espera un momento y vuelve a intentarlo."
                    ),
                    headers={"Retry-After": str(verdict.retry_after_seconds)},
                )

    def _buckets(
        self,
        door: Door,
        request: Request,
        *,
        subject: str | None,
        now: PosixTime,
    ) -> list[tuple[RateLimit, str]]:
        """Every bucket this attempt spends from, narrowest first.

        The subject goes first so the message somebody sees is about their own
        account rather than about their neighbours: with both budgets gone,
        the first refusal is the one that gets raised.
        """
        buckets: list[tuple[RateLimit, str]] = []

        if door.subject is not None and subject:
            buckets.append((door.subject, _subject_bucket(door, subject, now=now)))

        address = client_ip(request, trust_proxy=self._trust_proxy)

        if address is not None:
            window = door.address.window_of(now)
            buckets.append((door.address, f"{door.name}|ip|{address}|{window}"))

        return buckets


def _subject_bucket(door: Door, subject: str, *, now: PosixTime) -> str:
    """The key one account's attempts are counted under.

    Hashed, and folded first. Hashed because an address in a key is an address
    in a table that is not the one addresses belong in, and this one is read
    by every failed login. Folded because `Ana@x.com` and `ana@x.com` are one
    account, and a limit that could be reset by pressing shift would not be a
    limit.
    """
    if door.subject is None:
        raise ValueError(f"The {door.name!r} door counts no subject")

    digest = hashlib.sha256(subject.strip().casefold().encode("utf-8")).hexdigest()

    return f"{door.name}|id|{digest[:32]}|{door.subject.window_of(now)}"


def client_ip(request: Request, *, trust_proxy: bool) -> str | None:
    """Where this request says it came from, when that can be believed.

    `None` is a real answer and the most important one here. Behind the Lambda
    Web Adapter the socket is always the adapter itself, so reading the peer
    would file **every request on earth under one address** — a limit that
    locks out everybody the moment anybody attacks.

    So an address that cannot be established is skipped, and it is worth
    being exact about what that costs. The doors that also count a subject —
    login, changing a password — keep limiting, because the account is still
    known. The doors that count only an address — registering, asking for
    mail, guessing a code, the webhook secret — are **left unlimited** until
    it can be read again. That is the deliberate trade: the alternative is
    filing those requests under one shared bucket, which turns a front door
    that changed shape into everybody locked out at once. It should never
    happen, the fallbacks below are two, and it logs a warning when it does.

    In order:

    * the Function URL's own request context, which AWS builds and the
      adapter passes through — a client cannot forge it, because the adapter
      overwrites a header of that name with the event's;
    * `X-Forwarded-For`, reading the **last** hop rather than the first. The
      first is whatever the client typed; the last is what the infrastructure
      in front appended;
    * the socket, which is only trusted when nothing is in front — locally,
      and in the tests.
    """
    if not trust_proxy:
        return request.client.host if request.client else None

    from_context = _source_ip_of(request.headers.get(REQUEST_CONTEXT_HEADER))

    if from_context is not None:
        return from_context

    forwarded = request.headers.get(FORWARDED_FOR_HEADER)

    if forwarded:
        last = _an_address(forwarded.rsplit(",", 1)[-1])

        if last is not None:
            return last

    _logger.warning("no trustworthy client address on a guarded request")

    return None


def _an_address(value: str | None) -> str | None:
    """The value, if it really is an IP address.

    Everything this reads arrives in a header, and a header is a string
    somebody else chose. Checking the shape costs nothing and buys two
    things: a key of arbitrary length never reaches the counter's table, and
    a value that is not an address is treated as "unknown" — which skips the
    address limit rather than inventing a bucket per piece of junk.

    Normalised through `ip_address` so the same host cannot hold two budgets
    by spelling itself differently: IPv6 has more than one way to write every
    address it can write at all.
    """
    if value is None:
        return None

    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _source_ip_of(header: str | None) -> str | None:
    """The `sourceIp` inside the adapter's request-context header.

    Tolerant of both shapes it can arrive in — plain JSON and base64 — and of
    neither: a header this cannot read is the same as no header, which costs
    the address limit and never the request.
    """
    if not header:
        return None

    for raw in (header, _decoded(header)):
        if raw is None:
            continue

        try:
            parsed: object = json.loads(raw)
        except (ValueError, TypeError):
            continue

        if not isinstance(parsed, dict):
            continue

        context = cast("dict[str, object]", parsed)
        # A Function URL nests it under `http`; a REST-style event keeps it
        # one level up, under `identity`. Reading both costs a line and saves
        # this from being wrong the day the front door changes.
        for key in ("http", "identity"):
            nested = context.get(key)

            if not isinstance(nested, dict):
                continue

            source = cast("dict[str, object]", nested).get("sourceIp")

            if isinstance(source, str):
                address = _an_address(source)

                if address is not None:
                    return address

    return None


def _decoded(header: str) -> str | None:
    try:
        return base64.b64decode(header, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
