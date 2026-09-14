"""Declaring what is going to be charged, and reading what the month holds.

Two halves, and the split matters:

* **Managing the bill.** Declaring, correcting, pausing and forgetting. Plain
  writes to one aggregate — no ledger, no balance, no event. A bill is a
  statement about the future and the future has not happened.
* **Reading the calendar.** What falls inside a window, and what it adds up
  to. Computed at read time from the bills and today's date, never stored: a
  projection that outlived the day it assumed would be a lie with a timestamp
  on it, which is the same reason the amortization table in `financing.py` is
  not stored either.

**Two totals, never one.** "What this month costs" and "what has not fallen
due yet" are different questions and a reader takes whichever is on screen to
be the answer to both. The first is what a budget is built on; the second is
what tells somebody whether this fortnight is going to be tight.

And both are **per currency**, like net worth and every report here: summing
pesos and dollars needs a rate this app does not have and has no business
inventing.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
)
from personal_finance.contexts.financial.application.ports import (
    AccountLookup,
    ScheduledBillRepository,
)
from personal_finance.contexts.financial.domain.bills import (
    MAX_WINDOW_DAYS,
    BillCadence,
    BillId,
    BillOccurrence,
    BillStatus,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


class NoSuchBillError(Exception):
    """Raised when a bill id belongs to nobody, or to somebody else.

    One exception for both, because telling them apart out loud would confirm
    that a bill exists to whoever guessed its id.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeclareBillCommand:
    user_id: UserId
    name: str
    amount: Money
    cadence: BillCadence
    starts_on: dt.date
    direction: MovementDirection = MovementDirection.OUTGOING
    account_id: AccountId | None = None
    category: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AmendBillCommand:
    """A correction. Absent fields are left alone, which is why clearing the
    two optional ones needs a flag rather than a `None`."""

    user_id: UserId
    bill_id: BillId
    name: str | None = None
    amount: Money | None = None
    cadence: BillCadence | None = None
    starts_on: dt.date | None = None
    direction: MovementDirection | None = None
    account_id: AccountId | None = None
    category: str | None = None
    clear_account: bool = False
    clear_category: bool = False


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BillSummary:
    """One bill, as a screen needs it.

    `frozen` is derived and never stored. An account is closed, never deleted
    — `Account.close` is explicit about it — so "this bill's account went
    away" is a question the account answers, and writing a state onto the bill
    would mean undoing it by hand the day somebody reopens the account.

    A frozen bill still shows what it costs and when it came. What it must not
    do, once confirming exists, is charge anything.
    """

    bill: ScheduledBill
    frozen: bool
    #: The next charge from today onwards, if any is still coming.
    next_occurrence: BillOccurrence | None


class ManageBillsUseCase:
    """Everything the owner of a bill can do to it.

    Every method answers with a `BillSummary` rather than the bare aggregate.
    The two derived fields — whether the account is closed, and when the next
    charge lands — are not decoration: an endpoint that answered `frozen:
    false` on a bill declared against a closed account would contradict the
    listing that renders right after it.

    The account is checked here rather than in the aggregate: whether an id
    names one of this user's accounts is a question about the repository, and
    a domain object that had to ask one would be a domain object holding a
    connection. Checking it at all is what stops a typo from producing a bill
    that can never be paid into anything.
    """

    def __init__(
        self,
        *,
        bills: ScheduledBillRepository,
        accounts: AccountLookup,
    ) -> None:
        self._bills = bills
        self._accounts = accounts

    def declare(self, command: DeclareBillCommand) -> BillSummary:
        self._require_account(command.user_id, command.account_id)

        bill = ScheduledBill.declare(
            user_id=command.user_id,
            name=command.name,
            amount=command.amount,
            cadence=command.cadence,
            starts_on=command.starts_on,
            direction=command.direction,
            account_id=command.account_id,
            category=command.category,
        )
        self._bills.save(bill)

        return self._summarize(bill)

    def amend(self, command: AmendBillCommand) -> BillSummary:
        bill = self._load(command.user_id, command.bill_id)
        self._require_account(command.user_id, command.account_id)

        bill.amend(
            name=command.name,
            amount=command.amount,
            cadence=command.cadence,
            starts_on=command.starts_on,
            direction=command.direction,
            account_id=command.account_id,
            category=command.category,
            clear_account=command.clear_account,
            clear_category=command.clear_category,
        )
        self._bills.save(bill)

        return self._summarize(bill)

    def pause(self, *, user_id: UserId, bill_id: BillId) -> BillSummary:
        bill = self._load(user_id, bill_id)
        bill.pause()
        self._bills.save(bill)

        return self._summarize(bill)

    def resume(self, *, user_id: UserId, bill_id: BillId) -> BillSummary:
        bill = self._load(user_id, bill_id)
        bill.resume()
        self._bills.save(bill)

        return self._summarize(bill)

    def forget(self, *, user_id: UserId, bill_id: BillId) -> None:
        if not self._bills.remove(user_id=user_id, bill_id=bill_id):
            raise NoSuchBillError(f"No such bill: {bill_id.value}")

    def _summarize(self, bill: ScheduledBill) -> BillSummary:
        """The same two derived answers the listing gives, for one bill.

        Read from today in UTC rather than from a zone the caller could pass:
        both fields are about whole days, and the worst a zone can do to them
        is move a charge due tonight into yesterday.
        """
        today = dt.datetime.now(dt.UTC).date()
        account = (
            None
            if bill.account_id is None
            else self._accounts.find(user_id=bill.user_id, account_id=bill.account_id)
        )

        return BillSummary(
            bill=bill,
            frozen=account is not None and account.is_closed,
            next_occurrence=_next_from_today(bill, today=today),
        )

    def _load(self, user_id: UserId, bill_id: BillId) -> ScheduledBill:
        bill = self._bills.find(user_id=user_id, bill_id=bill_id)

        if bill is None:
            raise NoSuchBillError(f"No such bill: {bill_id.value}")

        return bill

    def _require_account(self, user_id: UserId, account_id: AccountId | None) -> None:
        if account_id is None:
            return

        if self._accounts.find(user_id=user_id, account_id=account_id) is None:
            raise AccountNotFoundError(f"No such account: {account_id.value}")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CurrencyTotal:
    """What the window holds, in one currency.

    `upcoming` is a subset of `expected`, not a remainder: it is what has not
    fallen due yet. **It does not mean "unpaid"** — nothing can be confirmed
    yet, so a charge whose day has passed is one this app cannot see either
    way, and calling it unpaid would be a claim. The day confirming exists,
    this number narrows to what is genuinely outstanding and the label on
    screen can say so.
    """

    currency: Currency
    expected: Decimal
    upcoming: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BillsView:
    since: dt.date
    until: dt.date
    bills: tuple[BillSummary, ...]
    occurrences: tuple[BillOccurrence, ...]
    totals: tuple[CurrencyTotal, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ListBillsQuery:
    user_id: UserId
    #: Defaults to the calendar month `today` falls in, read in `timezone`.
    since: dt.date | None = None
    until: dt.date | None = None
    timezone: str


class ListBillsUseCase:
    """What the owner has declared, and what it means for the window.

    Loads the accounts as well as the bills — one small query for a handful of
    rows — because whether a bill is frozen cannot be answered without them,
    and answering it per bill would be one query each.
    """

    def __init__(
        self,
        *,
        bills: ScheduledBillRepository,
        accounts: AccountLookup,
    ) -> None:
        self._bills = bills
        self._accounts = accounts

    def execute(self, query: ListBillsQuery) -> BillsView:
        today = today_in(zone_of(query.timezone))
        since, until = _window(query, today=today)

        bills = sorted(
            self._bills.list_by_user(query.user_id),
            key=lambda bill: (bill.status is BillStatus.PAUSED, bill.name.casefold()),
        )
        closed = {
            account.id
            for account in self._accounts.list_by_user(query.user_id)
            if account.is_closed
        }

        occurrences: list[BillOccurrence] = []
        summaries: list[BillSummary] = []

        for bill in bills:
            inside = bill.occurrences(since=since, until=until, today=today)
            occurrences.extend(inside)
            summaries.append(
                BillSummary(
                    bill=bill,
                    frozen=bill.account_id in closed,
                    next_occurrence=_next_from_today(bill, today=today),
                ),
            )

        occurrences.sort(key=lambda occurrence: occurrence.due_on)

        return BillsView(
            since=since,
            until=until,
            bills=tuple(summaries),
            occurrences=tuple(occurrences),
            totals=_totals(occurrences, today=today),
        )


def _window(query: ListBillsQuery, *, today: dt.date) -> tuple[dt.date, dt.date]:
    """The window asked for, or the month `today` sits in.

    Both ends are needed together: half a window is a question with no answer,
    and silently completing it with a month would answer a different one.
    """
    if (query.since is None) != (query.until is None):
        raise ValueError("A bill window needs both ends, or neither")

    if query.since is None or query.until is None:
        return _month_of(today)

    if query.until < query.since:
        raise ValueError("A bill window cannot end before it starts")

    if (query.until - query.since).days > MAX_WINDOW_DAYS:
        raise ValueError(f"A bill window cannot be wider than {MAX_WINDOW_DAYS} days")

    return query.since, query.until


def _month_of(day: dt.date) -> tuple[dt.date, dt.date]:
    first = day.replace(day=1)
    following = _add_one_month(first)

    return first, following - dt.timedelta(days=1)


def _add_one_month(first_of_month: dt.date) -> dt.date:
    if first_of_month.month == 12:
        return first_of_month.replace(year=first_of_month.year + 1, month=1)

    return first_of_month.replace(month=first_of_month.month + 1)


def _next_from_today(bill: ScheduledBill, *, today: dt.date) -> BillOccurrence | None:
    """The next charge coming, looked for a year out and no further.

    A year covers every cadence this knows, annual included, and bounds the
    walk for a bill whose owner declared it and then paused their life.
    """
    upcoming = bill.occurrences(
        since=today,
        until=today + dt.timedelta(days=MAX_WINDOW_DAYS),
        today=today,
    )

    return upcoming[0] if upcoming else None


def _totals(
    occurrences: Sequence[BillOccurrence],
    *,
    today: dt.date,
) -> tuple[CurrencyTotal, ...]:
    """The two figures, one pair per currency.

    Only what goes out is counted. A declared salary is a real recurring
    series and E3 will want it, but adding it here would net income against
    spending and produce a "this month costs" figure smaller than the month
    costs.
    """
    expected: dict[Currency, Decimal] = {}
    upcoming: dict[Currency, Decimal] = {}

    for occurrence in occurrences:
        if occurrence.direction is not MovementDirection.OUTGOING:
            continue

        currency = occurrence.amount.currency
        expected[currency] = (
            expected.get(currency, Decimal(0)) + occurrence.amount.amount
        )

        if occurrence.due_on >= today:
            upcoming[currency] = (
                upcoming.get(currency, Decimal(0)) + occurrence.amount.amount
            )

    return tuple(
        CurrencyTotal(
            currency=currency,
            expected=amount,
            upcoming=upcoming.get(currency, Decimal(0)),
        )
        for currency, amount in sorted(expected.items(), key=lambda pair: pair[0].value)
    )
