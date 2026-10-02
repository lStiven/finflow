"""What the history says comes back, guessed rather than declared.

The other half of the bills feature, and the weaker one on purpose. A
`ScheduledBill` is somebody's statement about their own money; a
`RecurringSeries` is an inference drawn from rows the ledger already holds,
and an inference has no authority to move anything. **Nothing here is stored
and nothing here writes.** The series is recomputed from the ledger every time
it is asked for, which is what makes it correct itself: a charge that arrives
tomorrow fixes yesterday's guess with nothing to re-process, and a series
somebody cancelled stops being proposed on its own.

The four nouns, kept apart:

* `ScheduledBill` — declared by the owner. Persisted, and it is what can be
  charged.
* `BillOccurrence` — one charge of one period of a declared bill.
* `RecurringSeries` — this. A rhythm read off the history, with a confidence.
* `RecurringCharge` — in `financing.py`, and none of the above: the insurance
  a credit carries every period.

**Why the cadence is not measured in days alone.** Netflix charges on the 15th
and the gaps between those charges are 30, 31 and 31 days — never the same
number twice. A detector that demanded a constant gap would call the most
regular charge a person has irregular. So the month-based cadences are decided
by walking `BillCadence`'s own calendar from the first sighting, with the day
read off the anchor, and only the day-based ones are decided by the gap. Two
rules, because there are two kinds of rhythm, and one of them is a calendar's.

The tolerances widen with the cadence for a reason that is not symmetry: the
longer the period, the more calendar there is between charges for a weekend, a
holiday or a bank's own batch to move the day. Twenty days on something annual
is not laxity — it is one charge a year having to survive a year of drift.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
import enum
import itertools

from personal_finance.contexts.financial.domain.bills import BillCadence
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Money, ValueObject


# Three, never two. With two sightings there is no way to tell "monthly" from
# "it happened twice": any pair of dates has exactly one gap, and one gap is a
# coincidence with a number attached.
MIN_SIGHTINGS = 3

# How far back the history is read.
#
# Twenty-five months, and it was thirteen until building this showed thirteen
# cannot work: three sightings is the minimum for any cadence, and thirteen
# months hold **two** annual charges at most. An annual renewal — the domain,
# the antivirus, the SOAT — would therefore never have been suggested at all,
# silently, because "not a series" and "not enough window" look identical from
# outside. Two years plus a month of slack is what three annual charges need.
#
# It costs nothing to read: the ledger is loaded whole for one user either
# way, and the window only decides what is filtered out of it.
HISTORY_MONTHS = 25

# How many periods one gap may span before the series stops being one series.
# A subscription that missed two charges in a row and came back is plausible;
# a gap of four periods is two separate stretches of somebody's life, and
# treating them as one rhythm would predict a charge from a shape that is not
# there.
MAX_STEPS_PER_GAP = 3

# How far the amounts may spread before the charge counts as variable. Under
# it the next charge is simply the last one; over it — the phone bill, the
# electricity — the last one is noise and the median of the recent ones is the
# better guess. Discarding variable charges instead would drop exactly the
# bills people feel the most.
VARIABLE_SPREAD = Decimal("0.05")

# How many recent charges a variable series predicts from. Three: enough for
# one freak month not to decide the answer, few enough that last winter does
# not.
VARIABLE_WINDOW = 3

# How far past its expected day a charge may be before the series is late. A
# fifth of the cadence, and never under two days — the same shape as the
# grace a declared bill gets, but proportional, because what moves an annual
# charge is not what moves a weekly one.
MIN_GRACE_DAYS = 2
GRACE_SHARE = Decimal("0.2")

#: How far apart two charges of this cadence sit, as a plain number of days.
#: Only ever used to work out *how many* periods a gap spans; where the charge
#: actually lands is the calendar's answer, never this one.
NOMINAL_DAYS: dict[BillCadence, int] = {
    BillCadence.WEEKLY: 7,
    BillCadence.BIWEEKLY: 14,
    BillCadence.MONTHLY: 30,
    BillCadence.BIMONTHLY: 61,
    BillCadence.QUARTERLY: 91,
    BillCadence.ANNUAL: 365,
}

#: How far a charge may land from where the calendar expected it and still be
#: the same rhythm.
TOLERANCE_DAYS: dict[BillCadence, int] = {
    BillCadence.WEEKLY: 2,
    BillCadence.BIWEEKLY: 3,
    BillCadence.MONTHLY: 3,
    BillCadence.BIMONTHLY: 5,
    BillCadence.QUARTERLY: 10,
    BillCadence.ANNUAL: 20,
}


class SeriesState(enum.Enum):
    """Whether this rhythm is still going.

    `DORMANT` is the important one and it is why a detector needs a state at
    all: a cancelled subscription that keeps proposing itself, or keeps
    reminding, is worse than no detector — it teaches its owner to ignore the
    one screen that was supposed to be worth reading.
    """

    #: The next charge is still ahead, or within its grace.
    ACTIVE = "active"
    #: One expected charge has not turned up.
    LATE = "late"
    #: Two have not. The series stops predicting and stops being proposed.
    DORMANT = "dormant"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Sighting(ValueObject):
    """One charge the ledger already holds, as the detector reads it.

    Deliberately not a `Transaction`. What a rhythm is made of is a day and an
    amount, and handing the whole movement in would let this module start
    reading fields — a bank, a note, an account's state — that have nothing to
    do with whether something comes back every month.
    """

    occurred_on: dt.date
    amount: Money
    account_id: AccountId | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecurringSeries(ValueObject):
    """A rhythm found in the history, with how much to believe it.

    Carries no identity of its own — no merchant, no name. What these charges
    have in common is a question about the counterparty, which is Merchant's
    vocabulary and therefore the application's to answer; what this decides is
    only whether a set of dates and amounts is a rhythm at all.

    `amount` is what the *next* charge is expected to cost, which for a
    variable series is not any amount that has ever been charged.
    """

    cadence: BillCadence
    direction: MovementDirection
    amount: Money
    #: The amounts spread further than `VARIABLE_SPREAD`, so `amount` is a
    #: median rather than a repeat. Worth saying out loud: "about $90.000" and
    #: "$90.000" are different promises.
    variable: bool
    sightings: int
    #: Expected charges that never turned up, inside the stretch that was
    #: seen. Not a failure of the detector — a month the gym did not bill is a
    #: real thing, and it is what `confidence` is mostly made of.
    missed: int
    first_seen: dt.date
    last_seen: dt.date
    next_due_on: dt.date
    state: SeriesState
    #: How much to believe it, from 0 to 1. A number to sort by and to show,
    #: never a threshold anything acts on by itself.
    confidence: Decimal
    #: The account most of these charges landed on, when they landed on one.
    account_id: AccountId | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _Fit:
    """How well one cadence explains one set of dates."""

    cadence: BillCadence
    missed: int
    drift: Decimal


def detect_series(
    sightings: Sequence[Sighting],
    *,
    direction: MovementDirection,
    today: dt.date,
) -> RecurringSeries | None:
    """The rhythm these charges follow, or None when they follow none.

    None is the ordinary answer and the cheap one. A false negative is a
    suggestion nobody sees; a false positive is this app telling somebody to
    declare a bill for a restaurant they happened to visit three times, and
    then carrying that mistake into every forecast they read. The rules here
    are strict in that direction on purpose: **every** gap has to fit the
    cadence, so one stray charge at a merchant that is otherwise a
    subscription costs the suggestion rather than corrupting it.

    Every sighting must be in one currency — the caller groups them, and a
    group that mixed two would be a median with no unit. Raised rather than
    guessed at.
    """
    # Before the per-day collapse, not after: that collapse adds up what one
    # day held, and adding pesos to dollars is exactly what this refuses —
    # checking afterwards would let the sum happen and then find one currency
    # left to complain about.
    if len({sighting.amount.currency for sighting in sightings}) > 1:
        raise ValueError("A recurring series cannot mix currencies")

    charges = _by_day(sightings)

    if len(charges) < MIN_SIGHTINGS:
        return None

    dates = [sighting.occurred_on for sighting in charges]
    fit = _best_fit(dates)

    if fit is None:
        return None

    anchor = dates[0]
    last = dates[-1]
    next_due = fit.cadence.next_after(last, anchor=anchor)
    amount, variable = _next_amount([sighting.amount for sighting in charges])

    return RecurringSeries(
        cadence=fit.cadence,
        direction=direction,
        amount=amount,
        variable=variable,
        sightings=len(charges),
        missed=fit.missed,
        first_seen=anchor,
        last_seen=last,
        next_due_on=next_due,
        state=_state_of(fit.cadence, next_due=next_due, anchor=anchor, today=today),
        confidence=_confidence(fit, sightings=len(charges)),
        account_id=_usual_account(charges),
    )


def _by_day(sightings: Sequence[Sighting]) -> list[Sighting]:
    """One charge per day, oldest first, with the day's amounts added up.

    Two charges at the same merchant on the same day are not a rhythm — they
    are one day. Leaving both in would put a gap of zero into the series and
    break every cadence at once, which is the detector failing on a shape it
    ought to handle. Adding them is the honest collapse: what left the account
    that day is the sum, whatever it was split into.
    """
    by_day: dict[dt.date, Sighting] = {}

    for sighting in sorted(sightings, key=lambda each: each.occurred_on):
        found = by_day.get(sighting.occurred_on)

        if found is None:
            by_day[sighting.occurred_on] = sighting
            continue

        by_day[sighting.occurred_on] = dataclasses.replace(
            found,
            amount=Money(
                amount=found.amount.amount + sighting.amount.amount,
                currency=found.amount.currency,
            ),
        )

    return [by_day[day] for day in sorted(by_day)]


def _best_fit(dates: Sequence[dt.date]) -> _Fit | None:
    """The cadence that explains these dates with the fewest missing charges.

    Several cadences can fit the same dates, and the tie is not cosmetic: a
    fortnightly salary also "fits" weekly, as a weekly series that misses
    every other week. Fewest missing charges is what tells them apart, and it
    is the right rule in general — the cadence that has to invent the least in
    order to hold is the one the money is actually following.
    """
    fits = [
        fit
        for fit in (_fit(dates, cadence) for cadence in BillCadence)
        if fit is not None
    ]

    if not fits:
        return None

    return min(
        fits,
        key=lambda fit: (fit.missed, fit.drift, NOMINAL_DAYS[fit.cadence]),
    )


def _fit(dates: Sequence[dt.date], cadence: BillCadence) -> _Fit | None:
    """Whether every gap between these dates is this cadence, missing charges
    included. None the moment one is not.

    Each step is measured from the **previous charge**, never from a grid laid
    down at the start. A grid accumulates: a fortnightly salary paid on the
    15th and the 30th drifts six days away from a fourteen-day grid inside
    three months and stops matching, though nothing about it has changed.
    Measuring from the last charge is also what makes the tolerance mean what
    it says — "this charge landed within three days of where it was due" —
    rather than "within three days of where the first one implies it was due".
    """
    tolerance = TOLERANCE_DAYS[cadence]
    missed = 0
    drift = 0

    for previous, actual in itertools.pairwise(dates):
        steps = _steps_between(previous, actual, cadence)

        if steps is None:
            return None

        expected = _advance(previous, cadence, steps=steps, anchor=dates[0])
        deviation = abs((actual - expected).days)

        if deviation > tolerance:
            return None

        missed += steps - 1
        drift += deviation

    return _Fit(
        cadence=cadence,
        missed=missed,
        drift=Decimal(drift) / Decimal(len(dates) - 1),
    )


def _steps_between(
    previous: dt.date,
    actual: dt.date,
    cadence: BillCadence,
) -> int | None:
    """How many periods this gap spans, or None when it spans none this
    cadence can explain.

    The nominal length is used here and nowhere else: deciding *how many*
    periods fit in a gap is arithmetic, and deciding *where* the charge should
    have landed is the calendar's — which is why a monthly charge on the 31st
    is not compared against thirty days.
    """
    nominal = NOMINAL_DAYS[cadence]
    gap = (actual - previous).days
    steps = max(1, (gap + nominal // 2) // nominal)

    return None if steps > MAX_STEPS_PER_GAP else steps


def _advance(
    date: dt.date,
    cadence: BillCadence,
    *,
    steps: int,
    anchor: dt.date,
) -> dt.date:
    """`steps` charges on from `date`, by the calendar a declared bill uses.

    `BillCadence.next_after` rather than a second implementation: the day of a
    monthly charge is read off the anchor every time, so a series anchored on
    the 31st is still on the 31st in March after February borrowed the 28th.
    A detector with its own calendar would be a second answer to "when is the
    next charge", drifting from the first the day either was fixed.
    """
    for _ in range(steps):
        date = cadence.next_after(date, anchor=anchor)

    return date


def _next_amount(amounts: Sequence[Money]) -> tuple[Money, bool]:
    """What the next charge is expected to cost, and whether that is a guess.

    Two ways of being fixed, and the second one is the whole subtlety.

    **Steady.** Every charge within five per cent of the middle one: the gym
    costs what the gym costs, and the answer is the last figure.

    **Steady until it went up.** Everything *except* the last within five per
    cent, and the last one somewhere else. That is a price rise, not noise, and
    the new price is the price now — quoting the median would keep forecasting
    the old one for as long as the old charges outnumber the new. This is the
    case a plain spread check gets exactly backwards: a subscription that rose
    from 16 900 to 19 900 spreads wider than five per cent, so it would be
    called variable and then predicted at 16 900 — the one figure certain to
    be wrong next month.

    **Variable.** Anything else. The phone bill and the electricity move every
    month and no single charge is the answer, so it is the median of the
    recent ones, and the screen says «≈» beside it. Dropping these instead
    would lose exactly the bills people feel.
    """
    currency = amounts[0].currency
    figures = [amount.amount for amount in amounts]

    if _tight(figures) or _tight(figures[:-1]):
        return Money(amount=figures[-1], currency=currency), False

    return (
        Money(amount=_median(figures[-VARIABLE_WINDOW:]), currency=currency),
        True,
    )


def _tight(figures: Sequence[Decimal]) -> bool:
    """Whether these figures are all the same charge, give or take.

    False for fewer than two: one figure is trivially consistent with itself,
    and letting that count would call every series fixed on the strength of
    its last charge alone.
    """
    if len(figures) < 2:
        return False

    middle = _median(figures)

    if middle <= 0:
        return False

    return max(abs(figure - middle) for figure in figures) / middle <= VARIABLE_SPREAD


def _median(figures: Sequence[Decimal]) -> Decimal:
    """The middle figure, and the lower one of the two when there is no middle.

    Never the average of the two: an average invents a figure nobody was ever
    charged, and on money it invents fractions of a cent along with it.
    """
    return sorted(figures)[(len(figures) - 1) // 2]


def _state_of(
    cadence: BillCadence,
    *,
    next_due: dt.date,
    anchor: dt.date,
    today: dt.date,
) -> SeriesState:
    grace = dt.timedelta(days=_grace_days(cadence))

    if today <= next_due + grace:
        return SeriesState.ACTIVE

    following = _advance(next_due, cadence, steps=1, anchor=anchor)

    return SeriesState.LATE if today <= following + grace else SeriesState.DORMANT


def _grace_days(cadence: BillCadence) -> int:
    return max(
        MIN_GRACE_DAYS,
        int(GRACE_SHARE * NOMINAL_DAYS[cadence]),
    )


def _confidence(fit: _Fit, *, sightings: int) -> Decimal:
    """How much to believe the rhythm, from 0 to 1.

    Three things, and none of them is enough alone. **Completeness** — how
    many of the charges the cadence expected actually turned up — is the
    strongest signal that this is a subscription and not a coincidence.
    **Punctuality** is how close to its day each charge landed, as a fraction
    of what this cadence tolerates. **Evidence** is simply how many charges
    there are: three is the minimum that means anything and six is as much as
    this asks for, because past that the other two are saying it better.

    The weights are a judgement, not a measurement. What they are for is
    ordering a list somebody reads top-down — not deciding anything, which is
    why nothing in this feature has a threshold.
    """
    completeness = Decimal(sightings) / Decimal(sightings + fit.missed)
    tolerance = Decimal(TOLERANCE_DAYS[fit.cadence])
    punctuality = max(Decimal(0), Decimal(1) - fit.drift / tolerance)
    evidence = min(Decimal(1), Decimal(sightings) / Decimal(6))
    score = (
        Decimal("0.40") * completeness
        + Decimal("0.35") * punctuality
        + Decimal("0.25") * evidence
    )

    return min(Decimal(1), max(Decimal(0), score)).quantize(Decimal("0.01"))


def _usual_account(sightings: Sequence[Sighting]) -> AccountId | None:
    """The account these charges mostly landed on, for a suggestion to prefill.

    Mostly and not always: a subscription moved from one card to another has
    both in its history, and the one it is on now is the one it is charged
    from — so ties go to the most recent, which is what `max` over a reversed
    walk gives.
    """
    seen: dict[AccountId, int] = {}

    for sighting in reversed(sightings):
        if sighting.account_id is not None:
            seen[sighting.account_id] = seen.get(sighting.account_id, 0) + 1

    if not seen:
        return None

    return max(seen, key=lambda account: seen[account])
