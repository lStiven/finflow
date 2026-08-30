"""Read side: what an accounts screen, a net-worth figure and a spending
summary need.

Filtering happens here rather than in the index, for the same reason it does
in Merchant: a person has a handful of accounts and their movements live in
one partition, and keeping the answer in one place beats three queries that
can disagree. This is the seam to push down if anyone ever outgrows it.

Two things are joined on the way out. **Who the movement was with** comes from
Merchant, through a port, because Financial stores what the bank wrote and the
canonical merchant behind that text is somebody else's decision — one a user
can change later, which is why the join is made on every read instead of being
stamped onto the row. And **what a period adds up to** is computed here rather
than left to a client paging two hundred movements at a time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
import enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    MerchantAttribution,
    MerchantDirectory,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountId,
    MovementDirection,
    TransactionOrigin,
)
from personal_finance.shared.domain.value_objects import Currency, PosixTime, UserId


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Every bank this reads emails from Colombia, and a purchase at 8pm on the
# 31st belongs to that month rather than to the next one an hour later in UTC.
# A client in another timezone says so; nothing here guesses from a locale.
DEFAULT_TIMEZONE = "America/Bogota"

# The buckets a grouping cannot place. They are part of the answer — dropping
# them would stop the parts adding up to the total — so they carry a name a
# client can show and translate.
UNATTRIBUTED_LABEL = "Unattributed"
UNASSIGNED_LABEL = "Unassigned"


class AccountScope(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class NetWorth:
    """Assets minus liabilities, and the two halves it came from.

    Per currency, never summed across them: converting would need an exchange
    rate, which is a fact about a moment nobody recorded here.
    """

    currency: Currency
    assets: Decimal
    liabilities: Decimal

    @property
    def total(self) -> Decimal:
        return self.assets - self.liabilities


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountsView:
    accounts: Sequence[Account]
    # One entry per currency the user holds. Empty when they hold no accounts,
    # which is an ordinary state and not an error.
    net_worth: Sequence[NetWorth]


class ListAccountsUseCase:
    """Every account a user declared, with what each holds now."""

    def __init__(self, *, accounts: AccountRepository) -> None:
        self._accounts = accounts

    def execute(
        self,
        *,
        user_id: UserId,
        scope: AccountScope = AccountScope.OPEN,
    ) -> AccountsView:
        held = [
            account
            for account in self._accounts.list_by_user(user_id)
            if _in_scope(account, scope)
        ]
        held.sort(key=lambda account: account.name.casefold())

        return AccountsView(accounts=held, net_worth=_net_worth(held))


class GetAccountUseCase:
    def __init__(self, *, accounts: AccountRepository) -> None:
        self._accounts = accounts

    def execute(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        return self._accounts.find(user_id=user_id, account_id=account_id)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MovementFilter:
    """What narrows a set of movements. Shared by the list and the summary,
    so "spent at this merchant in August" means the same thing to both.
    """

    user_id: UserId
    account_id: AccountId | None = None
    # True lists only what no account claimed — the queue somebody works
    # through after declaring an account, or the whole ledger for somebody who
    # declared none.
    unassigned: bool | None = None
    origin: TransactionOrigin | None = None
    # Money in or money out. Its own filter rather than a category, because
    # direction is a property of the movement while a category is Merchant's
    # answer about the counterparty — `income` the category and `incoming` the
    # direction disagree on a refund.
    direction: MovementDirection | None = None
    search: str | None = None
    # The merchant behind the counterparty text, and the kind of spending it
    # is. Both are Merchant's answer, joined on the way through, so a
    # movement no sighting has resolved yet matches neither.
    merchant_id: str | None = None
    category: str | None = None
    # Half-open on purpose: `since` is included and `until` is not, so two
    # consecutive periods can be asked for without one movement landing in
    # both of them.
    since: PosixTime | None = None
    until: PosixTime | None = None

    @property
    def needs_attribution(self) -> bool:
        return self.merchant_id is not None or self.category is not None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionQuery:
    filter: MovementFilter
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AttributedTransaction:
    """A movement and who it was with, if anybody has decided that yet.

    `merchant` is None for a counterparty no merchant owns — while the
    sighting is still on merchant's queue, and permanently for a movement
    entered by hand under a name nothing else has ever seen. It is an
    enrichment, never a precondition: the movement reads the same without it.
    """

    transaction: Transaction
    merchant: MerchantAttribution | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionPage:
    transactions: Sequence[AttributedTransaction]
    total: int


class ListTransactionsUseCase:
    """Movements, newest first, filtered the way a screen asks for them."""

    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        merchants: MerchantDirectory | None = None,
    ) -> None:
        self._ledger = ledger
        self._merchants = merchants

    def execute(self, query: TransactionQuery) -> TransactionPage:
        found = _narrow(_load(self._ledger, query.filter), query.filter)
        attributions = _attribute(
            self._merchants,
            user_id=query.filter.user_id,
            # Everything matched so far, not only the page: filtering by
            # merchant has to run before the window is cut, or `total` would
            # count movements the filter rejects.
            movements=found,
        )
        found = _matching_merchant(found, query.filter, attributions)

        # Newest first: what somebody looks at when they open the app.
        found.sort(
            key=lambda movement: movement.occurred_at.as_epoch_seconds(),
            reverse=True,
        )
        window = found[query.offset : query.offset + query.limit]

        return TransactionPage(
            transactions=[
                AttributedTransaction(
                    transaction=movement,
                    merchant=attributions.get(movement.counterparty),
                )
                for movement in window
            ],
            total=len(found),
        )


class GetTransactionUseCase:
    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        merchants: MerchantDirectory | None = None,
    ) -> None:
        self._ledger = ledger
        self._merchants = merchants

    def execute(
        self,
        *,
        user_id: UserId,
        transaction_id: str,
    ) -> AttributedTransaction | None:
        movement = self._ledger.find(user_id=user_id, transaction_id=transaction_id)

        if movement is None:
            return None

        attributions = _attribute(
            self._merchants,
            user_id=user_id,
            movements=[movement],
        )

        return AttributedTransaction(
            transaction=movement,
            merchant=attributions.get(movement.counterparty),
        )


class SummaryGrouping(enum.Enum):
    """Which question a summary answers."""

    MONTH = "month"
    CATEGORY = "category"
    MERCHANT = "merchant"
    ACCOUNT = "account"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SummaryQuery:
    filter: MovementFilter
    group_by: SummaryGrouping = SummaryGrouping.MONTH
    # Only months need it, and they need it badly: a purchase at 8pm on the
    # 31st is the next month in UTC, which is the wrong answer for everybody
    # this deployment serves.
    timezone: str = DEFAULT_TIMEZONE


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SpendingTotals:
    """What moved, in one currency. Never summed across two of them: that
    would need an exchange rate, which is a fact about a moment nobody
    recorded here.
    """

    currency: Currency
    incoming: Decimal
    outgoing: Decimal
    movements: int

    @property
    def net(self) -> Decimal:
        return self.incoming - self.outgoing


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SummaryGroup:
    """One bucket of the breakdown.

    `key` is None for the bucket this grouping cannot place: a movement no
    account claimed, or a counterparty no merchant owns yet. That bucket is
    part of the answer, not an error — leaving it out would make the parts
    stop adding up to the total.
    """

    key: str | None
    label: str
    totals: Sequence[SpendingTotals]
    # Across every currency in the bucket, which is what orders the groups: a
    # count means the same thing in two currencies and an amount does not.
    movements: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SpendingSummary:
    group_by: SummaryGrouping
    # Over everything the filter matched, so a client can show a period total
    # beside its breakdown without adding the buckets up itself.
    totals: Sequence[SpendingTotals]
    groups: Sequence[SummaryGroup]


class SummarizeSpendingUseCase:
    """What a period adds up to, broken down the way a screen asks.

    This exists because the alternative is a client paging two hundred
    movements at a time to add up a year, which is fine for a demo and absurd
    for real history. It is still the same in-memory pass every read here
    makes — one partition, one user — so what it saves is the round trips and
    the client-side arithmetic, not the scan.
    """

    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        accounts: AccountRepository,
        merchants: MerchantDirectory | None = None,
    ) -> None:
        self._ledger = ledger
        self._accounts = accounts
        self._merchants = merchants

    def execute(self, query: SummaryQuery) -> SpendingSummary:
        zone = _zone(query.timezone)
        criteria = query.filter
        found = _narrow(_load(self._ledger, criteria), criteria)
        attributions = _attribute(
            self._merchants,
            user_id=criteria.user_id,
            movements=found,
            # Grouping by merchant or by category needs them; so does filtering
            # on either. A breakdown by month or by account needs neither.
            wanted=query.group_by
            in {SummaryGrouping.MERCHANT, SummaryGrouping.CATEGORY}
            or criteria.needs_attribution,
        )
        found = _matching_merchant(found, criteria, attributions)
        # Only a breakdown by account needs them, and it needs them to say
        # `Tarjeta Bancolombia` where the row only holds a uuid.
        names = (
            self._account_names(criteria.user_id)
            if query.group_by is SummaryGrouping.ACCOUNT
            else dict[str, str]()
        )
        buckets: dict[str | None, list[Transaction]] = {}
        labels: dict[str | None, str] = {}

        for movement in found:
            key, label = _bucket(
                movement,
                group_by=query.group_by,
                attribution=attributions.get(movement.counterparty),
                zone=zone,
                account_names=names,
            )
            buckets.setdefault(key, []).append(movement)
            labels.setdefault(key, label)

        groups = [
            SummaryGroup(
                key=key,
                label=labels[key],
                totals=_totals(movements),
                movements=len(movements),
            )
            for key, movements in buckets.items()
        ]
        _sort_groups(groups, query.group_by)

        return SpendingSummary(
            group_by=query.group_by,
            totals=_totals(found),
            groups=groups,
        )

    def _account_names(self, user_id: UserId) -> Mapping[str, str]:
        return {
            str(account.id.value): account.name
            for account in self._accounts.list_by_user(user_id)
        }


def _in_scope(account: Account, scope: AccountScope) -> bool:
    if scope is AccountScope.ALL:
        return True

    return account.is_closed == (scope is AccountScope.CLOSED)


def _net_worth(accounts: Sequence[Account]) -> Sequence[NetWorth]:
    """Assets minus liabilities, one figure per currency.

    A closed account still counts: a paid-off loan sitting at zero changes
    nothing, and one closed with a balance is money that is still somewhere.
    """
    assets: dict[Currency, Decimal] = {}
    liabilities: dict[Currency, Decimal] = {}

    for account in accounts:
        side = assets if account.category is AccountCategory.ASSET else liabilities
        side[account.currency] = (
            side.get(account.currency, Decimal(0)) + account.balance.signed_amount
        )

    return [
        NetWorth(
            currency=currency,
            assets=assets.get(currency, Decimal(0)),
            liabilities=liabilities.get(currency, Decimal(0)),
        )
        for currency in sorted(
            set(assets) | set(liabilities),
            key=lambda currency: currency.value,
        )
    ]


def _load(
    ledger: TransactionLedger,
    criteria: MovementFilter,
) -> list[Transaction]:
    """The narrowest read the ledger can do for this filter.

    An account and "unassigned" are the two the storage can answer itself;
    everything else is applied over what comes back.
    """
    if criteria.account_id is not None:
        return list(
            ledger.list_movements(
                user_id=criteria.user_id,
                account_id=criteria.account_id,
            ),
        )

    if criteria.unassigned:
        return list(ledger.list_unassigned(criteria.user_id))

    return list(ledger.list_all(criteria.user_id))


def _narrow(
    movements: list[Transaction],
    criteria: MovementFilter,
) -> list[Transaction]:
    """Everything the ledger cannot filter on its own.

    `account_id` and `unassigned` already shaped the read; these are the rest,
    applied over one user's partition in memory for the same reason the whole
    module does it that way.
    """
    found = movements

    if criteria.unassigned is False:
        found = [movement for movement in found if movement.account_id is not None]

    if criteria.origin is not None:
        found = [movement for movement in found if movement.origin is criteria.origin]

    if criteria.direction is not None:
        found = [
            movement for movement in found if movement.direction is criteria.direction
        ]

    if criteria.since is not None:
        floor = criteria.since.as_epoch_seconds()
        found = [
            movement
            for movement in found
            if movement.occurred_at.as_epoch_seconds() >= floor
        ]

    if criteria.until is not None:
        ceiling = criteria.until.as_epoch_seconds()
        found = [
            movement
            for movement in found
            if movement.occurred_at.as_epoch_seconds() < ceiling
        ]

    if criteria.search:
        needle = criteria.search.strip().casefold()
        found = [
            movement for movement in found if needle in movement.counterparty.casefold()
        ]

    return found


def _attribute(
    merchants: MerchantDirectory | None,
    *,
    user_id: UserId,
    movements: Iterable[Transaction],
    wanted: bool = True,
) -> Mapping[str, MerchantAttribution]:
    """Who each of these movements was with, keyed by counterparty text.

    Empty when no directory is wired, which is a working configuration and not
    a broken one: every movement then reads without a merchant, exactly as it
    did before this join existed. Empty too when nothing in the answer would
    show or group by a merchant — a breakdown by month has no use for one, and
    reading somebody's whole merchant list to ignore it is a round trip for
    nothing.
    """
    if merchants is None or not wanted:
        return {}

    counterparties = sorted({movement.counterparty for movement in movements})

    if not counterparties:
        return {}

    return merchants.attribute(user_id=user_id, counterparties=counterparties)


def _matching_merchant(
    movements: list[Transaction],
    criteria: MovementFilter,
    attributions: Mapping[str, MerchantAttribution],
) -> list[Transaction]:
    """Applied after the attribution, because that is where the answer is.

    A movement nobody has resolved yet matches neither filter — it is not
    "uncategorized", it is unknown, and reporting it under a category would
    put spending in a bucket Merchant never placed it in.
    """
    if not criteria.needs_attribution:
        return movements

    def matches(movement: Transaction) -> bool:
        attribution = attributions.get(movement.counterparty)

        if attribution is None:
            return False

        if (
            criteria.merchant_id is not None
            and attribution.merchant_id != criteria.merchant_id
        ):
            return False

        return criteria.category is None or attribution.category == criteria.category

    return [movement for movement in movements if matches(movement)]


def _bucket(
    movement: Transaction,
    *,
    group_by: SummaryGrouping,
    attribution: MerchantAttribution | None,
    zone: dt.tzinfo,
    account_names: Mapping[str, str],
) -> tuple[str | None, str]:
    """Which group this movement falls in, and what to call it."""
    match group_by:
        case SummaryGrouping.MONTH:
            month = (
                movement.occurred_at.to_datetime().astimezone(zone).strftime("%Y-%m")
            )

            return month, month
        case SummaryGrouping.CATEGORY:
            if attribution is None:
                return None, UNATTRIBUTED_LABEL

            return attribution.category, attribution.category
        case SummaryGrouping.MERCHANT:
            if attribution is None:
                return None, UNATTRIBUTED_LABEL

            return attribution.merchant_id, attribution.display_name
        case SummaryGrouping.ACCOUNT:
            if movement.account_id is None:
                return None, UNASSIGNED_LABEL

            account_id = str(movement.account_id.value)

            return account_id, account_names.get(account_id, account_id)


def _totals(movements: Sequence[Transaction]) -> Sequence[SpendingTotals]:
    """One figure per currency, ordered by its code so the answer is stable."""
    incoming: dict[Currency, Decimal] = {}
    outgoing: dict[Currency, Decimal] = {}
    counted: dict[Currency, int] = {}

    for movement in movements:
        currency = movement.amount.currency
        side = (
            incoming if movement.direction is MovementDirection.INCOMING else outgoing
        )
        side[currency] = side.get(currency, Decimal(0)) + movement.amount.amount
        counted[currency] = counted.get(currency, 0) + 1

    return [
        SpendingTotals(
            currency=currency,
            incoming=incoming.get(currency, Decimal(0)),
            outgoing=outgoing.get(currency, Decimal(0)),
            movements=counted[currency],
        )
        for currency in sorted(counted, key=lambda currency: currency.value)
    ]


def _sort_groups(groups: list[SummaryGroup], group_by: SummaryGrouping) -> None:
    """Months run newest first; everything else runs busiest first.

    Busiest, not biggest: ordering by amount would have to compare a figure in
    one currency against a figure in another, and no rate here says what that
    means. A client showing one currency has every amount it needs to reorder
    them itself.
    """
    if group_by is SummaryGrouping.MONTH:
        groups.sort(key=lambda group: group.key or "", reverse=True)

        return

    groups.sort(key=lambda group: (-group.movements, group.label.casefold()))


def _zone(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown timezone: {name!r}") from error


# ----------------------------------------------------------------- history


MAX_HISTORY_MONTHS = 36
DEFAULT_HISTORY_MONTHS = 12


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MonthlyPoint:
    """One month of the run, closed off at the end of what it actually covers."""

    key: str
    starts_at: PosixTime
    # Exclusive, and for the month still being lived it is *now* rather than
    # the month's end: the totals and the net worth beside it both stop here,
    # so the three always describe the same window.
    ends_at: PosixTime
    partial: bool
    totals: Sequence[SpendingTotals]
    # What everything was worth at `ends_at`. Replayed from the ledger, never
    # stored.
    net_worth: Sequence[NetWorth]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PeriodComparison:
    """This month so far, against the same stretch of the month before it.

    Aligned by day of the month rather than by elapsed time: on the 15th this
    is the 1st to the 15th against the 1st to the 15th, which is what somebody
    means by "versus last month". Comparing a month three days old against a
    finished one is the mistake this exists to prevent — it reports spending
    down by most of it every month and is right about nothing.
    """

    key: str
    starts_at: PosixTime
    through: PosixTime
    previous_key: str
    previous_starts_at: PosixTime
    previous_through: PosixTime
    # The previous month ran out of days first — the 31st against a February.
    # The window is its whole length, and saying so is the caller's job.
    clamped: bool
    totals: Sequence[SpendingTotals]
    previous_totals: Sequence[SpendingTotals]
    net_worth: Sequence[NetWorth]
    previous_net_worth: Sequence[NetWorth]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class FinancialHistory:
    timezone: str
    # Oldest first, so a client draws it without reversing anything.
    months: Sequence[MonthlyPoint]
    comparison: PeriodComparison


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class HistoryQuery:
    user_id: UserId
    months: int = DEFAULT_HISTORY_MONTHS
    timezone: str = DEFAULT_TIMEZONE
    # Injectable so a test can stand at a chosen instant. Production leaves it
    # None and the clock answers.
    now: PosixTime | None = None


class ReadFinancialHistoryUseCase:
    """How this user's money has moved month by month, and how the current
    month compares with the one before it.

    Nothing here is stored or scheduled. Restating what an account holds
    solves its opening balance backwards, so `opening_balance` plus every
    movement up to an instant *is* the balance at that instant — history is a
    replay of the ledger, and a snapshot table would only be a second copy to
    keep in step.

    The consequence worth knowing: this is the best current reconstruction of
    the past, not a log of what was believed at the time. Declaring an account
    today, or correcting a balance, changes what last March reports — which is
    right, and is the same property that makes adoption retroactive.
    """

    def __init__(
        self,
        *,
        ledger: TransactionLedger,
        accounts: AccountRepository,
    ) -> None:
        self._ledger = ledger
        self._accounts = accounts

    def execute(self, query: HistoryQuery) -> FinancialHistory:
        zone = _zone(query.timezone)
        span = max(1, min(query.months, MAX_HISTORY_MONTHS))

        now = query.now or PosixTime.from_epoch_seconds(
            int(dt.datetime.now(tz=dt.UTC).timestamp()),
        )
        instant = dt.datetime.fromtimestamp(now.as_epoch_seconds(), tz=zone)

        # Closed accounts count: a card paid off and closed changes nothing,
        # and one closed holding money is money that is still somewhere.
        held = list(self._accounts.list_by_user(query.user_id))
        movements = sorted(
            self._ledger.list_all(query.user_id),
            key=lambda movement: movement.occurred_at.as_epoch_seconds(),
        )

        keys = _month_keys(instant, span)
        bounds = {key: _month_bounds(zone, key) for key in keys}
        current = keys[-1]
        previous_key, previous_through, clamped = _previous_window(zone, instant)
        here = int(instant.timestamp())

        # Every instant a balance is needed at, resolved in one forward walk
        # over the ledger instead of one walk per account per instant.
        checkpoints = sorted(
            {here, int(previous_through.timestamp())}
            | {
                min(int(bounds[key][1].timestamp()), here)
                if key == current
                else int(bounds[key][1].timestamp())
                for key in keys
            },
        )
        worth = _net_worth_timeline(held, movements, checkpoints)

        months: list[MonthlyPoint] = []
        for key in keys:
            starts, ends = bounds[key]
            partial = key == current
            # The lived month stops at now, so its totals and its net worth
            # cover the same window — and match the comparison's, which a
            # client shows beside them.
            closes = (
                min(int(ends.timestamp()), here)
                if partial
                else int(
                    ends.timestamp(),
                )
            )
            months.append(
                MonthlyPoint(
                    key=key,
                    starts_at=PosixTime.from_epoch_seconds(int(starts.timestamp())),
                    ends_at=PosixTime.from_epoch_seconds(closes),
                    partial=partial,
                    totals=_totals(
                        _between(movements, int(starts.timestamp()), closes),
                    ),
                    net_worth=worth[closes],
                ),
            )

        previous_starts = _month_bounds(zone, previous_key)[0]
        current_starts = bounds[current][0]

        return FinancialHistory(
            timezone=query.timezone,
            months=months,
            comparison=PeriodComparison(
                key=current,
                starts_at=PosixTime.from_epoch_seconds(int(current_starts.timestamp())),
                through=now,
                previous_key=previous_key,
                previous_starts_at=PosixTime.from_epoch_seconds(
                    int(previous_starts.timestamp()),
                ),
                previous_through=PosixTime.from_epoch_seconds(
                    int(previous_through.timestamp()),
                ),
                clamped=clamped,
                totals=_totals(
                    _between(movements, int(current_starts.timestamp()), here),
                ),
                previous_totals=_totals(
                    _between(
                        movements,
                        int(previous_starts.timestamp()),
                        int(previous_through.timestamp()),
                    ),
                ),
                net_worth=worth[here],
                previous_net_worth=worth[int(previous_through.timestamp())],
            ),
        )


def _month_keys(instant: dt.datetime, span: int) -> list[str]:
    """The `"2026-08"` of each of the last `span` months, oldest first.

    Walked as year and month integers rather than as datetimes: arithmetic on
    a calendar is exact, while stepping by days through local midnights is
    where daylight saving gets a chance to move a boundary.
    """
    year, month = instant.year, instant.month
    keys: list[str] = []
    for _ in range(span):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return list(reversed(keys))


def _month_bounds(zone: dt.tzinfo, key: str) -> tuple[dt.datetime, dt.datetime]:
    """The instants a month starts and ends at, in that zone. End exclusive."""
    year, month = (int(part) for part in key.split("-"))
    following = (year + 1, 1) if month == 12 else (year, month + 1)
    return _local_midnight(zone, year, month), _local_midnight(zone, *following)


def _local_midnight(zone: dt.tzinfo, year: int, month: int) -> dt.datetime:
    """The first instant of a month, in a zone that may not have a midnight.

    Havana and Asunción have both started daylight saving *at* midnight on the
    1st, so that local time does not exist and attaching a zone to it names an
    instant an hour from where it should be. Round-tripping through UTC
    resolves it to a real instant, and every zone without that problem is
    untouched.
    """
    naive = dt.datetime(year, month, 1, tzinfo=zone)
    return naive.astimezone(dt.UTC).astimezone(zone)


def _previous_window(
    zone: dt.tzinfo,
    instant: dt.datetime,
) -> tuple[str, dt.datetime, bool]:
    """Where the same stretch of the previous month ends, and whether it ran out.

    Returns its key, that instant, and whether the previous month was too
    short to reach the same day — the 31st against a February, which stops at
    the end of it because there is nowhere else honest to stop.
    """
    year, month = (
        (instant.year - 1, 12)
        if instant.month == 1
        else (instant.year, instant.month - 1)
    )
    key = f"{year:04d}-{month:02d}"
    starts, ends = _month_bounds(zone, key)

    last_day = (ends - dt.timedelta(days=1)).astimezone(zone).day
    if instant.day > last_day:
        return key, ends, True

    same_day = starts.replace(
        day=instant.day,
        hour=instant.hour,
        minute=instant.minute,
        second=instant.second,
    )
    return key, same_day.astimezone(dt.UTC).astimezone(zone), False


def _between(
    movements: Sequence[Transaction],
    floor: int,
    ceiling: int,
) -> list[Transaction]:
    """Half-open: `floor` counts, `ceiling` does not."""
    return [
        movement
        for movement in movements
        if floor <= movement.occurred_at.as_epoch_seconds() < ceiling
    ]


def _net_worth_timeline(
    accounts: Sequence[Account],
    movements: Sequence[Transaction],
    checkpoints: Sequence[int],
) -> dict[int, Sequence[NetWorth]]:
    """What everything was worth at each instant, in one pass over the ledger.

    Exclusive, matching the half-open month buckets: a movement dated exactly
    at a boundary belongs to the month starting there, so it must not already
    be counted in the one closing there.

    Unassigned movements move nothing — no account claimed them, so they
    belong to no balance. They still appear in the totals, because money did
    move, which is why the two figures can disagree and should.
    """
    queued: dict[AccountId, list[Transaction]] = {
        account.id: [] for account in accounts
    }
    for movement in movements:
        waiting = queued.get(movement.account_id) if movement.account_id else None
        if waiting is not None:
            waiting.append(movement)

    cursors: dict[AccountId, int] = {account.id: 0 for account in accounts}
    running = {account.id: account.opening_balance for account in accounts}
    timeline: dict[int, Sequence[NetWorth]] = {}

    for at in sorted(checkpoints):
        for account in accounts:
            waiting = queued[account.id]
            index = cursors[account.id]
            start = index
            while (
                index < len(waiting)
                and waiting[index].occurred_at.as_epoch_seconds() < at
            ):
                index += 1
            if index > start:
                running[account.id] = account.balance_after(
                    [row.as_movement() for row in waiting[start:index]],
                    starting=running[account.id],
                )
            cursors[account.id] = index

        # A copy each time, so answering a question never edits the account
        # that answered it.
        timeline[at] = _net_worth(
            [
                dataclasses.replace(account, balance=running[account.id])
                for account in accounts
            ],
        )

    return timeline
