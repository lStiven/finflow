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

# The bucket `top` folds the tail into. It is never a group: it has no key, so
# unlike every other bucket it cannot be reopened as a list of movements, and
# a client has to render it as the remainder it is.
OTHERS_LABEL = "Others"

# ISO weekday number to name. English like every other label here — a client
# translates them, the same way it translates `Unattributed`.
WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


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


class TransferView(enum.Enum):
    """Whether a set of movements includes the two sides of a transfer.

    A transfer between the owner's own accounts is not spending and not
    income: money moved from one of their balances to another and net worth
    did not change. So the *list* shows both sides by default — they explain
    why an account fell — while every **total** leaves them out, or a card
    payment would report as an expense the size of the card's whole balance.

    `ONLY` exists for the screen that asks the opposite question: what did I
    move between my own accounts this month.
    """

    INCLUDE = "include"
    EXCLUDE = "exclude"
    ONLY = "only"


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
    # Both sides of a transfer, neither, or only those. The default suits a
    # list; a total asks for `EXCLUDE`.
    transfers: TransferView = TransferView.INCLUDE
    # One currency only. Nothing here ever sums two of them, so a report that
    # ranks buckets by amount or stacks them in one chart has to pin this
    # first — otherwise the biggest bucket is whichever currency has the
    # larger numbers, which is a fact about the unit and not about spending.
    currency: Currency | None = None

    @property
    def needs_attribution(self) -> bool:
        return self.merchant_id is not None or self.category is not None


class TransactionSort(enum.Enum):
    """What "first" means in a page of movements.

    `DATE` is what somebody opening the app wants. `AMOUNT` is what a report
    wants — the ten largest of the month — and like every other ranking here
    it needs a pinned currency, or the order would be deciding that 100 USD is
    smaller than 5 000 COP.
    """

    DATE = "date"
    AMOUNT = "amount"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionQuery:
    filter: MovementFilter
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0
    sort: TransactionSort = TransactionSort.DATE

    def __post_init__(self) -> None:
        if self.sort is TransactionSort.AMOUNT and self.filter.currency is None:
            raise ValueError(
                "Sorting by amount needs a currency: without one the order "
                "would compare figures in different units.",
            )


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

        if query.sort is TransactionSort.AMOUNT:
            # Largest first, and ties broken by date so the page is stable
            # across two calls rather than left to the sort's whim.
            found.sort(
                key=lambda movement: (
                    movement.amount.amount,
                    movement.occurred_at.as_epoch_seconds(),
                ),
                reverse=True,
            )
        else:
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


class TrendInterval(enum.Enum):
    """The width of one step along a time axis.

    Separate from `SummaryGrouping` because it is a different question: a
    grouping asks what to put in buckets, an interval asks how wide each step
    of a series is, and only three of the groupings are steps at all.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class SummaryGrouping(enum.Enum):
    """Which question a summary answers.

    The first four are stretches of time and the rest are not, and the
    difference decides two things: how the buckets are ordered, and whether
    comparing each of them against the previous period means anything. It
    does not — January against February is not the same bucket twice.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    # Not a stretch of time but a slice through it: all the Mondays together,
    # which is the one grouping where a bucket recurs instead of passing.
    WEEKDAY = "weekday"
    CATEGORY = "category"
    MERCHANT = "merchant"
    ACCOUNT = "account"

    @property
    def is_temporal(self) -> bool:
        """Whether the bucket is a stretch of time rather than a thing money
        was spent on. Those order chronologically, and are narrowed with
        `since`/`until` rather than by folding a tail into a remainder.
        """
        return self in {
            SummaryGrouping.DAY,
            SummaryGrouping.WEEK,
            SummaryGrouping.MONTH,
            SummaryGrouping.WEEKDAY,
        }

    @property
    def recurs(self) -> bool:
        """Whether the same bucket comes round again in the next window, and
        so has a previous self worth comparing against.

        A month does not: `2026-08` against `2026-07` is two different months,
        not one month twice. A Monday does — Mondays this month against
        Mondays last month is a real question — which is the whole difference
        between `WEEKDAY` and the three periods it sits beside.
        """
        return self not in {
            SummaryGrouping.DAY,
            SummaryGrouping.WEEK,
            SummaryGrouping.MONTH,
        }


class SummaryOrder(enum.Enum):
    """What "biggest" means when the buckets are ranked.

    `MOVEMENTS` is the honest default: a count means the same thing in two
    currencies and an amount does not. `AMOUNT` is what a chart actually wants
    — the eight categories worth drawing, not the eight most frequent — and it
    is only offered once the filter has pinned a currency, because otherwise
    it would be comparing pesos against dollars and calling the pesos bigger.
    """

    MOVEMENTS = "movements"
    AMOUNT = "amount"


INTERVAL_OF = {
    SummaryGrouping.DAY: TrendInterval.DAY,
    SummaryGrouping.WEEK: TrendInterval.WEEK,
    SummaryGrouping.MONTH: TrendInterval.MONTH,
}


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SummaryQuery:
    filter: MovementFilter
    group_by: SummaryGrouping = SummaryGrouping.MONTH
    # Only the time groupings need it, and they need it badly: a purchase at
    # 8pm on the 31st is the next month in UTC, which is the wrong answer for
    # everybody this deployment serves.
    timezone: str = DEFAULT_TIMEZONE
    order: SummaryOrder = SummaryOrder.MOVEMENTS
    # Keep this many buckets and add the rest together. A donut with sixty
    # slices shows nothing; eight and a remainder shows where the money went.
    top: int | None = None
    # Also run the window immediately before this one, so every bucket can say
    # what it was last time. Needs `since` and `until` both set — without a
    # length there is no previous window of the same length to compare with.
    compare: bool = False

    def __post_init__(self) -> None:
        if self.order is SummaryOrder.AMOUNT and self.filter.currency is None:
            raise ValueError(
                "Ordering by amount needs a currency: without one the ranking "
                "would compare figures in different units.",
            )

        if self.top is not None:
            if self.top < 1:
                raise ValueError(f"top must be at least 1: {self.top}")

            if self.group_by.is_temporal:
                raise ValueError(
                    f"top does not apply to {self.group_by.value}: a stretch of "
                    "time is narrowed with `since`/`until`, and folding the "
                    "oldest days into a remainder answers nothing.",
                )

        if self.compare and (self.filter.since is None or self.filter.until is None):
            raise ValueError(
                "Comparing needs `since` and `until`: the previous window is "
                "the one of the same length ending where this one starts.",
            )

    @property
    def previous_window(self) -> tuple[PosixTime, PosixTime] | None:
        """The window of equal length immediately before this one.

        Half-open like everything else here and butted right up against the
        current one, so a movement lands in exactly one of the two.
        """
        if not self.compare:
            return None

        since, until = self.filter.since, self.filter.until

        if since is None or until is None:
            return None

        span = until.as_epoch_seconds() - since.as_epoch_seconds()

        return (
            PosixTime.from_epoch_seconds(since.as_epoch_seconds() - span),
            since,
        )


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
    # Across every currency in the bucket, which is what orders the groups by
    # default: a count means the same thing in two currencies and an amount
    # does not.
    movements: int
    # The same bucket in the window before this one, when `compare` asked for
    # it and the grouping is one where that means something. None otherwise —
    # which is not the same as zero, and a client must not draw it as a fall
    # to nothing.
    previous_totals: Sequence[SpendingTotals] | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SpendingSummary:
    group_by: SummaryGrouping
    order: SummaryOrder
    # Over everything the filter matched, so a client can show a period total
    # beside its breakdown without adding the buckets up itself. `groups` plus
    # `others` always adds up to this.
    totals: Sequence[SpendingTotals]
    groups: Sequence[SummaryGroup]
    # What `top` left out, added together. None when nothing was left out. It
    # is deliberately not a group: it has no key, so it cannot be reopened as
    # a list the way every real bucket can.
    others: SummaryGroup | None = None
    # How many buckets `others` stands for. Zero when nothing was folded.
    folded: int = 0
    # The window of equal length immediately before this one, present only
    # when `compare` asked for it.
    previous_totals: Sequence[SpendingTotals] | None = None
    previous_since: PosixTime | None = None
    previous_until: PosixTime | None = None


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
        window = query.previous_window

        # One read of the partition even when comparing: the two windows
        # differ only in their bounds, which are applied in memory anyway.
        loaded = _load(self._ledger, criteria)
        current = _narrow(loaded, criteria)
        before = (
            # The bounds are the whole of the difference: `_narrow` reads them
            # off the criteria it is handed.
            _narrow(
                loaded,
                dataclasses.replace(criteria, since=window[0], until=window[1]),
            )
            if window is not None
            else []
        )

        attributions = _attribute(
            self._merchants,
            user_id=criteria.user_id,
            # Both windows at once. Two calls would read the same merchant
            # partition twice to answer one question.
            movements=[*current, *before],
            # Grouping by merchant or by category needs them; so does filtering
            # on either. A breakdown by month or by account needs neither.
            wanted=query.group_by
            in {SummaryGrouping.MERCHANT, SummaryGrouping.CATEGORY}
            or criteria.needs_attribution,
        )
        current = _matching_merchant(current, criteria, attributions)
        before = _matching_merchant(before, criteria, attributions)

        # Only a breakdown by account needs them, and it needs them to say
        # `Tarjeta Bancolombia` where the row only holds a uuid.
        names = (
            self._account_names(criteria.user_id)
            if query.group_by is SummaryGrouping.ACCOUNT
            else dict[str, str]()
        )

        def split(
            movements: Sequence[Transaction],
        ) -> tuple[dict[str | None, list[Transaction]], dict[str | None, str]]:
            buckets: dict[str | None, list[Transaction]] = {}
            labels: dict[str | None, str] = {}

            for movement in movements:
                key, label = _bucket(
                    movement,
                    group_by=query.group_by,
                    attribution=attributions.get(movement.counterparty),
                    zone=zone,
                    account_names=names,
                )
                buckets.setdefault(key, []).append(movement)
                labels.setdefault(key, label)

            return buckets, labels

        buckets, labels = split(current)
        previous, previous_labels = split(before)

        # A bucket compared against itself only means something when the
        # bucket outlives the window, so a stretch of time gets the period
        # total and nothing per bucket. A weekday is the exception among the
        # temporal groupings: Mondays do come round again.
        comparable = query.compare and query.group_by.recurs

        if comparable:
            # Something bought last month and not this one is exactly what a
            # report exists to surface, so it stays in the answer at zero
            # rather than disappearing from it.
            for key, label in previous_labels.items():
                buckets.setdefault(key, [])
                labels.setdefault(key, label)

        groups = [
            SummaryGroup(
                key=key,
                label=labels[key],
                totals=_totals(movements),
                movements=len(movements),
                previous_totals=(
                    _totals(previous.get(key, ())) if comparable else None
                ),
            )
            for key, movements in buckets.items()
        ]
        _sort_groups(groups, query.group_by, query.order, criteria.currency)
        kept, others, folded = _fold(groups, query.top)

        return SpendingSummary(
            group_by=query.group_by,
            order=query.order,
            totals=_totals(current),
            groups=kept,
            others=others,
            folded=folded,
            previous_totals=_totals(before) if query.compare else None,
            previous_since=window[0] if window is not None else None,
            previous_until=window[1] if window is not None else None,
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

    if criteria.transfers is TransferView.EXCLUDE:
        found = [movement for movement in found if not movement.is_transfer]
    elif criteria.transfers is TransferView.ONLY:
        found = [movement for movement in found if movement.is_transfer]

    if criteria.direction is not None:
        found = [
            movement for movement in found if movement.direction is criteria.direction
        ]

    if criteria.currency is not None:
        found = [
            movement
            for movement in found
            if movement.amount.currency is criteria.currency
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
        case SummaryGrouping.DAY | SummaryGrouping.WEEK | SummaryGrouping.MONTH:
            key = _period_key(
                movement.occurred_at.to_datetime().astimezone(zone),
                INTERVAL_OF[group_by],
            )

            return key, key
        case SummaryGrouping.WEEKDAY:
            # ISO: Monday is 1. Sorting the keys as strings then runs Monday to
            # Sunday, which is the order a week is read in.
            weekday = movement.occurred_at.to_datetime().astimezone(zone).isoweekday()

            return str(weekday), WEEKDAY_LABELS[weekday - 1]
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


def _sort_groups(
    groups: list[SummaryGroup],
    group_by: SummaryGrouping,
    order: SummaryOrder,
    currency: Currency | None,
) -> None:
    """Periods run newest first, weekdays run Monday to Sunday, and everything
    else runs biggest first — by movement count unless a currency was pinned
    and `AMOUNT` asked for the other ranking.

    Count is the default because it means the same thing in two currencies and
    an amount does not. Pinning a currency is what makes the amount ranking
    answerable, which is why the query refuses one without the other.
    """
    if group_by is SummaryGrouping.WEEKDAY:
        # Numerically: "10" would come before "9" as text, and while a week
        # has no tenth day the reader of this should not have to check.
        groups.sort(key=lambda group: int(group.key or "0"))

        return

    if group_by.is_temporal:
        groups.sort(key=lambda group: group.key or "", reverse=True)

        return

    if order is SummaryOrder.AMOUNT and currency is not None:
        groups.sort(
            key=lambda group: (-_moved(group.totals, currency), group.label.casefold())
        )

        return

    groups.sort(key=lambda group: (-group.movements, group.label.casefold()))


def _moved(totals: Sequence[SpendingTotals], currency: Currency) -> Decimal:
    """How much money the bucket touched, in the one currency that was pinned.

    Both directions added, not netted: a merchant that took 100 and refunded
    100 is not a merchant nothing happened at, and netting would rank it last.
    """
    for figure in totals:
        if figure.currency is currency:
            return figure.incoming + figure.outgoing

    return Decimal(0)


def _fold(
    groups: list[SummaryGroup],
    top: int | None,
) -> tuple[list[SummaryGroup], SummaryGroup | None, int]:
    """Keep the first `top` buckets and add the rest together.

    The remainder carries no key on purpose: every real bucket can be reopened
    as the list of movements behind it by repeating the query with its key,
    and this one cannot, so it must not look like it can.
    """
    if top is None or len(groups) <= top:
        return groups, None, 0

    kept, tail = groups[:top], groups[top:]
    compared = any(group.previous_totals is not None for group in tail)

    return (
        kept,
        SummaryGroup(
            key=None,
            label=OTHERS_LABEL,
            totals=_merge(group.totals for group in tail),
            movements=sum(group.movements for group in tail),
            previous_totals=(
                _merge(group.previous_totals or () for group in tail)
                if compared
                else None
            ),
        ),
        len(tail),
    )


def _merge(figures: Iterable[Sequence[SpendingTotals]]) -> Sequence[SpendingTotals]:
    """Several buckets' totals added into one, still one figure per currency."""
    incoming: dict[Currency, Decimal] = {}
    outgoing: dict[Currency, Decimal] = {}
    counted: dict[Currency, int] = {}

    for totals in figures:
        for figure in totals:
            incoming[figure.currency] = (
                incoming.get(figure.currency, Decimal(0)) + figure.incoming
            )
            outgoing[figure.currency] = (
                outgoing.get(figure.currency, Decimal(0)) + figure.outgoing
            )
            counted[figure.currency] = (
                counted.get(figure.currency, 0) + figure.movements
            )

    return [
        SpendingTotals(
            currency=currency,
            incoming=incoming[currency],
            outgoing=outgoing[currency],
            movements=counted[currency],
        )
        for currency in sorted(counted, key=lambda currency: currency.value)
    ]


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
        # Two lists on purpose. Balances replay *every* movement, transfers
        # included: paying a card really does move both balances. Totals
        # replay only what was spent or earned, or a card payment would report
        # as a month's largest expense and again as income on the card.
        spending = [movement for movement in movements if not movement.is_transfer]

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
                        _between(spending, int(starts.timestamp()), closes),
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
                    _between(spending, int(current_starts.timestamp()), here),
                ),
                previous_totals=_totals(
                    _between(
                        spending,
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
    """The first instant of a month."""
    return _local_start(zone, dt.date(year, month, 1))


def _local_start(zone: dt.tzinfo, day: dt.date) -> dt.datetime:
    """The first instant of a day, in a zone that may not have a midnight.

    Havana and Asunción have both started daylight saving *at* midnight, so
    that local time does not exist and attaching a zone to it names an instant
    an hour from where it should be. Round-tripping through UTC resolves it to
    a real instant, and every zone without that problem is untouched.
    """
    naive = dt.datetime(day.year, day.month, day.day, tzinfo=zone)
    return naive.astimezone(dt.UTC).astimezone(zone)


def _period_key(instant: dt.datetime, interval: TrendInterval) -> str:
    """What to call the period this local instant falls in.

    A week is named by its ISO year and week, which is not always the calendar
    year: 2027-01-01 is a Friday and belongs to `2026-W53`. That is the
    correct answer — the week did start in December — and it is why the year
    here comes from `%G` and never from `%Y`.
    """
    match interval:
        case TrendInterval.DAY:
            return instant.strftime("%Y-%m-%d")
        case TrendInterval.WEEK:
            return instant.strftime("%G-W%V")
        case TrendInterval.MONTH:
            return instant.strftime("%Y-%m")


def _period_bounds(
    zone: dt.tzinfo,
    key: str,
    interval: TrendInterval,
) -> tuple[dt.datetime, dt.datetime]:
    """The instants a period starts and ends at, in that zone. End exclusive."""
    match interval:
        case TrendInterval.DAY:
            day = dt.date.fromisoformat(key)

            return _local_start(zone, day), _local_start(
                zone,
                day + dt.timedelta(days=1),
            )
        case TrendInterval.WEEK:
            year, week = key.split("-W")
            monday = dt.date.fromisocalendar(int(year), int(week), 1)

            return _local_start(zone, monday), _local_start(
                zone,
                monday + dt.timedelta(days=7),
            )
        case TrendInterval.MONTH:
            return _month_bounds(zone, key)


def _period_walk(
    zone: dt.tzinfo,
    since: dt.datetime,
    until: dt.datetime,
    interval: TrendInterval,
    limit: int,
) -> list[str]:
    """Every period touching `[since, until)`, oldest first and none missing.

    Dense on purpose: a month nothing happened in is a zero on the chart, not
    a gap the client has to notice and fill. Walked through each period's own
    end instant rather than by adding days, so a daylight-saving shift inside
    the range cannot slide a boundary.
    """
    keys: list[str] = []
    cursor = _period_bounds(zone, _period_key(since, interval), interval)[0]

    while cursor < until:
        if len(keys) >= limit:
            raise ValueError(
                f"That range is more than {limit} {interval.value}s. Ask for a "
                "shorter one, or a wider interval.",
            )

        key = _period_key(cursor, interval)
        keys.append(key)
        cursor = _period_bounds(zone, key, interval)[1]

    return keys


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


# ------------------------------------------------------------------ trends


MAX_TREND_BUCKETS = 372
DEFAULT_TREND_PERIODS = 12
MAX_TREND_SERIES = 20

# The single series `NONE` produces. There is nothing to tell apart, so it
# carries the label rather than a key that would look like a filter value.
TOTAL_LABEL = "Total"


class TrendDimension(enum.Enum):
    """What to split each step of the series by.

    `NONE` gives one series — the cashflow line — and it is not redundant with
    the others: every point already carries `incoming` and `outgoing`, so one
    undivided series is the income-against-expense chart.
    """

    NONE = "none"
    CATEGORY = "category"
    MERCHANT = "merchant"
    ACCOUNT = "account"


_GROUPING_OF = {
    TrendDimension.CATEGORY: SummaryGrouping.CATEGORY,
    TrendDimension.MERCHANT: SummaryGrouping.MERCHANT,
    TrendDimension.ACCOUNT: SummaryGrouping.ACCOUNT,
}


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TrendQuery:
    filter: MovementFilter
    interval: TrendInterval = TrendInterval.MONTH
    dimension: TrendDimension = TrendDimension.CATEGORY
    # How far back to go when the filter names no `since`. Ignored when it
    # does: an explicit range always wins over a default one.
    periods: int = DEFAULT_TREND_PERIODS
    # Keep this many series and add the rest into one. A stacked chart with
    # forty bands is a solid block.
    series: int | None = None
    order: SummaryOrder = SummaryOrder.MOVEMENTS
    timezone: str = DEFAULT_TIMEZONE
    # Injectable so a test can stand at a chosen instant. Production leaves it
    # None and the clock answers.
    now: PosixTime | None = None

    def __post_init__(self) -> None:
        if self.order is SummaryOrder.AMOUNT and self.filter.currency is None:
            raise ValueError(
                "Ordering by amount needs a currency: without one the ranking "
                "would compare figures in different units.",
            )

        if self.periods < 1:
            raise ValueError(f"periods must be at least 1: {self.periods}")

        if self.series is not None and not 1 <= self.series <= MAX_TREND_SERIES:
            raise ValueError(
                f"series must be between 1 and {MAX_TREND_SERIES}: {self.series}",
            )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TrendBucket:
    """One step of the axis, whether or not anything happened in it."""

    key: str
    starts_at: PosixTime
    # Exclusive, and for the period still being lived it is *now* rather than
    # the period's end, so nothing is charted as a finished step it is not.
    ends_at: PosixTime
    partial: bool


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TrendPoint:
    bucket: str
    # Empty when nothing moved in this bucket, which is the ordinary case for
    # most of a chart and not a hole in it.
    totals: Sequence[SpendingTotals]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TrendSeries:
    """One band of the chart, across every bucket.

    `key` is None for the series a dimension could not place — a counterparty
    no merchant owns yet, a movement no account claimed — and for the
    remainder `series` folded, which is why `label` is what a client renders
    and `key` only ever what it filters by.
    """

    key: str | None
    label: str
    # One per bucket, in the same order, none missing. That is the whole point
    # of the endpoint: a client zips this against `buckets` by index.
    points: Sequence[TrendPoint]
    totals: Sequence[SpendingTotals]
    movements: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SpendingTrend:
    interval: TrendInterval
    dimension: TrendDimension
    timezone: str
    starts_at: PosixTime
    ends_at: PosixTime
    # Oldest first, so a client draws it without reversing anything.
    buckets: Sequence[TrendBucket]
    series: Sequence[TrendSeries]
    # What `series` left out, added together. None when nothing was left out.
    others: TrendSeries | None = None
    folded: int = 0
    totals: Sequence[SpendingTotals] = ()


class ReadSpendingTrendUseCase:
    """How spending moved over time, split into the bands a chart stacks.

    `/summary?group_by=month` answers one dimension at a time: what a period
    adds up to, or how it splits by category, never both. A report wants the
    two together — restaurants against transport against groceries, month by
    month — and asking for it a month at a time is twelve round trips whose
    buckets can each be ranked differently, so they cannot even be stacked
    without the client reconciling them.

    So the ranking happens once, over the whole range, and every bucket is
    filled against that one set of series. Buckets are dense and points are
    aligned with them by index: a month nothing happened in is a zero, not a
    gap.
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

    def execute(self, query: TrendQuery) -> SpendingTrend:
        zone = _zone(query.timezone)
        criteria = query.filter
        now = query.now or PosixTime.from_epoch_seconds(
            int(dt.datetime.now(tz=dt.UTC).timestamp()),
        )
        here = dt.datetime.fromtimestamp(now.as_epoch_seconds(), tz=zone)

        since, until = self._range(query, zone=zone, here=here)
        keys = _period_walk(zone, since, until, query.interval, MAX_TREND_BUCKETS)
        bounded = dataclasses.replace(
            criteria,
            since=PosixTime.from_epoch_seconds(int(since.timestamp())),
            until=PosixTime.from_epoch_seconds(int(until.timestamp())),
        )

        found = _narrow(_load(self._ledger, bounded), bounded)
        attributions = _attribute(
            self._merchants,
            user_id=criteria.user_id,
            movements=found,
            wanted=query.dimension in {TrendDimension.CATEGORY, TrendDimension.MERCHANT}
            or criteria.needs_attribution,
        )
        found = _matching_merchant(found, bounded, attributions)
        names = (
            self._account_names(criteria.user_id)
            if query.dimension is TrendDimension.ACCOUNT
            else dict[str, str]()
        )

        # Which band, and which step of it, each movement lands in.
        placed: dict[str | None, dict[str, list[Transaction]]] = {}
        labels: dict[str | None, str] = {}

        for movement in found:
            band, label = _band(
                movement,
                dimension=query.dimension,
                attribution=attributions.get(movement.counterparty),
                account_names=names,
            )
            step = _period_key(
                movement.occurred_at.to_datetime().astimezone(zone),
                query.interval,
            )
            placed.setdefault(band, {}).setdefault(step, []).append(movement)
            labels.setdefault(band, label)

        # Ranked once, over the whole range, so every bucket stacks the same
        # bands in the same order.
        ranked = sorted(
            placed,
            key=lambda band: _rank(placed[band], labels[band], query, criteria),
        )
        kept = ranked if query.series is None else ranked[: query.series]
        tail = [] if query.series is None else ranked[query.series :]

        series = [_series(band, labels[band], placed[band], keys) for band in kept]
        others = (
            _series(
                None,
                OTHERS_LABEL,
                _pool(placed[band] for band in tail),
                keys,
            )
            if tail
            else None
        )

        return SpendingTrend(
            interval=query.interval,
            dimension=query.dimension,
            timezone=query.timezone,
            starts_at=PosixTime.from_epoch_seconds(int(since.timestamp())),
            ends_at=PosixTime.from_epoch_seconds(int(until.timestamp())),
            buckets=[_bucket_of(zone, key, query.interval, until) for key in keys],
            series=series,
            others=others,
            folded=len(tail),
            totals=_totals(found),
        )

    def _range(
        self,
        query: TrendQuery,
        *,
        zone: dt.tzinfo,
        here: dt.datetime,
    ) -> tuple[dt.datetime, dt.datetime]:
        """The window to chart, snapped outwards to whole periods.

        Snapped because a chart of half a January beside a whole February
        reports a fall that did not happen. An explicit `from`/`to` still wins
        over the default span — it is just widened to the periods it touches.
        """
        criteria = query.filter
        until = (
            here
            if criteria.until is None
            else dt.datetime.fromtimestamp(criteria.until.as_epoch_seconds(), tz=zone)
        )

        if criteria.since is not None:
            since = dt.datetime.fromtimestamp(
                criteria.since.as_epoch_seconds(),
                tz=zone,
            )
        else:
            # Walk back `periods - 1` whole steps from the last one *inside*
            # the window, so the answer holds exactly `periods` of them.
            # `until` is exclusive here as everywhere else, so a `to` landing
            # exactly on a boundary opens a period the window does not cover.
            cursor = _period_bounds(
                zone,
                _period_key(until - dt.timedelta(seconds=1), query.interval),
                query.interval,
            )[0]

            for _ in range(query.periods - 1):
                cursor = _period_bounds(
                    zone,
                    _period_key(cursor - dt.timedelta(seconds=1), query.interval),
                    query.interval,
                )[0]

            since = cursor

        if until <= since:
            raise ValueError("`to` must come after `from`.")

        return (
            _period_bounds(zone, _period_key(since, query.interval), query.interval)[0],
            until,
        )

    def _account_names(self, user_id: UserId) -> Mapping[str, str]:
        return {
            str(account.id.value): account.name
            for account in self._accounts.list_by_user(user_id)
        }


def _band(
    movement: Transaction,
    *,
    dimension: TrendDimension,
    attribution: MerchantAttribution | None,
    account_names: Mapping[str, str],
) -> tuple[str | None, str]:
    """Which series this movement belongs to, and what to call it."""
    if dimension is TrendDimension.NONE:
        return None, TOTAL_LABEL

    return _bucket(
        movement,
        group_by=_GROUPING_OF[dimension],
        attribution=attribution,
        # Only the temporal groupings read it, and none of them can get here.
        zone=dt.UTC,
        account_names=account_names,
    )


def _rank(
    steps: Mapping[str, Sequence[Transaction]],
    label: str,
    query: TrendQuery,
    criteria: MovementFilter,
) -> tuple[int | Decimal, str]:
    """Biggest first, by whichever measure the query can honestly compare."""
    movements = [movement for bucket in steps.values() for movement in bucket]

    if query.order is SummaryOrder.AMOUNT and criteria.currency is not None:
        return -_moved(_totals(movements), criteria.currency), label.casefold()

    return -len(movements), label.casefold()


def _series(
    key: str | None,
    label: str,
    steps: Mapping[str, Sequence[Transaction]],
    keys: Sequence[str],
) -> TrendSeries:
    """One band, filled against every bucket including the empty ones."""
    movements = [movement for bucket in steps.values() for movement in bucket]

    return TrendSeries(
        key=key,
        label=label,
        points=[
            TrendPoint(bucket=key, totals=_totals(steps.get(key, ()))) for key in keys
        ],
        totals=_totals(movements),
        movements=len(movements),
    )


def _pool(
    tail: Iterable[Mapping[str, Sequence[Transaction]]],
) -> dict[str, list[Transaction]]:
    """Every folded band's steps, added into one band."""
    pooled: dict[str, list[Transaction]] = {}

    for steps in tail:
        for key, movements in steps.items():
            pooled.setdefault(key, []).extend(movements)

    return pooled


def _bucket_of(
    zone: dt.tzinfo,
    key: str,
    interval: TrendInterval,
    until: dt.datetime,
) -> TrendBucket:
    """One step of the axis, closed off at the end of what it actually covers.

    A period the window stops inside of is `partial` and ends where the window
    does — whether it was cut short by *now* or by an explicit `to`. Both are
    the same mistake if left unsaid: half of a period charted beside whole
    ones reports a fall that did not happen.
    """
    starts, ends = _period_bounds(zone, key, interval)
    partial = ends > until

    return TrendBucket(
        key=key,
        starts_at=PosixTime.from_epoch_seconds(int(starts.timestamp())),
        ends_at=PosixTime.from_epoch_seconds(int(min(ends, until).timestamp())),
        partial=partial,
    )
