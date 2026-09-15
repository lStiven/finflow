"""Reading the ledger for rhythms nobody declared, and only ever proposing.

The detector's half of the bills feature. It reads two years of movements,
groups them by who they were with, and asks the domain whether each group is a
rhythm. **It writes nothing.** Accepting a suggestion is declaring a
bill, through the same endpoint anybody declares one with, which is what keeps
a heuristic from ever being the thing that moves money.

Three things decide whether this is useful rather than noisy, and all three
live here rather than in the domain, because all three are questions about
other people's vocabulary or about what is already on file:

* **Who the charges were with.** The key is the `merchant_id` Merchant
  attributed, not the text the bank wrote: the same gym arrives as `PAGO GYM
  SA` one month and `GYMSA*BOG` the next, and grouping by text would see two
  series of half the length — which is often two series of two, and therefore
  none. Only when no merchant claims a spelling does the normalized text stand
  in.
* **What must never be in the list.** The accruals this app computes itself
  are perfectly monthly and perfectly identical, so unfiltered they would head
  the ranking of "subscriptions" the app found. So would the charges this
  feature writes: a confirmed bill charge lands with `TransactionOrigin.\
SCHEDULED`, and proposing to declare a bill that is already declared is the
  detector reading its own handwriting. And transfers — paying the card from
  savings every month is the most regular charge anybody has, and it is not a
  subscription.
* **What is already declared.** Matched the same way the grouping is —
  counterparty, direction and currency — and under both spellings of the
  bill: the merchant behind its name, so a gym declared as "Gimnasio" is
  recognised in charges that say `GYMSA*BOG`, and the folded name itself, so
  a bill nothing has ever attributed still marks its own series instead of
  being proposed again for ever.

Income is detected and marked with its direction rather than dropped. A salary
is a recurring series of the textbook kind and E3 wants it for the month's
expected income; what it must never do is be netted against spending, which is
why nothing here totals anything.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt

from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.ports import (
    MerchantAttribution,
    MerchantDirectory,
    MovementHistory,
    ScheduledBillRepository,
)
from personal_finance.contexts.financial.domain.bills import BillId, ScheduledBill
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.recurring import (
    HISTORY_MONTHS,
    RecurringSeries,
    SeriesState,
    Sighting,
    detect_series,
)
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
    TransactionOrigin,
    normalize_counterparty,
)
from personal_finance.shared.domain.value_objects import Currency, UserId


#: Origins that can never be evidence of a rhythm somebody would want to
#: declare, because this application wrote them itself. `ACCRUAL` is the
#: interest and the insurance a credit charges every cut; `SCHEDULED` is a
#: bill charge somebody already confirmed.
SELF_WRITTEN = frozenset({TransactionOrigin.ACCRUAL, TransactionOrigin.SCHEDULED})

#: How many series one answer carries. A bound rather than a page: the list is
#: read top-down and nobody acts on the eightieth suggestion, so the honest
#: shape is "the best of them" rather than a cursor into a guess.
MAX_SERIES = 40


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DetectedSeries:
    """One rhythm, with who it was with and whether it is already declared.

    `bill_id` is the whole reason the merchant is resolved at all: a
    suggestion to declare something already declared is worse than no
    suggestion, because it makes the reader distrust the rest of the list.
    """

    series: RecurringSeries
    #: Stable across calls for the same group, so a screen can key on it. The
    #: merchant's id, or the folded counterparty text when no merchant owns
    #: that spelling.
    key: str
    #: What to show. The canonical merchant name where there is one, and
    #: otherwise the counterparty text exactly as the bank wrote it.
    name: str
    merchant_id: str | None
    category: str | None
    #: The bill already covering this, when one does.
    bill_id: BillId | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecurringView:
    #: The stretch of history that was read, so a screen can say what "nothing
    #: found" was looked for in.
    since: dt.date
    until: dt.date
    series: tuple[DetectedSeries, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DetectRecurringQuery:
    user_id: UserId
    timezone: str


@dataclasses.dataclass(slots=True)
class _Group:
    """The charges of one counterparty, in one currency and one direction.

    All three are part of the key. Two currencies in one group would be a
    median with no unit; two directions would be a refund folded into the
    charge it reverses, and the rhythm of neither.
    """

    #: The three of them together, which is what a screen keys on and what a
    #: declared bill is matched against. The counterparty alone is not enough:
    #: a merchant billing in two currencies is two series, and one of them
    #: being declared says nothing about the other.
    identity: GroupKey
    key: str
    name: str
    merchant_id: str | None
    category: str | None
    direction: MovementDirection
    currency: Currency
    sightings: list[Sighting] = dataclasses.field(default_factory=list[Sighting])


class DetectRecurringSeriesUseCase:
    """What comes back on its own, as far as the history can tell.

    Reads the whole of one user's ledger and narrows it here rather than
    through `MovementFilter`: that filter takes one origin, and what this
    needs is to exclude two — plus the transfers, which it can express and
    which are excluded for a different reason.

    It reads through `MovementHistory` rather than the ledger itself. A
    detector has no business being able to write, and a port that cannot is
    the cheapest way to say so.

    The merchant directory is optional, like everywhere else in this context.
    Without it every group falls back to the folded counterparty text, so the
    detector still works and simply groups less well — an enrichment that is
    down must not take a feature with it.
    """

    def __init__(
        self,
        *,
        ledger: MovementHistory,
        bills: ScheduledBillRepository,
        merchants: MerchantDirectory | None = None,
    ) -> None:
        self._ledger = ledger
        self._bills = bills
        self._merchants = merchants

    def execute(self, query: DetectRecurringQuery) -> RecurringView:
        zone = zone_of(query.timezone)
        today = today_in(zone)
        since = _months_before(today, months=HISTORY_MONTHS)

        movements = [
            movement
            for movement in self._ledger.list_all(query.user_id)
            if _is_evidence(movement) and since <= _day_of(movement, zone) <= today
        ]
        declared = list(self._bills.list_by_user(query.user_id))
        # One round trip for both sides of the question: what the movements
        # were with, and what the declared bills are with. Asking twice would
        # be two answers that could disagree about the same spelling.
        attributions = self._attribute(
            query.user_id,
            counterparties=[
                *(movement.counterparty for movement in movements),
                *(bill.name for bill in declared),
            ],
        )
        covered = _already_declared(declared, attributions)

        found = [
            detected
            for group in _grouped(movements, attributions, zone=zone).values()
            if (detected := _detected(group, today=today, covered=covered)) is not None
        ]
        found.sort(
            key=lambda each: (
                -each.series.confidence,
                -each.series.sightings,
                each.name.casefold(),
            ),
        )

        return RecurringView(
            since=since,
            until=today,
            series=tuple(found[:MAX_SERIES]),
        )

    def _attribute(
        self,
        user_id: UserId,
        *,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        if self._merchants is None:
            return {}

        wanted = sorted(set(counterparties))

        if not wanted:
            return {}

        return self._merchants.attribute(user_id=user_id, counterparties=wanted)


def _is_evidence(movement: Transaction) -> bool:
    """Whether this movement can say anything about a rhythm worth declaring.

    The three exclusions of the plan, and each for its own reason. What this
    app computed (`ACCRUAL`) and what this feature wrote (`SCHEDULED`) are
    perfectly regular by construction and would head the list. A transfer
    between somebody's own accounts is the most regular charge anybody has and
    is not spending at all.
    """
    return movement.origin not in SELF_WRITTEN and not movement.is_transfer


type GroupKey = tuple[str, MovementDirection, Currency]


def _grouped(
    movements: Sequence[Transaction],
    attributions: Mapping[str, MerchantAttribution],
    *,
    zone: dt.tzinfo,
) -> dict[GroupKey, _Group]:
    """The charges, gathered by who they were with.

    Three things make the key, and the day of each charge is read where its
    owner lives rather than in UTC — see `_day_of`.
    """
    groups: dict[GroupKey, _Group] = {}

    for movement in movements:
        attribution = attributions.get(movement.counterparty)
        key = _key_of(movement.counterparty, attribution)
        currency = movement.amount.currency
        identity = (key, movement.direction, currency)
        group = groups.get(identity)

        if group is None:
            group = _Group(
                identity=identity,
                key=key,
                name=(
                    movement.counterparty
                    if attribution is None
                    else attribution.display_name
                ),
                merchant_id=None if attribution is None else attribution.merchant_id,
                category=None if attribution is None else attribution.category,
                direction=movement.direction,
                currency=currency,
            )
            groups[identity] = group

        group.sightings.append(
            Sighting(
                occurred_on=_day_of(movement, zone),
                amount=movement.amount,
                account_id=movement.account_id,
            ),
        )

    return groups


def _day_of(movement: Transaction, zone: dt.tzinfo) -> dt.date:
    """The calendar day this charge happened on, where its owner lives.

    Never in UTC. A charge at nine in the evening in Bogotá is the 15th
    there and the 16th in UTC, and a monthly series whose days alternate
    between the two has no cadence anything can recognise.
    """
    return movement.occurred_at.to_datetime().astimezone(zone).date()


def _key_of(counterparty: str, attribution: MerchantAttribution | None) -> str:
    """The merchant, or the folded text when no merchant owns the spelling.

    Prefixed so the two spaces cannot collide: a merchant id and a piece of
    normalized text are different kinds of answer, and a key that could be
    either would group them together the day one looked like the other.
    """
    if attribution is not None:
        return f"merchant:{attribution.merchant_id}"

    return f"text:{normalize_counterparty(counterparty)}"


def _already_declared(
    bills: Sequence[ScheduledBill],
    attributions: Mapping[str, MerchantAttribution],
) -> dict[GroupKey, BillId]:
    """Which series a declared bill already covers.

    Keyed exactly the way the movements are — counterparty, direction and
    currency — because one of those three being different makes it a different
    commitment: a bill declared in pesos says nothing about the same
    merchant's charges in dollars, and a declared salary says nothing about
    money going out.

    **Two entries per bill, and that is the point.** Through the merchant
    behind its name, which is how a bill called "Gimnasio" is recognised in
    charges that say `GYMSA*BOG`; and through the folded name itself, which is
    the only thing that works for the ordinary case where nothing has ever
    attributed that name — a bill somebody typed, or one this very screen
    created by accepting a suggestion. With only the first, accepting a
    suggestion would leave it un-marked and the list would go on offering it,
    making a second bill on every click.

    Paused bills count. A paused bill is the record of something its owner
    decided about, and proposing they declare it again is the suggestion list
    arguing with a decision they already made.
    """
    covered: dict[GroupKey, BillId] = {}

    for bill in bills:
        currency = bill.amount.currency

        for key in _bill_keys(bill, attributions.get(bill.name)):
            covered[(key, bill.direction, currency)] = bill.id

    return covered


def _bill_keys(
    bill: ScheduledBill,
    attribution: MerchantAttribution | None,
) -> list[str]:
    """Every counterparty key this bill could be recognised under."""
    text = f"text:{normalize_counterparty(bill.name)}"

    if attribution is None:
        return [text]

    return [f"merchant:{attribution.merchant_id}", text]


def _covering_bill(
    group: _Group,
    covered: Mapping[GroupKey, BillId],
) -> BillId | None:
    """The declared bill this series is already covered by, if any.

    Asked under both of the group's own spellings for the reason
    `_already_declared` gives: the charges may be attributed to a merchant
    while the bill's name is not, and then only the folded text can match
    them up.
    """
    _, direction, currency = group.identity
    fallback = f"text:{normalize_counterparty(group.name)}"

    return covered.get(group.identity) or covered.get((fallback, direction, currency))


def _public_key(identity: GroupKey) -> str:
    """The identity as one string, for a client to key a row on.

    The currency and the direction are in it because two of them can share a
    counterparty: the same merchant billing in pesos and in dollars is two
    series, and a key that dropped the currency would give a list two rows
    with one key — duplicated in the DOM, and either of them marked declared
    the moment the other was.
    """
    key, direction, currency = identity

    return f"{key}|{direction.value}|{currency.value}"


def _detected(
    group: _Group,
    *,
    today: dt.date,
    covered: Mapping[GroupKey, BillId],
) -> DetectedSeries | None:
    """One group as a suggestion, or None when it is not one.

    Dormant series are dropped rather than returned with their state. Two
    missed charges means the subscription was cancelled or the merchant is
    gone, and a list that proposes declaring those is a list somebody stops
    reading — which costs the suggestions that were worth acting on.
    """
    series = detect_series(
        group.sightings,
        direction=group.direction,
        today=today,
    )

    if series is None or series.state is SeriesState.DORMANT:
        return None

    return DetectedSeries(
        series=series,
        key=_public_key(group.identity),
        name=group.name,
        merchant_id=group.merchant_id,
        category=group.category,
        bill_id=_covering_bill(group, covered),
    )


def _months_before(day: dt.date, *, months: int) -> dt.date:
    """The same day, that many months back, clamped to the month's end.

    Its own small walk rather than the calendar in `bills`: what that one
    answers is where a *charge* lands, which is a rule about somebody's money.
    This is only the far edge of a window to read.
    """
    total = day.month - 1 - months
    year = day.year + total // 12
    month = total % 12 + 1
    last = _last_day_of(year, month)

    return dt.date(year, month, min(day.day, last))


def _last_day_of(year: int, month: int) -> int:
    following = dt.date(year + month // 12, month % 12 + 1, 1)

    return (following - dt.timedelta(days=1)).day
