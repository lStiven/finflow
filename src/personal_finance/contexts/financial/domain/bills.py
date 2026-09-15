"""Money the owner knows is going to move, before it moves.

A domiciled charge — the gym, the rent, a streaming subscription — stops
producing a bank email precisely because it is automatic. The movement is real
and the balance this app shows drifts from the real one, month after month, on
exactly the most predictable spending a person has. Declaring the bill is what
closes that gap without depending on any history existing.

**Nothing here touches the ledger, and that is the rule that makes it safe.**
The ledger is what happened; a bill is what is going to happen. Projecting an
expected charge into balances, net worth or the monthly summary would put a
figure nobody has spent in front of every figure they have — and the alert
would announce it as a purchase, because an alert is built from the event and
the event could not tell the difference. A `ScheduledBill` becomes money only
when somebody confirms the charge, and that is a separate delivery.

The three nouns are easy to confuse, so they are named apart:

* `ScheduledBill` — what the owner declared. Persisted.
* `BillOccurrence` — one charge of one period, with its expected date. Derived
  from the bill's calendar, never stored: a stored copy of something a
  calendar can compute is a copy that goes stale when the day changes.
* `RecurringSeries` — what a detector *guesses* from the history. Does not
  exist yet, and will never write anything.

Confirming a charge does not change that. **"Paid" is read back off the ledger
row it wrote**, never written down here: the row's identity is derived from the
bill and the period, so the ledger already knows, and a second copy of the
answer would be the copy that survives somebody deleting the movement. Erasing
that movement therefore un-pays the charge, with nothing to remember to undo.

The one thing the ledger cannot answer is a charge that was *skipped* — the
month the gym did not bill, the subscription that was already cancelled. No
money moved, so there is no row to read, and that answer is stored on the bill.

And a fourth that already exists and is none of these: `RecurringCharge` in
`financing.py` is the insurance a credit carries every period.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
import dataclasses
import datetime as dt
import enum
from typing import Self
import uuid

from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
    MovementFingerprint,
    MovementId,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Money,
    PosixTime,
    UserId,
    ValueObject,
)


MAX_NAME_LENGTH = 120

# How long after its expected day a charge is still "about to happen" rather
# than late.
#
# Three days, fixed, and deliberately *not* the detector's tolerance. The two
# numbers answer different questions: a detector looking at history has to
# recognise a rhythm it was never told, so its tolerance grows with the
# cadence — up to twenty days for something annual. Here the owner stated the
# day. What moves it is the calendar, not uncertainty: a charge due on the 4th
# lands on the 6th when the 4th is a Saturday, and a holiday on the Monday
# pushes it one more. Three days covers that and nothing else, so "late" keeps
# meaning late even for a bill that only comes once a year.
GRACE_DAYS = 3

# A window wider than this is refused rather than served slowly: a weekly bill
# over ten years is five hundred rows nobody asked to read, built one date at
# a time.
MAX_WINDOW_DAYS = 400

# How many skipped periods one bill remembers. Skipping is the rare answer —
# the month a charge simply did not come — so this is years of them, and it
# exists only so a stored bill cannot grow without a bound. Past it the oldest
# is forgotten rather than the newest refused: a skip from four years ago
# reappearing as an uncleared charge is visible and harmless, while refusing
# the skip somebody is asking for today is a screen with a button that does
# nothing.
MAX_SKIPPED_PERIODS = 240


class BillCadence(enum.Enum):
    """How often the charge comes back.

    Explicit string values: the cadence is persisted, so reordering the
    members must not rewrite anybody's data.
    """

    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    BIMONTHLY = "bimonthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"

    def next_after(self, date: dt.date, *, anchor: dt.date) -> dt.date:
        """The charge after the one on `date`.

        Month-based cadences step by months and keep the **anchor's** day,
        not the day they last landed on. That is the whole subtlety: a bill
        anchored on the 31st lands on the 28th in February, and stepping from
        that would leave it on the 28th for the rest of its life. Reading the
        day off the anchor every time means February borrows the end of the
        month and March gets the 31st back.
        """
        if self in _DAY_STEP:
            return date + dt.timedelta(days=_DAY_STEP[self])

        return _add_months(date, months=_MONTH_STEP[self], day=anchor.day)

    def skip_to(self, anchor: dt.date, *, since: dt.date) -> dt.date:
        """A charge at or shortly before `since`, in one step rather than many.

        Deliberately approximate: it may land a period early, and the caller
        finishes with `next_after` so the day of the month is decided by the
        same rule as every other charge. Getting close is arithmetic; being
        exact is the calendar's job.
        """
        if self in _DAY_STEP:
            step = _DAY_STEP[self]
            periods = (since - anchor).days // step

            return anchor + dt.timedelta(days=periods * step)

        step = _MONTH_STEP[self]
        months = (since.year - anchor.year) * 12 + (since.month - anchor.month)

        return _add_months(
            anchor, months=max(months - months % step, 0), day=anchor.day
        )


_DAY_STEP = {
    BillCadence.WEEKLY: 7,
    BillCadence.BIWEEKLY: 14,
}

_MONTH_STEP = {
    BillCadence.MONTHLY: 1,
    BillCadence.BIMONTHLY: 2,
    BillCadence.QUARTERLY: 3,
    BillCadence.ANNUAL: 12,
}


def _add_months(date: dt.date, *, months: int, day: int) -> dt.date:
    """`date` advanced by whole months, landing on `day` or the month's end.

    Clamping rather than spilling into the next month: a bill on the 31st is
    a bill on the last day, and turning it into the 1st of March would move
    it into a period it does not belong to — and, once totals exist, into the
    wrong month.
    """
    total = date.month - 1 + months
    year = date.year + total // 12
    month = total % 12 + 1

    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


class BillStatus(enum.Enum):
    """Whether this bill is still expecting charges.

    `PAUSED` is the owner's decision and nothing else sets it. There is no
    member for "its account went away", because an account is never deleted —
    `Account.close` says so in as many words — so a bill whose account is
    closed is answered by looking at the account, not by writing a state here
    that would then have to be undone when they reopen it.
    """

    ACTIVE = "active"
    PAUSED = "paused"


class OccurrenceState(enum.Enum):
    """What can be said about one charge, today.

    Settled first, calendar second, and the order is the rule: a charge that
    was paid on the 9th is paid, not overdue, however far past its day it is.
    Only a charge nobody has answered for falls back on what the calendar can
    say about it.
    """

    #: Confirmed: a ledger row exists for this bill and this period.
    PAID = "paid"
    #: The owner said this one is not going to happen. No money moved and none
    #: is expected to, so it leaves both of the month's figures.
    SKIPPED = "skipped"
    #: Nobody has answered for it, and its day has not arrived yet or is
    #: within the grace period.
    EXPECTED = "expected"
    #: Nobody has answered for it and its day and grace both passed. Now that
    #: confirming exists this does mean unpaid, which is exactly what it could
    #: not mean before.
    OVERDUE = "overdue"

    @property
    def is_settled(self) -> bool:
        """Whether somebody has answered for this charge, either way."""
        return self in _SETTLED_STATES


_SETTLED_STATES = frozenset({OccurrenceState.PAID, OccurrenceState.SKIPPED})


@dataclasses.dataclass(frozen=True, slots=True)
class ChargePayment(ValueObject):
    """The ledger row that confirmed one charge, as this side of it reads.

    Carried rather than merely counted because the three fields answer three
    different questions a screen asks, and none of them is on the bill. What
    it actually cost may not be what the bill says — the gym raised its price
    and the owner confirmed the real figure. When it actually moved is not the
    day it was due — the 4th was a Saturday. And the movement's own id is what
    lets somebody go and look at it, or delete it, which is the only way to
    un-pay a charge.
    """

    movement_id: str
    amount: Money
    occurred_at: PosixTime


@dataclasses.dataclass(frozen=True, slots=True)
class BillId(ValueObject):
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid bill id: {value!r}") from error


@dataclasses.dataclass(frozen=True, slots=True)
class BillOccurrence(ValueObject):
    """One charge of one period, as the calendar predicts it.

    `due_on` is both the date and the identity: a charge is the charge of that
    day, and the pair (bill, date) is what a confirmed row is keyed on. It is
    the anchor's own calendar date, not the date the money actually moved —
    the money may move three days later, and `payment` is where that is
    recorded, so both are kept.

    `amount` stays what the bill projects even once it is paid. The two can
    differ and the difference is worth seeing: "120 000 expected, 130 000
    charged" is the gym raising its price, and a screen that overwrote the
    first with the second would hide it.
    """

    bill_id: BillId
    due_on: dt.date
    amount: Money
    direction: MovementDirection
    state: OccurrenceState
    #: The ledger row confirming it, when there is one. Always None unless
    #: `state` is `PAID`, and never None when it is.
    payment: ChargePayment | None = None


@dataclasses.dataclass(eq=False, slots=True)
class ScheduledBill(AggregateRoot[BillId]):
    """A charge its owner declared, and the calendar it follows.

    `name` does double duty on purpose: it is what the owner reads in the list
    *and* the counterparty a confirmed charge will carry into the ledger, so
    the movement says "Gimnasio" rather than a code only this screen knows.
    Two fields would be two things to keep in step for no gain.

    `account_id` is optional, like everywhere else in this context: somebody
    watching only what goes out has no accounts declared at all, and the bill
    is still worth knowing about.
    """

    user_id: UserId
    name: str
    amount: Money
    cadence: BillCadence
    #: The first expected charge, and the day every later one is derived from.
    starts_on: dt.date
    direction: MovementDirection = MovementDirection.OUTGOING
    account_id: AccountId | None = None
    category: str | None = None
    status: BillStatus = BillStatus.ACTIVE
    #: The periods the owner said would not happen. Stored, unlike "paid",
    #: because nothing else records them: a skip moves no money, so there is
    #: no ledger row to read the answer back off.
    skipped: frozenset[dt.date] = frozenset()
    created_at: PosixTime = dataclasses.field(default_factory=PosixTime.now)

    @classmethod
    def declare(
        cls,
        *,
        user_id: UserId,
        name: str,
        amount: Money,
        cadence: BillCadence,
        starts_on: dt.date,
        direction: MovementDirection = MovementDirection.OUTGOING,
        account_id: AccountId | None = None,
        category: str | None = None,
    ) -> Self:
        """Record what the owner says is going to be charged.

        No event. Nothing outside Financial has any business knowing what
        somebody plans to be charged for — that is a statement about their
        life, not about money that moved — and the fact worth publishing is
        the charge itself, which the ledger will publish when it is confirmed.
        """
        return cls(
            id=BillId.new(),
            user_id=user_id,
            name=_valid_name(name),
            amount=_chargeable(amount),
            cadence=cadence,
            starts_on=starts_on,
            direction=direction,
            account_id=account_id,
            category=_valid_category(category),
        )

    def amend(
        self,
        *,
        name: str | None = None,
        amount: Money | None = None,
        cadence: BillCadence | None = None,
        starts_on: dt.date | None = None,
        direction: MovementDirection | None = None,
        account_id: AccountId | None = None,
        category: str | None = None,
        clear_account: bool = False,
        clear_category: bool = False,
    ) -> None:
        """Correct what the bill says.

        Everything is correctable, because everything about a declared bill is
        a guess the first time: the gym raises its price, the charge moves to
        another account, the merchant turns out to be somebody else. What is
        *not* correctable is the identity — that is what a delete is for.

        The two `clear_*` flags exist because `None` already means "leave it
        alone" here, and an owner has to be able to say "this comes out of no
        account of mine" after having said it came out of one.
        """
        if name is not None:
            self.name = _valid_name(name)

        if amount is not None:
            self.amount = _chargeable(amount)

        if cadence is not None:
            self.cadence = cadence

        if starts_on is not None:
            self.starts_on = starts_on

        if direction is not None:
            # A salary declared as an expense would inflate the month's total
            # for as long as it stood, and re-declaring it would lose the
            # history of what it paid.
            self.direction = direction

        if clear_account:
            self.account_id = None
        elif account_id is not None:
            self.account_id = account_id

        if clear_category:
            self.category = None
        elif category is not None:
            self.category = _valid_category(category)

    def pause(self) -> None:
        """Stop expecting charges, without forgetting the bill.

        The cancelled subscription, and the one the owner is about to cancel.
        Deleting would lose what it cost and when it came; a paused bill keeps
        answering that and predicts nothing.
        """
        self.status = BillStatus.PAUSED

    def resume(self) -> None:
        self.status = BillStatus.ACTIVE

    def charge_id(self, period: dt.date) -> MovementId:
        """What the ledger row for this period is, or would be, called.

        Here rather than in the caller so there is exactly one answer. Reading
        a charge back and writing one have to agree on this to the byte — they
        are the same question asked from two directions — and a second place
        that derived it would be a second place to get it wrong, with the
        failure showing up as money that can be taken twice.
        """
        return MovementId.from_fingerprint(
            MovementFingerprint.from_schedule(
                user_id=self.user_id,
                bill_id=self.id.value,
                period=period,
            ),
        )

    def occurs_on(self, period: dt.date) -> bool:
        """Whether this bill is charged on exactly that day.

        What stops a period from being invented. Nothing else checks it: the
        day is a path segment, and without this anybody could confirm — and so
        move money for — a charge that is not on this bill's calendar at all,
        or skip a day it was never going to be charged on and leave a skip
        that answers for nothing.
        """
        return period >= self.starts_on and self._first_on_or_after(period) == period

    def skip(self, period: dt.date) -> None:
        """Say this charge is not going to happen.

        The month the gym did not bill, the subscription cancelled before its
        renewal. It writes nothing anywhere else — that is the difference from
        confirming — and it takes the charge out of both of the month's
        figures, because a charge nobody is going to be asked for is not what
        the month costs and not what is left to pay.

        Refused on a period this bill is not charged on, for the reason
        `occurs_on` gives. Skipping one already skipped is not an error: the
        answer is the same either way, and a screen retrying a request it is
        not sure landed must not be told off for it.
        """
        if not self.occurs_on(period):
            raise ValueError(f"This bill is not charged on {period.isoformat()}")

        kept = sorted({*self.skipped, period})

        # Oldest first out, so what is forgotten is the skip furthest from
        # anything anybody is still reading.
        self.skipped = frozenset(kept[-MAX_SKIPPED_PERIODS:])

    def unskip(self, period: dt.date) -> None:
        """Take a skip back, and let the charge be expected again.

        No `occurs_on` check, deliberately: amending the bill's day or its
        cadence can leave a skip on a date the calendar no longer visits, and
        refusing to remove that would make it permanent.
        """
        self.skipped = self.skipped - {period}

    def occurrences(
        self,
        *,
        since: dt.date,
        until: dt.date,
        today: dt.date,
        payments: Mapping[dt.date, ChargePayment] | None = None,
    ) -> tuple[BillOccurrence, ...]:
        """Every charge this bill expects inside the window, in order.

        Empty for a paused bill: a pause that still filled the month's total
        with charges nobody expects would make the total the one number in the
        app that cannot be trusted.

        Walks the calendar from `starts_on` rather than from `since`, because
        a month-based cadence has to keep the anchor's day — see
        `next_after`. The walk is bounded twice: by `until`, and by the window
        the caller is allowed to ask for.

        `payments` is what the ledger holds for this bill, keyed by period —
        handed in rather than looked up, because whether a row exists is a
        question for a repository and an aggregate that could ask one would
        be an aggregate holding a connection. Absent, every charge reads
        unsettled, which is the safe direction: a charge shown as still coming
        is a charge somebody looks at, and one hidden as paid is one they
        never think about again.
        """
        if self.status is BillStatus.PAUSED or until < since:
            return ()

        if (until - since).days > MAX_WINDOW_DAYS:
            raise ValueError(
                f"A bill window cannot be wider than {MAX_WINDOW_DAYS} days",
            )

        settled = payments or {}
        found: list[BillOccurrence] = []
        date = self._first_on_or_after(since)

        while date <= until:
            found.append(self.charge_on(date, today=today, payment=settled.get(date)))
            date = self.cadence.next_after(date, anchor=self.starts_on)

        return tuple(found)

    def charge_on(
        self,
        date: dt.date,
        *,
        today: dt.date,
        payment: ChargePayment | None = None,
    ) -> BillOccurrence:
        """One charge of one period, with settlement outranking the calendar.

        Paid first, then skipped, then what the day says. A charge paid a week
        late is paid, not overdue — reading it the other way round would put a
        red figure beside money that has already left.
        """
        if payment is not None:
            state = OccurrenceState.PAID
        elif date in self.skipped:
            state = OccurrenceState.SKIPPED
        else:
            state = _state_on(date, today=today)

        return BillOccurrence(
            bill_id=self.id,
            due_on=date,
            amount=self.amount,
            direction=self.direction,
            state=state,
            payment=payment,
        )

    def _first_on_or_after(self, since: dt.date) -> dt.date:
        """The first charge at or after `since`, reached by arithmetic.

        Stepping one charge at a time from `starts_on` would make the cost of
        a listing depend on how long ago the bill started rather than on how
        wide the window is — and `starts_on` is a date somebody typed. A
        weekly bill anchored in the year 202 is ninety-odd thousand
        iterations, twice per bill, on a request that asks about one month.
        The window guard above does not bound that, because it bounds the
        window and this walk was not inside it.
        """
        if since <= self.starts_on:
            return self.starts_on

        date = self.cadence.skip_to(self.starts_on, since=since)

        # The jump lands at or just before `since`; at most a couple of steps
        # finish the job, and stepping is what keeps the anchor's day right.
        while date < since:
            date = self.cadence.next_after(date, anchor=self.starts_on)

        return date


def _state_on(due_on: dt.date, *, today: dt.date) -> OccurrenceState:
    if due_on + dt.timedelta(days=GRACE_DAYS) < today:
        return OccurrenceState.OVERDUE

    return OccurrenceState.EXPECTED


def _valid_name(name: str) -> str:
    """Collapsed, and refused rather than cut.

    This is the counterparty a confirmed charge will carry into the ledger,
    so a silent truncation would not be a cosmetic loss — it would be a
    movement whose description is half a sentence, with nothing saying why.
    """
    stripped = " ".join(name.split())

    if not stripped:
        raise ValueError("A bill needs a name")

    if len(stripped) > MAX_NAME_LENGTH:
        raise ValueError(f"A bill name cannot exceed {MAX_NAME_LENGTH} characters")

    return stripped


def _valid_category(category: str | None) -> str | None:
    if category is None:
        return None

    stripped = category.strip()

    return stripped or None


def _chargeable(amount: Money) -> Money:
    """`Money` already refuses a negative. Zero is refused here.

    A bill for nothing is either a mistake or a placeholder, and both of them
    end up as a row in a total that adds nothing and a reminder that says to
    pay zero.
    """
    if amount.amount == 0:
        raise ValueError("A bill cannot be for nothing")

    return amount
