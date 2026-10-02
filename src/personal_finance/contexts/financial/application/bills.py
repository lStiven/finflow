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

**Two totals, never one.** "What this month costs" and "what is still to pay"
are different questions and a reader takes whichever is on screen to be the
answer to both. The first is what a budget is built on; the second is what
tells somebody whether this fortnight is going to be tight.

* **Confirming a charge.** Money, at last — and the one thing under this
  heading that writes to the ledger. It goes through the same use case that
  records a movement entered by hand, so a balance moves, a merchant is
  attributed, the month's spending counts it and a phone hears about it, all
  without a line of new machinery. What it does not reuse is the identity: a
  confirmed charge is keyed on its bill and its period, so pressing the button
  twice is refused by the ledger's own conditional write rather than by a
  check somebody has to remember to keep.

**"Paid" is never stored here.** It is read back off the ledger row the
confirmation wrote, whose id the bill can derive from the period alone. That
is what makes deleting the movement un-pay the charge with nothing to
remember to undo — and it is why there is no state to drift. Skipping is the
exception and has to be stored: no money moves, so there is no row to read.

And both are **per currency**, like net worth and every report here: summing
pesos and dollars needs a rate this app does not have and has no business
inventing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.financial.application.commands import (
    ConfirmScheduledChargeCommand,
    DeleteTransactionCommand,
)
from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    ManageTransactionsUseCase,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import (
    AccountLookup,
    ChargeLookup,
    ScheduledBillRepository,
)
from personal_finance.contexts.financial.domain.bills import (
    MAX_WINDOW_DAYS,
    BillCadence,
    BillId,
    BillOccurrence,
    BillStatus,
    ChargePayment,
    ChargeSource,
    OccurrenceState,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.exceptions import AccountClosedError
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


# How far ahead of today a charge is asked about when working out which one is
# next. Long enough for every cadence somebody pays early — a month — and
# short enough that a weekly bill does not turn one screen into a year of key
# reads. Past it a charge simply reads unsettled, which is the safe direction:
# it is shown as still coming rather than hidden as done.
SETTLEMENT_HORIZON_DAYS = 31


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
        charges: ChargeLookup,
    ) -> None:
        self._bills = bills
        self._accounts = accounts
        self._charges = charges

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

    def set_autopay(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        enabled: bool,
        timezone: str,
    ) -> BillSummary:
        """Turn charging itself on or off for one bill.

        The timezone is not a detail here: turning it on writes down the day
        it was turned on, and that day decides which charges the switch may
        ever reach. Read in UTC it would be tomorrow for half the evening in
        Bogotá, and a charge due today would fall outside a permission granted
        minutes ago.

        Refused on a bill charged to a closed account, for the same reason
        confirming one is: a charge that cannot be paid into anything must not
        be armed to pay itself.
        """
        bill = self._load(user_id, bill_id)

        if enabled:
            self._require_open_account(bill)
            bill.start_autopay(today=today_in(zone_of(timezone)))
        else:
            bill.stop_autopay()

        self._bills.save(bill)

        return self._summarize(bill)

    def _require_open_account(self, bill: ScheduledBill) -> None:
        if bill.account_id is None:
            return

        account = self._accounts.find(
            user_id=bill.user_id,
            account_id=bill.account_id,
        )

        if account is not None and account.is_closed:
            raise AccountClosedError(
                "This bill comes out of a closed account. Point it at another "
                "one, or reopen that account, before it charges itself",
            )

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
        payments = read_payments(
            _near_charges([bill], today=today),
            user_id=bill.user_id,
            charges=self._charges,
        )

        return BillSummary(
            bill=bill,
            frozen=account is not None and account.is_closed,
            next_occurrence=_next_from_today(
                bill,
                today=today,
                payments=payments.get(bill.id, {}),
            ),
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

    `outstanding` is a subset of `expected`: what nobody has answered for yet.
    It used to be "what has not fallen due", which was all this could say
    before charges could be confirmed — a charge whose day had passed was one
    the app could not see either way, and calling it unpaid would have been a
    claim. Now it is one, and the field is named for what it means.

    A **skipped** charge leaves both figures. It is not what the month costs,
    because nobody is going to be asked for it, and it is not outstanding for
    the same reason. A **paid** one stays in `expected` and leaves
    `outstanding`: it is exactly what the month cost.
    """

    currency: Currency
    expected: Decimal
    outstanding: Decimal


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

    And one more lookup, for the same reason: which of these charges the
    ledger already holds. Every (bill, period) pair on screen is asked for in
    a single call, rather than a round trip per charge — the whole screen is
    three reads whatever is declared.
    """

    def __init__(
        self,
        *,
        bills: ScheduledBillRepository,
        accounts: AccountLookup,
        charges: ChargeLookup,
    ) -> None:
        self._bills = bills
        self._accounts = accounts
        self._charges = charges

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
        payments = read_payments(
            [
                *charges_between(bills, since=since, until=until, today=today),
                # The charges just past today as well: the window may be a
                # month somebody scrolled back to, and `next_occurrence` is
                # always about now.
                *_near_charges(bills, today=today),
            ],
            user_id=query.user_id,
            charges=self._charges,
        )

        occurrences: list[BillOccurrence] = []
        summaries: list[BillSummary] = []

        for bill in bills:
            settled = payments.get(bill.id, {})
            occurrences.extend(
                bill.occurrences(
                    since=since,
                    until=until,
                    today=today,
                    payments=settled,
                ),
            )
            summaries.append(
                BillSummary(
                    bill=bill,
                    frozen=bill.account_id in closed,
                    next_occurrence=_next_from_today(
                        bill,
                        today=today,
                        payments=settled,
                    ),
                ),
            )

        occurrences.sort(key=lambda occurrence: occurrence.due_on)

        return BillsView(
            since=since,
            until=until,
            bills=tuple(summaries),
            occurrences=tuple(occurrences),
            totals=_totals(occurrences),
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


def _next_from_today(
    bill: ScheduledBill,
    *,
    today: dt.date,
    payments: Mapping[dt.date, ChargePayment],
) -> BillOccurrence | None:
    """The next charge nobody has answered for yet, a year out and no further.

    A year covers every cadence this knows, annual included, and bounds the
    walk for a bill whose owner declared it and then paused their life.

    Settled charges are stepped over, which is the whole point: a card showing
    "el 4, hoy" for a charge already confirmed this morning is the screen
    telling somebody to pay something twice.
    """
    upcoming = bill.occurrences(
        since=today,
        until=today + dt.timedelta(days=MAX_WINDOW_DAYS),
        today=today,
        payments=payments,
    )

    return next(
        (charge for charge in upcoming if not charge.state.is_settled),
        None,
    )


def charges_between(
    bills: Sequence[ScheduledBill],
    *,
    since: dt.date,
    until: dt.date,
    today: dt.date,
) -> list[tuple[ScheduledBill, dt.date]]:
    """Every (bill, period) pair falling inside a window."""
    return [
        (bill, charge.due_on)
        for bill in bills
        for charge in bill.occurrences(since=since, until=until, today=today)
    ]


def _near_charges(
    bills: Sequence[ScheduledBill],
    *,
    today: dt.date,
) -> list[tuple[ScheduledBill, dt.date]]:
    """The pairs close enough to today to be worth asking the ledger about.

    Bounded by `SETTLEMENT_HORIZON_DAYS` rather than by the year
    `_next_from_today` walks: nobody confirms a charge four months early, and
    asking would put a year of weekly keys behind every screen that renders a
    bill.
    """
    return charges_between(
        bills,
        since=today,
        until=today + dt.timedelta(days=SETTLEMENT_HORIZON_DAYS),
        today=today,
    )


def read_payments(
    pairs: Sequence[tuple[ScheduledBill, dt.date]],
    *,
    user_id: UserId,
    charges: ChargeLookup,
) -> dict[BillId, dict[dt.date, ChargePayment]]:
    """What the ledger holds for these charges, in one lookup.

    Two keys per charge, at most. The bill derives the id of the row it would
    have written itself, so that half is a plain existence question over known
    keys — no index, no scan, and no stored "paid" flag to disagree with the
    row itself. The other half is the movement somebody linked, whose id is
    the bank's own fingerprint and could never be derived; it is stored, and
    **it is still read against the ledger**, so a link pointing at a movement
    that has since been erased answers nothing, exactly as an erased
    confirmation does.

    When both exist for one period — which the use cases refuse to create,
    but two tabs could race into — the row this app wrote wins. It is the one
    that moved a balance, and it is the one an undo has to be able to erase.
    """
    wanted: dict[str, tuple[BillId, dt.date, ChargeSource]] = {}

    for bill, period in pairs:
        # A paused bill charges nothing, so there is nothing to ask about.
        if bill.status is BillStatus.PAUSED:
            continue

        wanted[bill.charge_id(period).value] = (bill.id, period, ChargeSource.CONFIRMED)
        linked = bill.linked_movement(period)

        if linked is not None:
            wanted.setdefault(linked, (bill.id, period, ChargeSource.MATCHED))

    if not wanted:
        return {}

    found: dict[BillId, dict[dt.date, ChargePayment]] = {}
    rows = charges.find_many(user_id=user_id, movement_ids=list(wanted))

    for movement_id, movement in rows.items():
        asked = wanted.get(movement_id)

        if asked is None:
            continue

        bill_id, period, source = asked
        settled = found.setdefault(bill_id, {})
        already = settled.get(period)

        if already is not None and already.source is ChargeSource.CONFIRMED:
            continue

        settled[period] = ChargePayment(
            movement_id=movement_id,
            amount=movement.amount,
            occurred_at=movement.occurred_at,
            source=source,
        )

    return found


def _totals(occurrences: Sequence[BillOccurrence]) -> tuple[CurrencyTotal, ...]:
    """The two figures, one pair per currency.

    Only what goes out is counted. A declared salary is a real recurring
    series and E3 will want it, but adding it here would net income against
    spending and produce a "this month costs" figure smaller than the month
    costs.

    A paid charge counts towards what the month costs at **what it actually
    cost**, not at what the bill projected. The two differ whenever a price
    changed, and the first is the figure that matches the account.

    Skipped charges are in neither. Nobody is going to be asked for them, so
    they are not what the month costs and not what is left to pay.
    """
    expected: dict[Currency, Decimal] = {}
    outstanding: dict[Currency, Decimal] = {}

    for occurrence in occurrences:
        if occurrence.direction is not MovementDirection.OUTGOING:
            continue

        if occurrence.state is OccurrenceState.SKIPPED:
            continue

        charged = (
            occurrence.amount
            if occurrence.payment is None
            else occurrence.payment.amount
        )
        currency = charged.currency
        expected[currency] = expected.get(currency, Decimal(0)) + charged.amount

        if not occurrence.state.is_settled:
            outstanding[currency] = (
                outstanding.get(currency, Decimal(0)) + charged.amount
            )

    return tuple(
        CurrencyTotal(
            currency=currency,
            expected=amount,
            outstanding=outstanding.get(currency, Decimal(0)),
        )
        for currency, amount in sorted(expected.items(), key=lambda pair: pair[0].value)
    )


class _Unknown:
    """ "Nobody told me" — distinct from "there is no payment".

    A plain sentinel rather than `None`, because `None` is a real answer here
    and the difference decides whether the ledger is asked at all.
    """


_UNKNOWN = _Unknown()


def _payment_of(movement: Transaction) -> ChargePayment:
    """The ledger row just written, as the reading side of it."""
    return ChargePayment(
        movement_id=movement.id.value,
        amount=movement.amount,
        occurred_at=movement.occurred_at,
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmChargeCommand:
    """One charge of a declared bill, the moment its owner says it happened.

    `amount` and `occurred_at` are what the owner corrects when reality did
    not match the projection: the gym raised its price, and the 4th was a
    Saturday so the money left on the 6th. Absent, the bill's own figure and
    the period's own day stand.

    Neither takes part in the charge's identity, which is the bill and the
    period. So confirming again with a different figure does not write a
    second row — it is refused as already paid, and correcting it is editing
    the movement, where every other correction in this app is made.
    """

    user_id: UserId
    bill_id: BillId
    period: dt.date
    amount: Money | None = None
    occurred_at: PosixTime | None = None
    note: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SettledCharge:
    """One charge after somebody answered for it, with its bill.

    Both halves, because both change: the charge gains a state, and the bill's
    "next charge" moves past it. An endpoint that answered with only one of
    them would leave the screen holding a card that still says the charge is
    due today.
    """

    bill: BillSummary
    occurrence: BillOccurrence
    #: The ledger row, when this was a confirmation. None for a skip, and for
    #: an undo — there is no row left to name.
    movement: Transaction | None = None


class SettleBillChargeUseCase:
    """Answering for one charge: it happened, it did not, or undo either.

    The only thing under this module that moves money, and it moves it
    through `ManageTransactionsUseCase` rather than through a ledger of its
    own. That is the argument for bills living inside Financial rather than in
    a context of their own, cashed: confirming a charge is one call, and the
    balance, the merchant, the month's spending, the integration event and the
    alert on somebody's phone all come with it already built.

    Undo is a first-class operation here, not a nicety. Both buttons are one
    tap away from a figure that changes what an account says it holds, and a
    tap that cannot be taken back on a money screen is how somebody stops
    trusting the screen.
    """

    def __init__(
        self,
        *,
        bills: ScheduledBillRepository,
        accounts: AccountLookup,
        charges: ChargeLookup,
        transactions: ManageTransactionsUseCase,
    ) -> None:
        self._bills = bills
        self._accounts = accounts
        self._charges = charges
        self._transactions = transactions

    def confirm(self, command: ConfirmChargeCommand) -> SettledCharge:
        """Say this charge happened, and write it into the ledger.

        Refused for a charge the bill's calendar does not have, for a paused
        bill, and for one whose account is closed. The first stops a period
        from being invented out of a path segment — the amount and the account
        come off the bill, so an invented period is money moving for a charge
        that does not exist. The other two refuse what the screen is already
        not offering, so a stale tab cannot do what a fresh one cannot.

        **And refused for a charge a movement already answered for.** That one
        is not symmetry with `link`, it is the whole point of the feature:
        writing here would record the same money twice, which is the drift
        this exists to remove with the sign flipped. The conditional write
        cannot catch it — the bank's row is keyed on the bank's fingerprint,
        so the two never collide.
        """
        bill = self._load(command.user_id, command.bill_id)
        self._chargeable(bill, command.period)
        self._unmatched(bill, command.period)
        amount = self._charged_amount(bill, command.amount)

        movement = self._transactions.confirm_scheduled(
            ConfirmScheduledChargeCommand(
                user_id=command.user_id,
                bill_id=bill.id.value,
                period=command.period,
                direction=bill.direction,
                amount=amount,
                occurred_at=command.occurred_at or _midday_on(command.period),
                # The bill's name *is* the counterparty, which is why the
                # aggregate refuses to truncate it: this is the text somebody
                # reads in their ledger and the text a merchant is attributed
                # from.
                counterparty=bill.name,
                account_id=bill.account_id,
                note=command.note,
            ),
        )

        return self._settled(
            bill,
            command.period,
            movement=movement,
            payment=_payment_of(movement),
        )

    def undo_confirmation(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        period: dt.date,
    ) -> SettledCharge:
        """Erase the movement this charge's confirmation wrote.

        The balance gets back exactly what the charge took, because that is
        what deleting a movement already does. Nothing else has to be undone:
        "paid" was never written anywhere, so removing the row is the whole of
        removing the answer.

        Silent when there is no such row. An undo whose job is already done
        has done its job, and a 404 on the second tap of a button somebody is
        unsure about is a worse answer than nothing.

        No `occurs_on` check, deliberately: amending the bill's day can leave
        a confirmed charge on a date the calendar no longer visits, and
        refusing to undo that would strand real money.
        """
        bill = self._load(user_id, bill_id)
        movement_id = bill.charge_id(period).value
        found = self._charges.find_many(
            user_id=user_id,
            movement_ids=[movement_id],
        )

        if movement_id in found:
            self._transactions.delete(
                DeleteTransactionCommand(
                    user_id=user_id,
                    transaction_id=movement_id,
                ),
            )

        # Only when a row of this app's own was actually erased. A charge
        # answered by a *movement* is undone by unlinking, and skipping it
        # here would leave the bill at once linked and skipped for one period
        # — the state `link` clears on purpose — so the charge would vanish
        # from what the month owes the moment somebody unlinked it.
        if movement_id in found and bill.autopay and bill.occurs_on(period):
            # Otherwise the next sweep writes it straight back. With autopay
            # on, "expected" means "will be charged", so an undo that left the
            # charge expected would be a button that undoes nothing — the
            # money would return within the hour, and the person would be
            # watching a screen argue with them. Skipping says what taking the
            # charge back means here: this one is not going to happen. It is
            # stored, visible on the card, and taken back in one tap.
            bill.skip(period)
            self._bills.save(bill)

        # No re-read: the row is gone, or was never there. Asking again would
        # be asking an eventually consistent index whether a write that just
        # happened has landed, and the honest answer is already in hand.
        return self._settled(bill, period, payment=None)

    def link(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        period: dt.date,
        movement_id: str,
    ) -> SettledCharge:
        """Say a movement already in the ledger is what this charge cost.

        The answer for the month the bank *did* send the email: the money is
        recorded, the balance already moved, and what was missing is only that
        nobody had said which charge it was for. **It writes nothing to the
        ledger**, which is the entire point — writing would be this feature
        producing the double count it exists to prevent.

        Refused when the charge is already confirmed. Both answers claim the
        same charge was paid and they point at different money; undoing the
        confirmation first is what to do, and the refusal says so.

        Refused for a movement this app wrote itself — an accrual, another
        bill's charge — and for one already answering for some other charge.
        The first would let the feature confirm itself; the second would let
        one payment settle three months.
        """
        bill = self._load(user_id, bill_id)
        self._chargeable(bill, period)

        settled = self._payment_for(bill, period)

        if settled is not None and settled.source is ChargeSource.CONFIRMED:
            raise ValueError(
                "This charge is already paid. Undo the payment before linking "
                "a movement to it",
            )

        movement = self._movement(user_id, movement_id)
        self._linkable(bill, movement)
        self._unclaimed(bill, period, movement_id=movement.id.value)

        bill.link(period, movement.id.value)
        self._bills.save(bill)

        return self._settled(
            bill,
            period,
            payment=ChargePayment(
                movement_id=movement.id.value,
                amount=movement.amount,
                occurred_at=movement.occurred_at,
                source=ChargeSource.MATCHED,
            ),
        )

    def unlink(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        period: dt.date,
    ) -> SettledCharge:
        """Take back the claim that a movement answered for this charge.

        **Erases nothing.** The movement is the bank's fact and stays where it
        is, spent and counted; what goes away is the claim about which charge
        it paid, which is the only part this app made up. That is the whole
        difference from undoing a confirmation, and it is why the two are
        separate operations rather than one "undo" that guesses.

        Silent when there is nothing linked, for the reason every undo here is.
        """
        bill = self._load(user_id, bill_id)
        bill.unlink(period)
        self._bills.save(bill)

        return self._settled(bill, period, payment=None)

    def skip(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        period: dt.date,
    ) -> SettledCharge:
        """Say this charge is not going to happen.

        Refused once it is paid. A skip over a confirmed charge would hide a
        movement that is in the ledger and moved a balance — the figures would
        stop adding up, and nothing on screen would say why. Undoing the
        confirmation is what to do first, and the refusal says so.
        """
        bill = self._load(user_id, bill_id)
        self._chargeable(bill, period)

        settled = self._payment_for(bill, period)

        if settled is not None:
            # Two ways to be paid, two ways to take it back, and the message
            # has to name the right one: a confirmation is undone by erasing
            # the row it wrote, a linked movement by forgetting the link —
            # erasing that one would throw away the bank's own fact.
            raise ValueError(
                "This charge is already paid. Undo the payment before skipping it"
                if settled.source is ChargeSource.CONFIRMED
                else "This charge is already paid by a movement. Unlink it "
                "before skipping it",
            )

        bill.skip(period)
        self._bills.save(bill)

        # Unpaid by the check above, and skipping cannot have made it paid.
        return self._settled(bill, period, payment=None)

    def undo_skip(
        self,
        *,
        user_id: UserId,
        bill_id: BillId,
        period: dt.date,
    ) -> SettledCharge:
        """Let a skipped charge be expected again."""
        bill = self._load(user_id, bill_id)
        bill.unskip(period)
        self._bills.save(bill)

        return self._settled(bill, period)

    def _settled(
        self,
        bill: ScheduledBill,
        period: dt.date,
        *,
        movement: Transaction | None = None,
        payment: ChargePayment | _Unknown | None = _UNKNOWN,
    ) -> SettledCharge:
        """The charge and its bill, as they stand after the change.

        `payment` is what the caller already knows about this period, and
        every path that just wrote something passes it. **A re-read here would
        contradict the write it is reporting:** the lookup is a
        `BatchGetItem`, which is eventually consistent, so a confirmation
        could answer `state: expected` for the row it had just written and
        hand back `movement` in the same breath. That is the same shape as the
        `frozen` bug this endpoint family already had once — two answers from
        one API disagreeing about one field.

        `_UNKNOWN` is for the one path that genuinely does not know: taking a
        skip back says nothing about whether the charge was also paid, which
        it can be.
        """
        today = dt.datetime.now(dt.UTC).date()
        settled = (
            self._payment_for(bill, period)
            if isinstance(payment, _Unknown)
            else payment
        )
        near = dict(
            read_payments(
                _near_charges([bill], today=today),
                user_id=bill.user_id,
                charges=self._charges,
            ).get(bill.id, {}),
        )
        # What was just written wins over what the lookup saw, for the same
        # reason: `next_occurrence` must not point at the charge that was
        # confirmed a moment ago.
        if settled is None:
            near.pop(period, None)
        else:
            near[period] = settled

        account = (
            None
            if bill.account_id is None
            else self._accounts.find(user_id=bill.user_id, account_id=bill.account_id)
        )

        return SettledCharge(
            bill=BillSummary(
                bill=bill,
                frozen=account is not None and account.is_closed,
                next_occurrence=_next_from_today(bill, today=today, payments=near),
            ),
            occurrence=bill.charge_on(period, today=today, payment=settled),
            movement=movement,
        )

    def _payment_for(
        self,
        bill: ScheduledBill,
        period: dt.date,
    ) -> ChargePayment | None:
        return (
            read_payments(
                [(bill, period)],
                user_id=bill.user_id,
                charges=self._charges,
            )
            .get(bill.id, {})
            .get(period)
        )

    def _chargeable(self, bill: ScheduledBill, period: dt.date) -> None:
        if bill.status is BillStatus.PAUSED:
            raise ValueError("A paused bill is not charged for anything")

        if not bill.occurs_on(period):
            raise ValueError(
                f"This bill is not charged on {period.isoformat()}",
            )

        if bill.account_id is None:
            return

        account = self._accounts.find(
            user_id=bill.user_id,
            account_id=bill.account_id,
        )

        if account is None:
            raise AccountNotFoundError(f"No such account: {bill.account_id.value}")

        if account.is_closed:
            raise AccountClosedError(
                "This bill comes out of a closed account. Point it at another "
                "one, or reopen that account, before confirming a charge",
            )

    def _unmatched(self, bill: ScheduledBill, period: dt.date) -> None:
        """Refuse to write a charge a movement is already answering for.

        Read rather than assumed from the stored link: a link pointing at a
        movement somebody has since erased answers nothing, and refusing on
        the strength of it would leave a charge that can never be confirmed.
        """
        settled = self._payment_for(bill, period)

        if settled is not None and settled.source is ChargeSource.MATCHED:
            raise ValueError(
                "This charge is already paid by a movement. Unlink it before "
                "confirming it, or the same money is recorded twice",
            )

    def _movement(self, user_id: UserId, movement_id: str) -> Transaction:
        """The movement about to be linked, read back from the ledger.

        Read rather than trusted: the id comes off a screen, and a link to
        something that is not there would be a charge reading paid with
        nothing behind it. Asking through the same lookup as everything else
        here is also what keeps it this user's — one person's charge can never
        be settled by another person's money.
        """
        identity = movement_id.strip()
        found = self._charges.find_many(user_id=user_id, movement_ids=[identity])
        movement = found.get(identity)

        if movement is None:
            raise TransactionNotFoundError(f"No such movement: {movement_id}")

        return movement

    def _linkable(self, bill: ScheduledBill, movement: Transaction) -> None:
        """Whether this movement could be this bill's charge at all.

        The refusals that no screen may talk anybody out of. A row this app
        wrote is this feature confirming itself. A transfer between the
        owner's own accounts is not spending, so calling it a paid bill would
        put money in the month's total that nobody spent. The direction and
        the currency are the charge being a different fact altogether: a
        salary arriving does not pay the gym, and 40 dollars is not 40 000
        pesos whatever the day's rate is.

        How *close* it has to be — the day, the figure, the merchant — is
        deliberately not asked here. That is what proposes a link; this is
        what a person is allowed to assert.
        """
        if movement.origin.is_self_written:
            raise ValueError(
                "This movement is one this app wrote itself, so it cannot be "
                "what paid a charge",
            )

        if movement.is_transfer:
            raise ValueError(
                "A transfer between your own accounts is not a payment of a bill",
            )

        if movement.direction is not bill.direction:
            raise ValueError(
                "This movement goes the other way, so it cannot be this charge",
            )

        if movement.amount.currency is not bill.amount.currency:
            raise ValueError(
                f"This bill is in {bill.amount.currency.value}, so a movement "
                f"in {movement.amount.currency.value} cannot be its charge",
            )

    def _unclaimed(
        self,
        bill: ScheduledBill,
        period: dt.date,
        *,
        movement_id: str,
    ) -> None:
        """Refuse a movement already answering for some other charge.

        One payment settles one thing. Without this, one Netflix charge could
        be linked to January, February and March, and three months would read
        paid on the strength of one figure — the double count again, arrived
        at from the other side.

        Re-linking the same movement to the same charge is not a conflict: it
        is the answer that is already there, and a screen retrying a request
        it is unsure about must not be told off for it.
        """
        for other in self._bills.list_by_user(bill.user_id):
            for day, linked in other.linked.items():
                if linked != movement_id:
                    continue

                if other.id == bill.id and day == period:
                    continue

                raise ValueError(
                    f"That movement is already the charge of «{other.name}» "
                    f"on {day.isoformat()}",
                )

    def _charged_amount(self, bill: ScheduledBill, stated: Money | None) -> Money:
        """What actually moved, defaulting to what the bill says.

        A stated figure in another currency is refused rather than converted:
        a rate is a fact about a moment nobody recorded here, and the account
        would take the movement at face value.
        """
        if stated is None:
            return bill.amount

        if stated.currency is not bill.amount.currency:
            raise ValueError(
                f"This bill is in {bill.amount.currency.value}, so a charge "
                f"cannot be confirmed in {stated.currency.value}",
            )

        return stated

    def _load(self, user_id: UserId, bill_id: BillId) -> ScheduledBill:
        bill = self._bills.find(user_id=user_id, bill_id=bill_id)

        if bill is None:
            raise NoSuchBillError(f"No such bill: {bill_id.value}")

        return bill


def _midday_on(period: dt.date) -> PosixTime:
    """The period's own day, as an instant that stays on it.

    Noon UTC rather than midnight: the identity of a charge is a calendar day,
    and midnight lands on the day before for everybody west of Greenwich —
    including here, where a charge due on the 1st would be filed on the 31st
    and land in the previous month's spending. Noon is the same calendar day
    from UTC-11 to UTC+11, which is every zone this is read in.

    Only the default. The moment somebody says when the money actually moved,
    that is what is recorded.
    """
    return PosixTime.from_epoch_seconds(
        int(
            dt.datetime.combine(period, dt.time(hour=12), tzinfo=dt.UTC).timestamp(),
        ),
    )
