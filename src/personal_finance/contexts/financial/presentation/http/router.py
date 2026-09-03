"""The financial surface a frontend draws itself from.

Everything is scoped to the authenticated caller. Accounts and movements are
per-user, so no endpoint takes a user id: the token decides whose money is
read and whose can be edited, and anything belonging to somebody else is
reported as missing rather than as forbidden.

Two things shape this surface. **Accounts are declared, never discovered** —
Finflow works with none at all, reporting what came in and what went out, and
an account is what somebody adds when they want a running state for one card
or one savings account. And **money that never emails can be entered by hand**,
because an automatic payment the bank stays quiet about is still money that
moved.
"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import datetime as dt
from decimal import Decimal
import functools
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from personal_finance.contexts.financial.application.commands import (
    AccrueFinancingCommand,
    ChargeDraft,
    ClearFinancingCommand,
    CloseAccountCommand,
    DeleteTransactionCommand,
    EditTransactionCommand,
    EnterTransactionCommand,
    EnterTransferLegCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RenameAccountCommand,
    RestateBalanceCommand,
    RevalueAccountCommand,
    SetCreditLimitCommand,
    SetInvestmentTermsCommand,
    SetLoanTermsCommand,
)
from personal_finance.contexts.financial.application.financing import (
    DEFAULT_SCHEDULE_PERIODS,
    MAX_SCHEDULE_PERIODS,
    AccrualResult,
    AccrueFinancingUseCase,
    FinancingView,
    InvestmentPerformance,
    ManageFinancingUseCase,
    NotFinancedError,
    ReadFinancingUseCase,
    RevalueAccountUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import MerchantDirectory
from personal_finance.contexts.financial.application.queries import (
    DEFAULT_HISTORY_MONTHS,
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEZONE,
    DEFAULT_TREND_PERIODS,
    MAX_HISTORY_MONTHS,
    MAX_PAGE_SIZE,
    MAX_TREND_BUCKETS,
    MAX_TREND_SERIES,
    AccountScope,
    AttributedTransaction,
    FinancialHistory,
    GetAccountUseCase,
    GetTransactionUseCase,
    HistoryQuery,
    ListAccountsUseCase,
    ListTransactionsUseCase,
    MonthlyPoint,
    MovementFilter,
    NetWorth,
    PeriodComparison,
    ReadFinancialHistoryUseCase,
    ReadSpendingTrendUseCase,
    SpendingSummary,
    SpendingTotals,
    SpendingTrend,
    SummarizeSpendingUseCase,
    SummaryGroup,
    SummaryGrouping,
    SummaryOrder,
    SummaryQuery,
    TransactionQuery,
    TransactionSort,
    TransferView,
    TrendBucket,
    TrendDimension,
    TrendInterval,
    TrendQuery,
    TrendSeries,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    FinancingTermsError,
    TransactionAlreadyAssignedError,
    TransferLegError,
)
from personal_finance.contexts.financial.domain.financing import (
    MAX_CHARGE_NAME_LENGTH,
    MAX_RATE,
    MAX_TERM_MONTHS,
    AmortizationStyle,
    ChargeAmount,
    ChargeBasis,
    InterestRate,
    InvestmentProjection,
    InvestmentTerms,
    LoanSchedule,
    LoanTerms,
    ProjectedReturn,
    RateBasis,
    RecurringCharge,
    ScheduledPayment,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransactionOrigin,
    TransactionStatus,
    TransferRole,
)
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    build_merchant_directory,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_financial_settings,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)
from personal_finance.shared.presentation.catalog import (
    CatalogOption,
    label,
    options,
)


router = APIRouter(prefix="/financial", tags=["financial"])

MAX_NAME_LENGTH = 120
MAX_TEXT_LENGTH = 512

# Far past any balance this is for, and far short of what DynamoDB's `N` can
# hold. Unbounded, a magnitude the table cannot store reaches boto3 and comes
# back as a 500 with a stack trace instead of the refusal it is.
MAX_MONEY = Decimal("1e15")

# Epoch seconds this side of the year 10000. Unbounded, the conversion raises
# `OSError` from deep inside `datetime` rather than the `ValueError` the error
# translation below understands, and the request answers 500 — which, since
# Starlette's error middleware sits outside the CORS one, a browser then
# reports as a CORS failure rather than as a bad number.
MIN_EPOCH_SECONDS = 0
MAX_EPOCH_SECONDS = 253_402_300_799


# --------------------------------------------------------------- responses


class AccountResponse(BaseModel):
    id: str
    name: str
    kind: str
    # `asset` | `liability` — what decides which way this balance moves when
    # a payment arrives.
    category: str
    # Watched rather than counted: true for a loan and a mortgage, whose
    # balance is deliberately outside net worth and whose movements are
    # outside every total. Their owner already knows what they owe, and the
    # cuota that services them is money leaving an account this app is
    # watching anyway — counting both would report one payment twice.
    informational: bool
    currency: str
    # Signed: an asset legitimately goes below zero when its opening balance
    # was never stated, and a liability's positive amount is what is owed.
    balance: str
    opening_balance: str
    # Liabilities only, and only once the owner stated one. `available` is
    # `credit_limit` minus what is owed, and it is signed: a card over its
    # limit reports a negative, which is the case worth seeing.
    credit_limit: str | None
    available: str | None
    movements_applied: int
    opened_at: int
    closed_at: int | None
    bank: str | None
    # The bank/instrument keys whose alerts land here.
    instruments: list[str]
    # What this account charges or earns on its own. Both null on everything
    # that computes nothing, which is every savings account and every card.
    loan: LoanTermsResponse | None = None
    investment: InvestmentTermsResponse | None = None
    # The last statement date whose charges are already in the ledger. Null
    # while nothing has ever accrued.
    accrued_through: str | None = None


class RateResponse(BaseModel):
    """A rate as its owner typed it, plus the one figure that compares.

    `value` is a fraction — `0.1956`, not `19.56` — in whatever basis the bank
    quoted. `effective_annual` and `monthly` are the same rate converted, so a
    screen can show "19.56 % E.A. (1.4999 % mensual)" without re-deriving a
    conversion that has to agree with the one the charges were computed from.
    """

    value: str
    basis: RateBasis
    effective_annual: str
    monthly: str


class ChargeResponse(BaseModel):
    """One thing charged every period besides the interest."""

    name: str
    basis: ChargeBasis
    amount: str | None
    rate: str | None
    base: str | None
    # False when the bank collects it somewhere else, in which case it is part
    # of what has to be paid and never part of this balance.
    charged_to_balance: bool


class LoanTermsResponse(BaseModel):
    rate: RateResponse
    disbursed_on: str
    term_months: int
    statement_day: int
    payment_day: int
    style: AmortizationStyle
    principal: str | None
    installment: str | None
    installment_covers_charges: bool
    charges: list[ChargeResponse]
    # The last cut of the term as agreed at disbursement. Derived, so a client
    # never recomputes a calendar.
    matures_on: str


class InvestmentTermsResponse(BaseModel):
    opened_on: str
    statement_day: int
    # Null for variable income — shares, a fund whose unit price moves. Its
    # value is stated through `POST /accounts/{id}/value`, not computed.
    rate: RateResponse | None
    matures_on: str | None
    charges: list[ChargeResponse]


class ChargeAmountResponse(BaseModel):
    """One charge, priced for one period."""

    name: str
    amount: str
    charged_to_balance: bool


class ScheduledPaymentResponse(BaseModel):
    """One row of la tabla de amortización.

    `principal` is the only figure that moves the debt, and the reason the
    table is worth rendering: it is what the payment was worth once the month
    took what it was owed.
    """

    starts_on: str
    ends_on: str
    due_on: str
    opening_balance: str
    interest: str
    charges: list[ChargeAmountResponse]
    principal: str
    # Capital, interest and every charge: what actually has to be paid.
    due: str
    closing_balance: str


class LoanScheduleResponse(BaseModel):
    payments: list[ScheduledPaymentResponse]
    total_interest: str
    total_charges: str
    total_due: str
    # When the balance reaches zero, or null when it does not inside the rows
    # asked for.
    settles_on: str | None
    # The instalment does not cover the interest, so the debt grows every
    # month however long it is paid. The one figure worth interrupting
    # somebody for.
    negatively_amortizing: bool


class ProjectedReturnResponse(BaseModel):
    starts_on: str
    ends_on: str
    opening_balance: str
    earned: str
    charges: list[ChargeAmountResponse]
    closing_balance: str


class InvestmentProjectionResponse(BaseModel):
    periods: list[ProjectedReturnResponse]
    total_earned: str
    total_charges: str
    value_at_end: str
    matures_on: str | None


class InvestmentPerformanceResponse(BaseModel):
    """What went in, what came out, and what the position actually made.

    Read off the ledger rather than from the balance, which is the only way
    the gain is separable at all: a contribution and a return both raise the
    same number, and only the row says which it was. `earned` is signed and
    net of every charge the position carried.
    """

    contributed: str
    withdrawn: str
    earned: str


class FinancingResponse(BaseModel):
    """What an account that computes will do, worked out from today's balance.

    Never stored. A table assumes every instalment is paid on the day it is
    due, so the moment a real payment lands the balance it starts from moves
    and the whole table with it.
    """

    account: AccountResponse
    as_of: str
    # Interest for the days since the last cut, which nobody owes yet. It
    # carries no charges: insurance is charged whole on the cut day, so a
    # part-month owes none of it.
    pending_interest: str
    # Closed statement periods whose charges are not in the ledger yet.
    # Anything above zero means `payoff` is short by those months, and the
    # answer is `POST .../accrue`. Zero whenever the accrual is up to date.
    periods_due: int
    # What settling today would take: the balance plus that interest. Null on
    # an account holding nothing.
    payoff: str | None
    next_statement_on: str
    next_due_on: str | None
    schedule: LoanScheduleResponse | None
    projection: InvestmentProjectionResponse | None
    performance: InvestmentPerformanceResponse | None


class AccrualResponse(BaseModel):
    """What one account's accrual wrote.

    `skipped` counts the charges a previous run had already written. Running
    this twice is meant to be safe, and seeing that it was is more use than
    inferring it from silence.
    """

    account: AccountResponse
    posted: list[TransactionResponse]
    skipped: int
    accrued_through: str | None
    # Why nothing was written, when nothing was.
    reason: str | None


class NetWorthResponse(BaseModel):
    currency: str
    assets: str
    liabilities: str
    total: str


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]
    # One entry per currency held; never summed across them, because that
    # would need an exchange rate nobody recorded.
    net_worth: list[NetWorthResponse]


class StatedResponse(BaseModel):
    """What the bank said, before anybody corrected it."""

    amount: str
    currency: str
    occurred_at: int
    counterparty: str


class MerchantResponse(BaseModel):
    """The canonical merchant behind this movement's counterparty text.

    Joined when the answer is read, never stored on the movement: the grouping
    belongs to Merchant and a user can change it, so a rename or a merge shows
    up on every past movement at once with nothing to re-process.
    """

    id: str
    display_name: str
    # Merchant's vocabulary — the same values `GET /merchants/categories`
    # lists, so a client has one place to read labels from.
    category: str
    # True while Merchant is still waiting for somebody to confirm the
    # grouping. A screen can show the attribution and say it is a guess.
    needs_review: bool


class TransferResponse(BaseModel):
    """The half of a movement that says it was not spending.

    Present on both sides of a transfer between two of the owner's own
    accounts, and null on everything else. A client shows it instead of the
    counterparty text — `role` says which way the money went and
    `counterpart_*` names the other side — and, more importantly, knows not to
    read the amount as an expense.

    The other side is not always here. A card paid from another bank, from a
    wallet or in cash has one knowable side, entered by hand through
    `POST /financial/transactions/transfer`: `external` is true there and
    every `counterpart_*` field is null. Read `external` rather than
    null-checking the three — it is the question a client is actually asking,
    and the movement's own `counterparty` already carries what the owner
    called the other side ("Nequi", "efectivo").
    """

    id: str
    # `source` (money left this account) | `destination` (it arrived here; on
    # a credit card that is its debt going down).
    role: str
    # True when the other side is outside this app, which is exactly when the
    # three fields below are null.
    external: bool
    counterpart_movement_id: str | None
    counterpart_instrument_kind: str | None
    counterpart_last_four: str | None


class TransactionResponse(BaseModel):
    id: str
    # `outgoing` | `incoming`.
    direction: str
    amount: str
    currency: str
    occurred_at: int
    counterparty: str
    bank: str
    # `bank_alert` | `manual`.
    origin: str
    # `assigned` | `unassigned`.
    status: str
    account_id: str | None
    note: str | None
    # Present only once somebody corrected this movement.
    stated: StatedResponse | None
    # Null while no merchant owns this spelling: the sighting may still be on
    # merchant's queue, and a movement entered by hand under a name nothing
    # else has seen never gets one. Not an error either way.
    merchant: MerchantResponse | None
    # Set on both rows of a transfer between the owner's own accounts. Null
    # on ordinary spending, which is nearly everything.
    transfer: TransferResponse | None = None


class DeletedTransactionResponse(BaseModel):
    """What an erasure took out, and what the balances say now.

    `erased` is a list because a transfer is two rows stating one movement of
    money and they go together: a client that assumed one would leave the
    other side on screen pointing at a movement that no longer exists.

    `accounts` carries the accounts whose balances the erasure gave money back
    to, already recomputed, so a screen showing a balance does not need a
    second call to stop showing money that no longer moved. Empty when the
    movement was sitting on no account, which is exactly when no balance
    changed.
    """

    erased: list[str]
    accounts: list[AccountResponse]


class TransactionListResponse(BaseModel):
    transactions: list[TransactionResponse]
    total: int
    limit: int
    offset: int


class SpendingTotalsResponse(BaseModel):
    currency: str
    incoming: str
    outgoing: str
    # `incoming - outgoing`. Signed, and negative for a month that spent more
    # than it took in.
    net: str
    movements: int


class SummaryGroupResponse(BaseModel):
    # `2026-08` for a month, `2026-W32` for a week, `1`..`7` for a weekday,
    # otherwise the merchant, category or account id. Null is the bucket the
    # grouping could not place — a movement no account claimed, or a
    # counterparty no merchant owns yet. It belongs in the answer: without it
    # the groups stop adding up to `totals`.
    key: str | None
    label: str
    totals: list[SpendingTotalsResponse]
    movements: int
    # The same bucket in the window before this one, when `compare=true` asked
    # for it and the grouping is one where that means something. Null
    # otherwise — which is not zero, and must not be drawn as a fall to
    # nothing. An empty list *is* zero: the bucket existed and nothing moved.
    previous_totals: list[SpendingTotalsResponse] | None = None


class SpendingSummaryResponse(BaseModel):
    group_by: str
    timezone: str
    order: str
    # Over everything the filter matched, so a period total needs no second
    # call and no adding up of the buckets. `groups` plus `others` always adds
    # up to this.
    totals: list[SpendingTotalsResponse]
    # Stretches of time run newest first and weekdays run Monday to Sunday;
    # every other grouping runs biggest first, by movement count unless
    # `order=amount` and a `currency` were both asked for.
    groups: list[SummaryGroupResponse]
    # What `top` left out, added together, or null when nothing was left out.
    # It carries no key on purpose: every real bucket can be reopened as the
    # list behind it by repeating the query with its key, and this one cannot.
    others: SummaryGroupResponse | None = None
    # How many buckets `others` stands for.
    folded: int = 0
    # The window of equal length immediately before this one, present only
    # when `compare=true`.
    previous_totals: list[SpendingTotalsResponse] | None = None
    previous_starts_at: int | None = None
    previous_ends_at: int | None = None


class TrendBucketResponse(BaseModel):
    """One step of the axis, whether or not anything happened in it."""

    key: str
    starts_at: int
    # Exclusive, and for the period still being lived it is *now* rather than
    # the period's end.
    ends_at: int
    partial: bool


class TrendPointResponse(BaseModel):
    bucket: str
    # Empty when nothing moved in this bucket — the ordinary case for most of
    # a chart, and not a hole in it.
    totals: list[SpendingTotalsResponse]


class TrendSeriesResponse(BaseModel):
    """One band of the chart, across every bucket.

    `key` is null for the band the dimension could not place, and for the
    remainder `series` folded. So `label` is what to render and `key` only
    ever what to filter by.
    """

    key: str | None
    label: str
    # One per bucket, in the same order, none missing: zip it against
    # `buckets` by index.
    points: list[TrendPointResponse]
    totals: list[SpendingTotalsResponse]
    movements: int


class SpendingTrendResponse(BaseModel):
    interval: str
    dimension: str
    timezone: str
    starts_at: int
    ends_at: int
    # Oldest first, and dense: a period nothing happened in is a zero rather
    # than a gap the client has to notice and fill.
    buckets: list[TrendBucketResponse]
    series: list[TrendSeriesResponse]
    others: TrendSeriesResponse | None = None
    folded: int = 0
    totals: list[SpendingTotalsResponse] = []


class MonthlyPointResponse(BaseModel):
    key: str
    starts_at: int
    # Exclusive, so two consecutive months never claim the same movement.
    ends_at: int
    # The month still being lived. Its totals cover part of a month and must
    # not be charted as a finished one.
    partial: bool
    totals: list[SpendingTotalsResponse]
    # What everything was worth when the month closed — or right now, for the
    # partial one. Replayed from the ledger, never stored.
    net_worth: list[NetWorthResponse]


class PeriodComparisonResponse(BaseModel):
    """This month so far against the same stretch of the month before it.

    Aligned by day of the month: on the 15th, the 1st to the 15th against the
    1st to the 15th. Comparing a young month against a finished one reports
    spending down by most of it, every month, and is right about nothing.
    """

    key: str
    starts_at: int
    through: int
    previous_key: str
    previous_starts_at: int
    previous_through: int
    # The previous month ran out of days first — the 31st against a February.
    # The window is the whole of it, and a client that says "vs julio" should
    # say something else here.
    clamped: bool
    totals: list[SpendingTotalsResponse]
    previous_totals: list[SpendingTotalsResponse]
    net_worth: list[NetWorthResponse]
    previous_net_worth: list[NetWorthResponse]


class FinancialHistoryResponse(BaseModel):
    timezone: str
    # Oldest first, so a client charts it without reversing anything.
    months: list[MonthlyPointResponse]
    comparison: PeriodComparisonResponse


# ---------------------------------------------------------------- payloads


class AccountKindOption(CatalogOption):
    """An account kind, which side it lands on, and whether it is counted.

    `category` is derived from the kind and never chosen, so a client can
    group the dropdown — and say "money you owe" against a balance — without
    duplicating the rule that decides it.

    `informational` is the other half of that: a loan and a mortgage keep a
    balance and a schedule of their own and take part in no total, so a form
    can say so while somebody is choosing rather than leaving them to notice
    that their net worth did not move.
    """

    category: str
    informational: bool


class FinancialCatalogResponse(BaseModel):
    """Every vocabulary this context's endpoints accept.

    `instrument_kinds` is the one that is not an account kind and is the one
    people reach for anyway: a savings account is `savings`, but the alerts it
    sends name the instrument `account`. Offer it as its own list, never a
    text field.
    """

    account_kinds: list[AccountKindOption]
    instrument_kinds: list[CatalogOption]
    account_categories: list[CatalogOption]
    currencies: list[CatalogOption]
    movement_directions: list[CatalogOption]
    transaction_origins: list[CatalogOption]
    transaction_statuses: list[CatalogOption]
    account_scopes: list[CatalogOption]
    summary_groupings: list[CatalogOption]
    # How the buckets of a breakdown are ranked. `amount` is only accepted
    # together with a `currency`, since nothing here converts between two.
    summary_orders: list[CatalogOption]
    # How a page of movements is ordered. `amount`, likewise, needs a
    # `currency`.
    transaction_sorts: list[CatalogOption]
    # The axis and the bands of `/financial/trends`.
    trend_intervals: list[CatalogOption]
    trend_dimensions: list[CatalogOption]
    # Whether an answer counts the two sides of a transfer between the owner's
    # own accounts. `/transactions` defaults to `include`, every total to
    # `exclude`.
    transfer_views: list[CatalogOption]
    # How a rate is quoted. Not interchangeable: 19.56 % E.A. is 1.4999 % a
    # month, 19.56 % nominal anual is 1.63 %, and offering this as a free
    # field is how a loan ends up projected at a tenth of its interest.
    rate_bases: list[CatalogOption]
    # What a recurring charge is a proportion of — the insurance on the debt,
    # the insurance on the property, the flat fee, the withholding.
    charge_bases: list[CatalogOption]
    # How a loan's instalment is put together.
    amortization_styles: list[CatalogOption]
    # Which side of a transfer a hand-entered leg is. `source` is money
    # leaving the named account, `destination` money arriving on it — and on a
    # credit card, arriving is the debt going down.
    transfer_roles: list[CatalogOption]


class OpenAccountPayload(BaseModel):
    """Declare an account.

    Give it an instrument and every alert arriving under that bank and those
    last four digits lands here — including the ones that already arrived and
    have been waiting. Leave it out for what never emails: cash, a mortgage.
    """

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: AccountKind
    currency: Currency = Currency.COP
    # On a liability this is what has been spent so far, not the limit. The
    # two are separate fields because conflating them is the mistake that
    # makes a card read as fully drawn on the day it is declared.
    opening_balance: Decimal | None = Field(default=None, ge=0)
    credit_limit: Decimal | None = Field(default=None, ge=0)
    bank: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    instrument_kind: InstrumentKind | None = None
    last_four: str | None = Field(default=None, pattern=r"^\d{4,}$")

    @model_validator(mode="after")
    def _only_a_liability_has_a_limit(self) -> OpenAccountPayload:
        # Asks the kind rather than restating which kinds are liabilities:
        # `AccountKind.category` is the one place that decides it.
        if (
            self.credit_limit is not None
            and self.kind.category is not AccountCategory.LIABILITY
        ):
            raise ValueError(
                f"A {self.kind.value} account has no credit limit: a limit is "
                "what may be owed, and an asset owes nothing",
            )

        return self

    @model_validator(mode="after")
    def _instrument_is_all_or_nothing(self) -> OpenAccountPayload:
        # `bank` stands on its own — a mortgage names the bank that holds it
        # and has no card. The kind and the digits are what go together:
        # matching on one of them would merge two real accounts.
        if (self.instrument_kind is None) != (self.last_four is None):
            raise ValueError(
                "An instrument needs instrument_kind and last_four together: "
                "matching on one of them would merge two real accounts",
            )

        if self.instrument_kind is not None and not self.bank:
            raise ValueError("An instrument needs the bank whose alerts carry it")

        return self


class LinkInstrumentPayload(BaseModel):
    """Teach an account another of the names its alerts arrive under.

    One real account emails as a debit card for purchases and as an account
    number for transfers, under different last four digits. Both have to be
    linked or half its movements wait forever.
    """

    bank: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    instrument_kind: InstrumentKind
    last_four: str = Field(pattern=r"^\d{4,}$")


class RenameAccountPayload(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class SetCreditLimitPayload(BaseModel):
    """State or restate what a card may owe. `null` clears it."""

    credit_limit: Decimal | None = Field(default=None, ge=0)


class RestateBalancePayload(BaseModel):
    """What the account holds **now** — the figure the bank shows today.

    Not the opening balance: that one is derived from this and the movements
    already recorded, because it is the half nobody can look up. Signed, so
    an overdrawn account and an overpaid card can both be stated.
    """

    balance: Decimal = Field(ge=-MAX_MONEY, le=MAX_MONEY)


class RatePayload(BaseModel):
    """La tasa de interés, as a fraction and in the basis the bank quotes it.

    `0.1956`, never `19.56`. The three bases are the three ways a rate is
    printed here — `efectivo anual` (E.A.), `nominal anual` capitalizing
    monthly (N.A. M.V.) and the monthly rate itself (M.V.) — and they are not
    interchangeable: 19.56 % E.A. is 1.4999 % a month, while 19.56 % nominal
    is 1.63 %, and reading one as the other is a tenth of the interest.
    """

    value: Decimal = Field(ge=0, le=MAX_RATE)
    basis: RateBasis


class ChargePayload(BaseModel):
    """One thing charged every period besides the interest.

    A *seguro de vida deudores* is a rate on what is owed; a *seguro de
    incendio y terremoto* is a rate on what the property is insured for, which
    is not a balance this app holds; an administration fee is a flat amount;
    *retención en la fuente* is a rate on what an investment earned. Which
    `basis` is chosen decides which of `amount`, `rate` and `base` is
    required, and the wrong pairing is refused rather than silently priced
    at zero.

    `charged_to_balance` is false when the bank collects it somewhere else —
    its own direct debit on a savings account. It is then part of what has to
    be paid and never part of this balance, because that debit arrives as its
    own alert and posting it here as well would charge one insurance twice.
    """

    name: str = Field(min_length=1, max_length=MAX_CHARGE_NAME_LENGTH)
    basis: ChargeBasis
    amount: Decimal | None = Field(default=None, ge=0, le=MAX_MONEY)
    rate: Decimal | None = Field(default=None, ge=0, le=MAX_RATE)
    base: Decimal | None = Field(default=None, ge=0, le=MAX_MONEY)
    charged_to_balance: bool = True


class LoanTermsPayload(BaseModel):
    """What a loan costs, so what is owed can be more than what is unpaid.

    None of it can be read from a bank alert: an alert says a payment was
    made, never what the payment was made of. Paying 2 000 000 against
    60 000 000 does not leave 58 000 000, because the month charged interest
    first and the insurance after it.

    Amounts are bare figures in the account's own currency, like the credit
    limit and the restated balance — asking a caller to restate the currency
    only creates a way to get it wrong.

    `accrue_from` is where the arithmetic starts. **Left out it means today**,
    which is right for the ordinary case: somebody declaring a mortgage they
    have paid for three years states the balance their bank shows, and that
    figure already contains those three years of interest. Send the
    disbursement date instead — with the amount disbursed as the opening
    balance — to have the history rebuilt from the beginning.
    """

    rate: RatePayload
    disbursed_on: dt.date
    term_months: int = Field(ge=1, le=MAX_TERM_MONTHS)
    # La fecha de corte: the day interest is charged and the statement closes.
    statement_day: int = Field(ge=1, le=31)
    # When the instalment is due, usually a few days after the cut. Defaults
    # to the cut itself.
    payment_day: int | None = Field(default=None, ge=1, le=31)
    style: AmortizationStyle = AmortizationStyle.FRENCH
    # What was disbursed. Optional: somebody declaring a loan halfway through
    # its life knows what they owe and not always what they borrowed.
    principal: Decimal | None = Field(default=None, ge=0, le=MAX_MONEY)
    # La cuota, when the bank fixed one. Left out, it is computed from the
    # balance, the rate and what is left of the term.
    installment: Decimal | None = Field(default=None, ge=0, le=MAX_MONEY)
    # Whether the number on the statement already includes the insurance.
    # Getting this backwards misstates the capital portion by exactly the
    # insurance, every month.
    installment_covers_charges: bool = False
    charges: list[ChargePayload] = Field(
        default_factory=lambda: list[ChargePayload](),
        max_length=12,
    )
    accrue_from: dt.date | None = None


class InvestmentTermsPayload(BaseModel):
    """How an investment earns, when it earns at a rate at all.

    A CDT, a remunerated savings account or a fund with an agreed return has a
    rate and its value can be computed. Shares and a fund whose unit price
    moves have none: send no `rate` and state what it is worth through
    `POST /accounts/{id}/value` instead, which records the difference as a
    movement so the gain is visible rather than folded into a balance.
    """

    opened_on: dt.date
    statement_day: int = Field(ge=1, le=31)
    rate: RatePayload | None = None
    # El vencimiento of a CDT. Nothing accrues past it: the money stopped
    # being invested.
    matures_on: dt.date | None = None
    charges: list[ChargePayload] = Field(
        default_factory=lambda: list[ChargePayload](),
        max_length=12,
    )
    accrue_from: dt.date | None = None


class AccruePayload(BaseModel):
    """Post whatever the closed periods charged, up to a day.

    `through` left out means today, read in `timezone`: a cut on the 15th is
    the 15th where the owner lives, and a period closed in UTC would charge a
    Bogotá mortgage five hours early on the last day of some months.
    """

    through: dt.date | None = None
    timezone: str = Field(default=DEFAULT_TIMEZONE, max_length=64)


class RevaluePayload(BaseModel):
    """What this investment is worth now.

    The difference against what the ledger says is recorded **as a movement**,
    not folded into the opening balance the way `PUT /balance` does it. That
    is the whole point: a gain nobody can see as a row is a gain no report can
    attribute, and an investment whose return is invisible reads exactly like
    a savings account.
    """

    market_value: Decimal = Field(ge=-MAX_MONEY, le=MAX_MONEY)
    occurred_at: int | None = Field(
        default=None,
        ge=MIN_EPOCH_SECONDS,
        le=MAX_EPOCH_SECONDS,
    )


class EnterTransactionPayload(BaseModel):
    """Money that moved without an alert to announce it."""

    direction: MovementDirection
    amount: Decimal = Field(gt=0)
    currency: Currency = Currency.COP
    occurred_at: int
    counterparty: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    account_id: str | None = None
    bank: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class EnterTransferLegPayload(BaseModel):
    """The owner's side of a payment between two of their own balances.

    For the card paid from another bank, from a wallet or in cash — the case
    where no single alert can name both instruments, so nothing can write the
    pair. What this records is that the movement is *not* spending and *not*
    income, which is the one thing a total has to know about it.

    No `direction`: `role` fixes it. And `account_id` is required, unlike a
    plain manual entry — this asserts that a balance moved, and there is no
    balance to move without it.
    """

    role: TransferRole
    amount: Decimal = Field(gt=0)
    currency: Currency = Currency.COP
    occurred_at: int
    # What the owner calls the other side: "Nequi", "efectivo", "PSE". Free
    # text on purpose — it names something this app does not hold, so there is
    # no vocabulary it could be checked against.
    counterparty: str = Field(min_length=1, max_length=MAX_TEXT_LENGTH)
    account_id: str
    bank: str = Field(default="", max_length=MAX_TEXT_LENGTH)
    note: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class EditTransactionPayload(BaseModel):
    """A correction. Everything omitted is left alone.

    On a movement that came from a bank alert, the first correction keeps what
    the bank said: `stated` on the response is how a reader tells the two
    apart afterwards.
    """

    amount: Decimal | None = Field(default=None, gt=0)
    currency: Currency | None = None
    occurred_at: int | None = None
    counterparty: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_TEXT_LENGTH,
    )
    # An empty string clears the note; omitting the field leaves it alone.
    note: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    account_id: str | None = None
    # Take it off whatever account holds it, leaving it unassigned.
    detach: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> EditTransactionPayload:
        if self.detach and self.account_id is not None:
            raise ValueError("Give an account_id or detach, not both")

        if self.amount is not None and self.currency is None:
            raise ValueError("Changing the amount needs its currency too")

        if not any(
            (
                self.amount is not None,
                self.occurred_at is not None,
                self.counterparty is not None,
                self.note is not None,
                self.account_id is not None,
                self.detach,
            ),
        ):
            raise ValueError("Nothing to change")

        return self


# ---------------------------------------------------------- wiring the deps


@functools.lru_cache(maxsize=1)
def build_accounts() -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(
        client=get_dynamodb_client(),
        table_name=get_financial_settings().accounts_table,
    )


@functools.lru_cache(maxsize=1)
def build_ledger() -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(
        client=get_dynamodb_client(),
        table_name=get_financial_settings().accounts_table,
    )


@functools.lru_cache(maxsize=1)
def _build_manage_accounts() -> ManageAccountsUseCase:
    return ManageAccountsUseCase(
        accounts=build_accounts(),
        ledger=build_ledger(),
        event_publisher=LoggingEventPublisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_manage_transactions() -> ManageTransactionsUseCase:
    return ManageTransactionsUseCase(
        accounts=build_accounts(),
        ledger=build_ledger(),
        event_publisher=LoggingEventPublisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_manage_financing() -> ManageFinancingUseCase:
    return ManageFinancingUseCase(
        accounts=build_accounts(),
        event_publisher=LoggingEventPublisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_accrue_financing() -> AccrueFinancingUseCase:
    return AccrueFinancingUseCase(
        accounts=build_accounts(),
        ledger=build_ledger(),
        event_publisher=LoggingEventPublisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_revalue_account() -> RevalueAccountUseCase:
    return RevalueAccountUseCase(
        accounts=build_accounts(),
        ledger=build_ledger(),
        event_publisher=LoggingEventPublisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_read_financing() -> ReadFinancingUseCase:
    return ReadFinancingUseCase(accounts=build_accounts(), ledger=build_ledger())


@functools.lru_cache(maxsize=1)
def _build_list_accounts() -> ListAccountsUseCase:
    return ListAccountsUseCase(accounts=build_accounts())


@functools.lru_cache(maxsize=1)
def _build_get_account() -> GetAccountUseCase:
    return GetAccountUseCase(accounts=build_accounts())


@functools.lru_cache(maxsize=1)
def _build_list_transactions() -> ListTransactionsUseCase:
    return ListTransactionsUseCase(
        ledger=build_ledger(),
        merchants=build_merchant_directory(),
    )


@functools.lru_cache(maxsize=1)
def _build_get_transaction() -> GetTransactionUseCase:
    return GetTransactionUseCase(
        ledger=build_ledger(),
        merchants=build_merchant_directory(),
    )


@functools.lru_cache(maxsize=1)
def _build_summarize_spending() -> SummarizeSpendingUseCase:
    return SummarizeSpendingUseCase(
        ledger=build_ledger(),
        accounts=build_accounts(),
        merchants=build_merchant_directory(),
    )


@functools.lru_cache(maxsize=1)
def _build_read_trend() -> ReadSpendingTrendUseCase:
    return ReadSpendingTrendUseCase(
        ledger=build_ledger(),
        accounts=build_accounts(),
        merchants=build_merchant_directory(),
    )


def get_manage_accounts_use_case() -> ManageAccountsUseCase:
    return _build_manage_accounts()


def get_manage_transactions_use_case() -> ManageTransactionsUseCase:
    return _build_manage_transactions()


def get_manage_financing_use_case() -> ManageFinancingUseCase:
    return _build_manage_financing()


def get_accrue_financing_use_case() -> AccrueFinancingUseCase:
    return _build_accrue_financing()


def get_revalue_account_use_case() -> RevalueAccountUseCase:
    return _build_revalue_account()


def get_read_financing_use_case() -> ReadFinancingUseCase:
    return _build_read_financing()


def get_list_accounts_use_case() -> ListAccountsUseCase:
    return _build_list_accounts()


def get_account_use_case() -> GetAccountUseCase:
    return _build_get_account()


def get_list_transactions_use_case() -> ListTransactionsUseCase:
    return _build_list_transactions()


def get_transaction_use_case() -> GetTransactionUseCase:
    return _build_get_transaction()


def get_read_trend_use_case() -> ReadSpendingTrendUseCase:
    return _build_read_trend()


def get_summarize_spending_use_case() -> SummarizeSpendingUseCase:
    return _build_summarize_spending()


def get_read_history_use_case() -> ReadFinancialHistoryUseCase:
    return ReadFinancialHistoryUseCase(ledger=build_ledger(), accounts=build_accounts())


def get_merchant_directory() -> MerchantDirectory:
    return build_merchant_directory()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


# -------------------------------------------------------------- endpoints


@router.get("/catalog", response_model=FinancialCatalogResponse)
def get_catalog() -> FinancialCatalogResponse:
    """What a client may send, so a form cannot offer what the API rejects.

    Unauthenticated like the merchant catalogue beside it: this is the shape
    of the API, not anybody's data, and a client needs it to render the form
    that a session is created from.
    """
    return FinancialCatalogResponse(
        account_kinds=[
            AccountKindOption(
                value=kind.value,
                label=label(kind.value),
                category=kind.category.value,
                informational=kind.informational,
            )
            for kind in AccountKind
        ],
        instrument_kinds=options(InstrumentKind),
        account_categories=options(AccountCategory),
        currencies=options(Currency),
        movement_directions=options(MovementDirection),
        transaction_origins=options(TransactionOrigin),
        transaction_statuses=options(TransactionStatus),
        account_scopes=options(AccountScope),
        summary_groupings=options(SummaryGrouping),
        summary_orders=options(SummaryOrder),
        transaction_sorts=options(TransactionSort),
        trend_intervals=options(TrendInterval),
        trend_dimensions=options(TrendDimension),
        transfer_views=options(TransferView),
        transfer_roles=options(TransferRole),
        rate_bases=options(RateBasis),
        charge_bases=options(ChargeBasis),
        amortization_styles=options(AmortizationStyle),
    )


@router.get("/accounts", response_model=AccountListResponse)
def list_accounts(
    user_id: CurrentUser,
    use_case: Annotated[ListAccountsUseCase, Depends(get_list_accounts_use_case)],
    scope: Annotated[AccountScope, Query()] = AccountScope.OPEN,
) -> AccountListResponse:
    view = use_case.execute(user_id=user_id, scope=scope)

    return AccountListResponse(
        accounts=[_account_response(account) for account in view.accounts],
        net_worth=[_net_worth_response(figure) for figure in view.net_worth],
    )


@router.post(
    "/accounts",
    response_model=AccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def open_account(
    user_id: CurrentUser,
    payload: OpenAccountPayload,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    with _domain_errors():
        account = use_case.open(
            OpenAccountCommand(
                user_id=user_id,
                name=payload.name,
                kind=payload.kind,
                currency=payload.currency,
                opening_balance=(
                    None
                    if payload.opening_balance is None
                    else Money(
                        amount=payload.opening_balance,
                        currency=payload.currency,
                    )
                ),
                credit_limit=(
                    None
                    if payload.credit_limit is None
                    else Money(
                        amount=payload.credit_limit,
                        currency=payload.currency,
                    )
                ),
                bank=payload.bank,
                instrument_kind=payload.instrument_kind,
                last_four=payload.last_four,
            ),
        )

    return _account_response(account)


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(
    user_id: CurrentUser,
    account_id: str,
    use_case: Annotated[GetAccountUseCase, Depends(get_account_use_case)],
) -> AccountResponse:
    account = use_case.execute(
        user_id=user_id,
        account_id=_account_id(account_id),
    )

    if account is None:
        raise _no_such_account(account_id)

    return _account_response(account)


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
def rename_account(
    user_id: CurrentUser,
    account_id: str,
    payload: RenameAccountPayload,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    with _domain_errors():
        account = use_case.rename(
            RenameAccountCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                name=payload.name,
            ),
        )

    return _account_response(account)


@router.put("/accounts/{account_id}/balance", response_model=AccountResponse)
def restate_balance(
    user_id: CurrentUser,
    account_id: str,
    payload: RestateBalancePayload,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    """Correct what this account holds, without touching a single movement.

    For the ordinary case of declaring an account and not knowing what it
    held before the alerts Finflow already has: open it at zero, then send
    the figure the bank shows today. The opening balance is solved backwards
    so the ledger still adds up to it, every movement keeps counting exactly
    once, and net worth follows from the corrected number.

    PUT because the body carries the whole fact. Allowed on a closed account:
    correcting what was already there is not new money moving.
    """
    with _domain_errors():
        account = use_case.restate_balance(
            RestateBalanceCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                balance=payload.balance,
            ),
        )

    return _account_response(account)


@router.put("/accounts/{account_id}/credit-limit", response_model=AccountResponse)
def set_credit_limit(
    user_id: CurrentUser,
    account_id: str,
    payload: SetCreditLimitPayload,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    """State or restate what this card may owe.

    PUT rather than PATCH: the body carries the whole fact, and sending no
    limit clears it rather than leaving the old one in place.
    """
    with _domain_errors():
        account = use_case.set_credit_limit(
            SetCreditLimitCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                credit_limit=payload.credit_limit,
            ),
        )

    return _account_response(account)


@router.post("/accounts/{account_id}/instruments", response_model=AccountResponse)
def link_instrument(
    user_id: CurrentUser,
    account_id: str,
    payload: LinkInstrumentPayload,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    """Add another card or account number whose alerts belong here.

    One real account emails as a debit card for purchases and as an account
    number for transfers. Linking is always the owner's call — inferring it
    would be guessing about somebody's money — and it adopts the movements
    already waiting under that key.
    """
    with _domain_errors():
        account = use_case.link_instrument(
            LinkInstrumentCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                bank=payload.bank,
                instrument_kind=payload.instrument_kind,
                last_four=payload.last_four,
            ),
        )

    return _account_response(account)


@router.post("/accounts/{account_id}/close", response_model=AccountResponse)
def close_account(
    user_id: CurrentUser,
    account_id: str,
    use_case: Annotated[ManageAccountsUseCase, Depends(get_manage_accounts_use_case)],
) -> AccountResponse:
    """Stop taking movements, keeping the history and the balance.

    Not a delete: a closed account still explains past spending, and a
    paid-off loan closing at zero is exactly what should stay visible.
    """
    with _domain_errors():
        account = use_case.close(
            CloseAccountCommand(user_id=user_id, account_id=_account_id(account_id)),
        )

    return _account_response(account)


@router.put("/accounts/{account_id}/loan", response_model=AccountResponse)
def set_loan_terms(
    user_id: CurrentUser,
    account_id: str,
    payload: LoanTermsPayload,
    use_case: Annotated[
        ManageFinancingUseCase,
        Depends(get_manage_financing_use_case),
    ],
) -> AccountResponse:
    """State what this loan costs: the rate, the term, the cut, the insurance.

    Only a loan or a mortgage takes these. A credit card is deliberately left
    out even though it charges interest too: its interest is charged on
    whatever part of the statement went unpaid, which nothing in this app
    knows, and posting a month of it would invent a debt for everybody who
    pays their card in full.

    PUT because the body carries the whole fact — sending it again replaces
    the terms rather than merging into them. Every period already posted stays
    exactly as it was: a rate corrected today did not change what last March
    actually charged.
    """
    with _domain_errors():
        account = use_case.set_loan(
            SetLoanTermsCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                rate=_rate(payload.rate),
                disbursed_on=payload.disbursed_on,
                term_months=payload.term_months,
                statement_day=payload.statement_day,
                payment_day=payload.payment_day,
                style=payload.style,
                principal=payload.principal,
                installment=payload.installment,
                installment_covers_charges=payload.installment_covers_charges,
                charges=[_charge(charge) for charge in payload.charges],
                accrue_from=payload.accrue_from,
            ),
        )

    return _account_response(account)


@router.put("/accounts/{account_id}/investment", response_model=AccountResponse)
def set_investment_terms(
    user_id: CurrentUser,
    account_id: str,
    payload: InvestmentTermsPayload,
    use_case: Annotated[
        ManageFinancingUseCase,
        Depends(get_manage_financing_use_case),
    ],
) -> AccountResponse:
    """State how this investment earns, when it earns at a rate at all.

    Leave `rate` out for variable income — shares, a fund whose unit price
    moves. Nothing about those can be computed, and what they are worth is
    stated through `POST /accounts/{id}/value` instead.
    """
    with _domain_errors():
        account = use_case.set_investment(
            SetInvestmentTermsCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                opened_on=payload.opened_on,
                statement_day=payload.statement_day,
                rate=None if payload.rate is None else _rate(payload.rate),
                matures_on=payload.matures_on,
                charges=[_charge(charge) for charge in payload.charges],
                accrue_from=payload.accrue_from,
            ),
        )

    return _account_response(account)


@router.delete("/accounts/{account_id}/financing", response_model=AccountResponse)
def clear_financing(
    user_id: CurrentUser,
    account_id: str,
    use_case: Annotated[
        ManageFinancingUseCase,
        Depends(get_manage_financing_use_case),
    ],
) -> AccountResponse:
    """Stop computing charges, keeping every period already posted.

    The rows stay: they are movements like any other and the balance is their
    running total, so taking them back would be inventing a different history.
    What stops is the future.
    """
    with _domain_errors():
        account = use_case.clear(
            ClearFinancingCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
            ),
        )

    return _account_response(account)


@router.get("/accounts/{account_id}/financing", response_model=FinancingResponse)
def read_financing(
    user_id: CurrentUser,
    account_id: str,
    use_case: Annotated[ReadFinancingUseCase, Depends(get_read_financing_use_case)],
    periods: Annotated[int, Query(ge=1, le=MAX_SCHEDULE_PERIODS)] = (
        DEFAULT_SCHEDULE_PERIODS
    ),
    as_of: Annotated[dt.date | None, Query()] = None,
    timezone: Annotated[str, Query(max_length=64)] = DEFAULT_TIMEZONE,
) -> FinancingResponse:
    """La tabla de amortización, and what settling today would take.

    Worked out from the balance the ledger holds right now, never from the
    amount originally borrowed: a table built from the principal describes a
    loan nobody has. Nothing here is stored, because it assumes every
    instalment is paid on the day it is due and the first real payment that
    lands early or late moves the whole table.
    """
    with _domain_errors():
        view = use_case.execute(
            user_id=user_id,
            account_id=_account_id(account_id),
            periods=periods,
            as_of=as_of,
            timezone=_known_timezone(timezone),
        )

    return _financing_response(view)


@router.post("/accounts/{account_id}/accrue", response_model=AccrualResponse)
def accrue_account(
    user_id: CurrentUser,
    account_id: str,
    payload: AccruePayload,
    use_case: Annotated[
        AccrueFinancingUseCase,
        Depends(get_accrue_financing_use_case),
    ],
) -> AccrualResponse:
    """Post what the closed periods charged, as ordinary movements.

    A month of interest and the insurance it carried become ledger rows, so
    the balance stays the running total of things somebody can read — and so
    the interest shows in a month's spending, which is where it belongs: it is
    the part of a loan that actually costs money, while the instalment itself
    is a transfer between two of the owner's own balances.

    Safe to call as often as you like. Each charge is identified by its
    account and its period, so a second call writes a key the ledger already
    holds and is refused there; `skipped` says how many.
    """
    with _domain_errors():
        results = use_case.execute(
            AccrueFinancingCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                through=payload.through,
                timezone=_known_timezone(payload.timezone),
            ),
        )

    return _accrual_response(results[0])


@router.post("/accrue", response_model=list[AccrualResponse])
def accrue_everything(
    user_id: CurrentUser,
    payload: AccruePayload,
    use_case: Annotated[
        AccrueFinancingUseCase,
        Depends(get_accrue_financing_use_case),
    ],
) -> list[AccrualResponse]:
    """The same, for every financed account this user holds.

    The shape a scheduled run wants, and the shape a client wants when a
    screen opens: one call brings every loan and every fixed-income position
    up to date, and accounts with nothing to charge answer with a reason
    rather than an error.
    """
    with _domain_errors():
        results = use_case.execute(
            AccrueFinancingCommand(
                user_id=user_id,
                through=payload.through,
                timezone=_known_timezone(payload.timezone),
            ),
        )

    return [_accrual_response(result) for result in results]


@router.post("/accounts/{account_id}/value", response_model=AccrualResponse)
def revalue_account(
    user_id: CurrentUser,
    account_id: str,
    payload: RevaluePayload,
    use_case: Annotated[
        RevalueAccountUseCase,
        Depends(get_revalue_account_use_case),
    ],
) -> AccrualResponse:
    """State what this investment is worth now, recording the difference.

    Not the same thing as `PUT /accounts/{id}/balance`, and the difference is
    the whole reason this exists. A restatement solves the opening balance
    backwards so the ledger still adds up, which is right for a savings
    account whose history is incomplete — and wrong here, because the gain
    then lives in the opening balance and every report answers that the
    position returned nothing. This records it as a movement instead.

    Stating the value it already has changes nothing and is not an error,
    which is what makes a double submit harmless.
    """
    with _domain_errors():
        account, transaction = use_case.execute(
            RevalueAccountCommand(
                user_id=user_id,
                account_id=_account_id(account_id),
                market_value=payload.market_value,
                occurred_at=(
                    None
                    if payload.occurred_at is None
                    else PosixTime.from_epoch_seconds(payload.occurred_at)
                ),
            ),
        )

    return AccrualResponse(
        account=_account_response(account),
        posted=[_transaction_response(AttributedTransaction(transaction=transaction))]
        if transaction is not None
        else [],
        skipped=0,
        accrued_through=(
            None
            if account.accrued_through is None
            else account.accrued_through.isoformat()
        ),
        reason=None if transaction is not None else "the value has not changed",
    )


@router.get("/history", response_model=FinancialHistoryResponse)
def read_history(
    user_id: CurrentUser,
    use_case: Annotated[
        ReadFinancialHistoryUseCase,
        Depends(get_read_history_use_case),
    ],
    months: Annotated[int, Query(ge=1, le=MAX_HISTORY_MONTHS)] = DEFAULT_HISTORY_MONTHS,
    timezone: Annotated[str, Query(max_length=64)] = DEFAULT_TIMEZONE,
) -> FinancialHistoryResponse:
    """How this money has moved month by month, and how this month compares.

    Nothing here is stored or scheduled. Restating what an account holds
    solves its opening balance backwards, so the opening balance plus every
    movement up to an instant *is* the balance at that instant — history is a
    replay of the ledger rather than a snapshot table to keep in step.

    So this is the current best reconstruction of the past, not a log of what
    was believed at the time: declaring an account today, or correcting a
    balance, changes what last March reports. That is the same property that
    makes adoption retroactive, and it is right.

    The comparison is aligned by day of the month rather than by whole months.
    Against a finished previous month, a month three days old always reports
    spending down by most of it — true, and useless.
    """
    with _domain_errors():
        history = use_case.execute(
            HistoryQuery(
                user_id=user_id,
                months=months,
                # Refused here rather than deep in the replay, and refused the
                # same way `/summary` refuses it.
                timezone=_known_timezone(timezone),
            ),
        )

    return _history_response(history)


@router.get("/net-worth", response_model=list[NetWorthResponse])
def get_net_worth(
    user_id: CurrentUser,
    use_case: Annotated[ListAccountsUseCase, Depends(get_list_accounts_use_case)],
) -> list[NetWorthResponse]:
    """Assets minus liabilities, one figure per currency held.

    Empty for somebody who declared no accounts, which is an ordinary answer:
    they are watching what comes in and goes out, not a net position.
    """
    view = use_case.execute(user_id=user_id, scope=AccountScope.ALL)

    return [_net_worth_response(figure) for figure in view.net_worth]


@router.get("/transactions", response_model=TransactionListResponse)
def list_transactions(
    user_id: CurrentUser,
    use_case: Annotated[
        ListTransactionsUseCase,
        Depends(get_list_transactions_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
    account_id: Annotated[str | None, Query()] = None,
    unassigned: Annotated[bool | None, Query()] = None,
    origin: Annotated[TransactionOrigin | None, Query()] = None,
    direction: Annotated[MovementDirection | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=MAX_TEXT_LENGTH)] = None,
    merchant_id: Annotated[str | None, Query(max_length=64)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    since: Annotated[
        int | None,
        Query(alias="from", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    until: Annotated[
        int | None,
        Query(alias="to", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    transfers: Annotated[TransferView, Query()] = TransferView.INCLUDE,
    currency: Annotated[Currency | None, Query()] = None,
    sort: Annotated[TransactionSort, Query()] = TransactionSort.DATE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionListResponse:
    """Movements, newest first, each with the merchant behind its text.

    `merchant_id` and `category` are Merchant's answer, so a movement whose
    counterparty nobody has resolved yet matches neither — it is unknown, not
    uncategorized. `from` is included and `to` is not, so two consecutive
    months can be asked for without one movement landing in both.

    `transfers` defaults to `include` here and to `exclude` on `/summary`,
    which is the one place these two surfaces deliberately disagree: both
    sides of a card payment belong in the list, because they explain why an
    account fell, and in no total, because nothing was spent. A screen showing
    a figure from `/summary` beside the list behind it should ask for
    `transfers=exclude` on both.

    `sort=amount` answers "my ten largest expenses this month" in one call. It
    needs a `currency`, because without one the order would be deciding that
    100 USD is smaller than 5 000 COP.
    """
    with _domain_errors():
        page = use_case.execute(
            TransactionQuery(
                filter=_movement_filter(
                    user_id=user_id,
                    account_id=account_id,
                    unassigned=unassigned,
                    origin=origin,
                    direction=direction,
                    search=search,
                    merchant_id=merchant_id,
                    category=_known_category(category, merchants),
                    since=since,
                    until=until,
                    transfers=transfers,
                    currency=currency,
                ),
                limit=limit,
                offset=offset,
                sort=sort,
            ),
        )

    return TransactionListResponse(
        transactions=[_transaction_response(entry) for entry in page.transactions],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=SpendingSummaryResponse)
def summarize_spending(
    user_id: CurrentUser,
    use_case: Annotated[
        SummarizeSpendingUseCase,
        Depends(get_summarize_spending_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
    group_by: Annotated[SummaryGrouping, Query()] = SummaryGrouping.MONTH,
    since: Annotated[
        int | None,
        Query(alias="from", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    until: Annotated[
        int | None,
        Query(alias="to", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    account_id: Annotated[str | None, Query()] = None,
    unassigned: Annotated[bool | None, Query()] = None,
    origin: Annotated[TransactionOrigin | None, Query()] = None,
    direction: Annotated[MovementDirection | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=MAX_TEXT_LENGTH)] = None,
    merchant_id: Annotated[str | None, Query(max_length=64)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    transfers: Annotated[TransferView, Query()] = TransferView.EXCLUDE,
    currency: Annotated[Currency | None, Query()] = None,
    order: Annotated[SummaryOrder, Query()] = SummaryOrder.MOVEMENTS,
    top: Annotated[int | None, Query(ge=1, le=100)] = None,
    compare: Annotated[bool, Query()] = False,
    timezone: Annotated[str, Query(max_length=64)] = DEFAULT_TIMEZONE,
) -> SpendingSummaryResponse:
    """What a period adds up to, broken down by day, week, month, weekday,
    category, merchant or account — and totalled per currency, never across
    them.

    It takes the same filters as `/transactions`, so a bucket here can be
    opened as a list by repeating the query with its key — with `weekday` the
    one exception, since there is no filter for "every Monday". `timezone`
    only affects the time groupings, and it matters: a purchase at 8pm on the
    31st falls in the next month once it is read in UTC.

    `transfers` defaults to `exclude` here, unlike on `/transactions`: money
    moved between two of the owner's own accounts is neither spending nor
    income, and counting it would report a card payment as the month's largest
    expense and again as income on the card. `only` answers the opposite
    question — what did I move between my own accounts.

    Three parameters exist for reports specifically:

    * `currency` pins the answer to one, which is what makes the other two
      answerable — nothing here converts between two currencies.
    * `order=amount` ranks the buckets by money rather than by frequency, and
      `top` keeps that many and adds the rest into `others`. Together they are
      the eight slices a donut can show. `top` is refused on a stretch of time,
      which is narrowed with `from`/`to` instead.
    * `compare=true` also runs the window of equal length immediately before
      this one, so each bucket can be drawn against what it was. It needs
      `from` and `to`, since without a length there is no previous window. On
      a time grouping it reports the period total and nothing per bucket:
      `2026-08` against `2026-07` is two different months, not one month twice.
    """
    with _domain_errors():
        summary = use_case.execute(
            SummaryQuery(
                filter=_movement_filter(
                    user_id=user_id,
                    account_id=account_id,
                    unassigned=unassigned,
                    origin=origin,
                    direction=direction,
                    search=search,
                    merchant_id=merchant_id,
                    category=_known_category(category, merchants),
                    since=since,
                    until=until,
                    transfers=transfers,
                    currency=currency,
                ),
                group_by=group_by,
                order=order,
                top=top,
                compare=compare,
                timezone=_known_timezone(timezone),
            ),
        )

    return _summary_response(summary, timezone=timezone)


@router.get("/trends", response_model=SpendingTrendResponse)
def read_trend(
    user_id: CurrentUser,
    use_case: Annotated[
        ReadSpendingTrendUseCase,
        Depends(get_read_trend_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
    interval: Annotated[TrendInterval, Query()] = TrendInterval.MONTH,
    dimension: Annotated[TrendDimension, Query()] = TrendDimension.CATEGORY,
    periods: Annotated[int, Query(ge=1, le=MAX_TREND_BUCKETS)] = DEFAULT_TREND_PERIODS,
    series: Annotated[int | None, Query(ge=1, le=MAX_TREND_SERIES)] = None,
    order: Annotated[SummaryOrder, Query()] = SummaryOrder.MOVEMENTS,
    since: Annotated[
        int | None,
        Query(alias="from", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    until: Annotated[
        int | None,
        Query(alias="to", ge=MIN_EPOCH_SECONDS, le=MAX_EPOCH_SECONDS),
    ] = None,
    account_id: Annotated[str | None, Query()] = None,
    unassigned: Annotated[bool | None, Query()] = None,
    origin: Annotated[TransactionOrigin | None, Query()] = None,
    direction: Annotated[MovementDirection | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=MAX_TEXT_LENGTH)] = None,
    merchant_id: Annotated[str | None, Query(max_length=64)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    transfers: Annotated[TransferView, Query()] = TransferView.EXCLUDE,
    currency: Annotated[Currency | None, Query()] = None,
    timezone: Annotated[str, Query(max_length=64)] = DEFAULT_TIMEZONE,
) -> SpendingTrendResponse:
    """How spending moved over time, split into the bands a chart stacks.

    `/summary` answers one dimension at a time — what a period adds up to, or
    how it splits by category, never both. This answers the two together:
    restaurants against transport against groceries, month by month. Asking
    `/summary` for that a month at a time is twelve round trips whose buckets
    can each be ranked differently, so they cannot be stacked without the
    client reconciling them first.

    Two properties are what a chart needs and what this guarantees. The bands
    are **ranked once over the whole range**, so every bucket stacks the same
    ones in the same order. And the buckets are **dense** and each series has
    exactly one point per bucket, in the same order: a month nothing happened
    in is a zero, and a client zips `series[].points` against `buckets` by
    index without ever filling a gap.

    The range is `periods` intervals back from now, or an explicit `from`/`to`
    widened to the intervals it touches — half of August charted beside the
    whole of September reports a fall that did not happen. It takes the same
    filters as `/transactions`, so any band can be opened as the list behind
    it by repeating the query with the band's key.

    `dimension=none` is one undivided band, which is not the same question as
    the rest: every point already carries `incoming` and `outgoing`, so that
    one band is the cashflow chart.
    """
    with _domain_errors():
        trend = use_case.execute(
            TrendQuery(
                filter=_movement_filter(
                    user_id=user_id,
                    account_id=account_id,
                    unassigned=unassigned,
                    origin=origin,
                    direction=direction,
                    search=search,
                    merchant_id=merchant_id,
                    category=_known_category(category, merchants),
                    since=since,
                    until=until,
                    transfers=transfers,
                    currency=currency,
                ),
                interval=interval,
                dimension=dimension,
                periods=periods,
                series=series,
                order=order,
                timezone=_known_timezone(timezone),
            ),
        )

    return _trend_response(trend)


@router.post(
    "/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def enter_transaction(
    user_id: CurrentUser,
    payload: EnterTransactionPayload,
    use_case: Annotated[
        ManageTransactionsUseCase,
        Depends(get_manage_transactions_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
) -> TransactionResponse:
    """Record money the bank never emailed about.

    An automatic payment, cash, a transfer that produced no alert. The account
    is optional: somebody watching only what comes in and goes out has none.
    """
    with _domain_errors():
        transaction = use_case.enter(
            EnterTransactionCommand(
                user_id=user_id,
                direction=payload.direction,
                amount=Money(amount=payload.amount, currency=payload.currency),
                occurred_at=PosixTime.from_epoch_seconds(payload.occurred_at),
                counterparty=payload.counterparty,
                account_id=(
                    None
                    if payload.account_id is None
                    else _account_id(payload.account_id)
                ),
                bank=payload.bank,
                note=payload.note,
            ),
        )

    return _transaction_response(_attributed(transaction, merchants))


@router.post(
    "/transactions/transfer",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def enter_transfer_leg(
    user_id: CurrentUser,
    payload: EnterTransferLegPayload,
    use_case: Annotated[
        ManageTransactionsUseCase,
        Depends(get_manage_transactions_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
) -> TransactionResponse:
    """Record paying a card, or moving money, from outside this app.

    Paying a credit card from an account at the *same* bank needs nothing
    here: that alert names both instruments and the pair is written from it.
    This is for the other way round — paid from another bank, from a wallet,
    in cash — where only one side is ever knowable, and recording it as an
    ordinary movement would count a payment as an expense or as income.

    The movement lands on the account named, moving its balance like any
    other, and stays out of every total. On a credit card, `role=destination`
    is its debt going down.
    """
    with _domain_errors():
        transaction = use_case.enter_transfer_leg(
            EnterTransferLegCommand(
                user_id=user_id,
                role=payload.role,
                amount=Money(amount=payload.amount, currency=payload.currency),
                occurred_at=PosixTime.from_epoch_seconds(payload.occurred_at),
                counterparty=payload.counterparty,
                account_id=_account_id(payload.account_id),
                bank=payload.bank,
                note=payload.note,
            ),
        )

    return _transaction_response(_attributed(transaction, merchants))


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    user_id: CurrentUser,
    transaction_id: str,
    use_case: Annotated[GetTransactionUseCase, Depends(get_transaction_use_case)],
) -> TransactionResponse:
    entry = use_case.execute(user_id=user_id, transaction_id=transaction_id)

    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No movement {transaction_id}",
        )

    return _transaction_response(entry)


@router.patch("/transactions/{transaction_id}", response_model=TransactionResponse)
def edit_transaction(
    user_id: CurrentUser,
    transaction_id: str,
    payload: EditTransactionPayload,
    use_case: Annotated[
        ManageTransactionsUseCase,
        Depends(get_manage_transactions_use_case),
    ],
    merchants: Annotated[MerchantDirectory, Depends(get_merchant_directory)],
) -> TransactionResponse:
    with _domain_errors():
        transaction = use_case.edit(
            EditTransactionCommand(
                user_id=user_id,
                transaction_id=transaction_id,
                amount=(
                    None
                    if payload.amount is None or payload.currency is None
                    else Money(amount=payload.amount, currency=payload.currency)
                ),
                occurred_at=(
                    None
                    if payload.occurred_at is None
                    else PosixTime.from_epoch_seconds(payload.occurred_at)
                ),
                counterparty=payload.counterparty,
                note=payload.note,
                account_id=(
                    None
                    if payload.account_id is None
                    else _account_id(payload.account_id)
                ),
                detach=payload.detach,
            ),
        )

    return _transaction_response(_attributed(transaction, merchants))


@router.delete(
    "/transactions/{transaction_id}",
    response_model=DeletedTransactionResponse,
)
def delete_transaction(
    user_id: CurrentUser,
    transaction_id: str,
    use_case: Annotated[
        ManageTransactionsUseCase,
        Depends(get_manage_transactions_use_case),
    ],
) -> DeletedTransactionResponse:
    """Erase a movement, and give the balance back what it took.

    For a movement that should not be there at all: a purchase that was
    reversed, something entered twice, a row created while trying things out.
    Whatever it took off an account comes back — a two-thousand expense
    deleted is two thousand the account holds again — and the balance is
    recomputed from the rows that remain rather than nudged, so it cannot end
    up disagreeing with them.

    Not the same as `PATCH` with `detach`, which only takes the movement off
    its account: that one still exists and still counts in what came in and
    went out.

    Both sides of a transfer go together. Erasing either row of a payment
    between two of the owner's own accounts erases the other and restores both
    balances — half of it would be a row claiming money moved to a movement
    that is no longer there. A leg paid from outside this app has no second
    row and goes alone.
    """
    with _domain_errors():
        result = use_case.delete(
            DeleteTransactionCommand(
                user_id=user_id,
                transaction_id=transaction_id,
            ),
        )

    return DeletedTransactionResponse(
        erased=[movement.id.value for movement in result.erased],
        accounts=[_account_response(account) for account in result.restored],
    )


# ----------------------------------------------------------------- helpers


def _account_id(value: str) -> AccountId:
    try:
        return AccountId.from_string(value)
    except ValueError as error:
        raise _no_such_account(value) from error


def _no_such_account(account_id: str) -> HTTPException:
    """Missing rather than forbidden: whether somebody else owns an account is
    not something this endpoint tells a stranger.
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No account {account_id}",
    )


def _account_response(account: Account) -> AccountResponse:
    return AccountResponse(
        id=str(account.id.value),
        name=account.name,
        kind=account.kind.value,
        category=account.category.value,
        informational=account.informational,
        currency=account.currency.value,
        balance=str(account.balance.signed_amount),
        opening_balance=str(account.opening_balance.signed_amount),
        credit_limit=(
            None if account.credit_limit is None else str(account.credit_limit.amount)
        ),
        available=(None if account.available is None else str(account.available)),
        movements_applied=account.movements_applied,
        opened_at=account.opened_at.as_epoch_seconds(),
        closed_at=(
            None if account.closed_at is None else account.closed_at.as_epoch_seconds()
        ),
        bank=account.bank,
        instruments=sorted(print_.value for print_ in account.fingerprints),
        loan=None if account.loan is None else _loan_terms_response(account.loan),
        investment=(
            None
            if account.investment is None
            else _investment_terms_response(account.investment)
        ),
        accrued_through=(
            None
            if account.accrued_through is None
            else account.accrued_through.isoformat()
        ),
    )


def _rate(payload: RatePayload) -> InterestRate:
    return InterestRate(value=payload.value, basis=payload.basis)


def _charge(payload: ChargePayload) -> ChargeDraft:
    return ChargeDraft(
        name=payload.name,
        basis=payload.basis,
        amount=payload.amount,
        rate=payload.rate,
        base=payload.base,
        charged_to_balance=payload.charged_to_balance,
    )


def _rate_response(rate: InterestRate) -> RateResponse:
    return RateResponse(
        value=str(rate.value),
        basis=rate.basis,
        # Rounded where a screen would round anyway: these are the converted
        # figures, and thirty significant digits of `(1+i)^(1/12)` is noise a
        # client would have to trim itself.
        effective_annual=str(round(rate.effective_annual, 8)),
        monthly=str(round(rate.monthly, 8)),
    )


def _charge_response(charge: RecurringCharge) -> ChargeResponse:
    return ChargeResponse(
        name=charge.name,
        basis=charge.basis,
        amount=None if charge.amount is None else str(charge.amount.amount),
        rate=None if charge.rate is None else str(charge.rate),
        base=None if charge.base is None else str(charge.base.amount),
        charged_to_balance=charge.charged_to_balance,
    )


def _loan_terms_response(terms: LoanTerms) -> LoanTermsResponse:
    return LoanTermsResponse(
        rate=_rate_response(terms.rate),
        disbursed_on=terms.disbursed_on.isoformat(),
        term_months=terms.term_months,
        statement_day=terms.statement_day,
        payment_day=terms.due_day,
        style=terms.style,
        principal=None if terms.principal is None else str(terms.principal.amount),
        installment=(
            None if terms.installment is None else str(terms.installment.amount)
        ),
        installment_covers_charges=terms.installment_covers_charges,
        charges=[_charge_response(charge) for charge in terms.charges],
        matures_on=terms.matures_on().isoformat(),
    )


def _investment_terms_response(terms: InvestmentTerms) -> InvestmentTermsResponse:
    return InvestmentTermsResponse(
        opened_on=terms.opened_on.isoformat(),
        statement_day=terms.statement_day,
        rate=None if terms.rate is None else _rate_response(terms.rate),
        matures_on=None if terms.matures_on is None else terms.matures_on.isoformat(),
        charges=[_charge_response(charge) for charge in terms.charges],
    )


def _charge_amount_response(charge: ChargeAmount) -> ChargeAmountResponse:
    return ChargeAmountResponse(
        name=charge.name,
        amount=str(charge.amount.amount),
        charged_to_balance=charge.charged_to_balance,
    )


def _payment_response(payment: ScheduledPayment) -> ScheduledPaymentResponse:
    return ScheduledPaymentResponse(
        starts_on=payment.period.starts_on.isoformat(),
        ends_on=payment.period.ends_on.isoformat(),
        due_on=payment.due_on.isoformat(),
        opening_balance=str(payment.opening_balance.amount),
        interest=str(payment.interest.amount),
        charges=[_charge_amount_response(charge) for charge in payment.charges],
        principal=str(payment.principal.amount),
        due=str(payment.due.amount),
        closing_balance=str(payment.closing_balance.amount),
    )


def _schedule_response(schedule: LoanSchedule) -> LoanScheduleResponse:
    return LoanScheduleResponse(
        payments=[_payment_response(payment) for payment in schedule.payments],
        total_interest=str(schedule.total_interest.amount),
        total_charges=str(schedule.total_charges.amount),
        total_due=str(schedule.total_due.amount),
        settles_on=(
            None if schedule.settles_on is None else schedule.settles_on.isoformat()
        ),
        negatively_amortizing=schedule.negatively_amortizing,
    )


def _return_response(period: ProjectedReturn) -> ProjectedReturnResponse:
    return ProjectedReturnResponse(
        starts_on=period.period.starts_on.isoformat(),
        ends_on=period.period.ends_on.isoformat(),
        opening_balance=str(period.opening_balance.amount),
        earned=str(period.earned.amount),
        charges=[_charge_amount_response(charge) for charge in period.charges],
        closing_balance=str(period.closing_balance.amount),
    )


def _projection_response(
    projection: InvestmentProjection,
) -> InvestmentProjectionResponse:
    return InvestmentProjectionResponse(
        periods=[_return_response(period) for period in projection.periods],
        total_earned=str(projection.total_earned.amount),
        total_charges=str(projection.total_charges.amount),
        value_at_end=str(projection.value_at_end.amount),
        matures_on=(
            None if projection.matures_on is None else projection.matures_on.isoformat()
        ),
    )


def _performance_response(
    performance: InvestmentPerformance,
) -> InvestmentPerformanceResponse:
    return InvestmentPerformanceResponse(
        contributed=str(performance.contributed.amount),
        withdrawn=str(performance.withdrawn.amount),
        # Signed: a position that lost money reported as a gain is worse than
        # no figure at all.
        earned=str(performance.earned.signed_amount),
    )


def _financing_response(view: FinancingView) -> FinancingResponse:
    return FinancingResponse(
        account=_account_response(view.account),
        as_of=view.as_of.isoformat(),
        pending_interest=str(view.pending_interest.amount),
        periods_due=view.periods_due,
        payoff=None if view.payoff is None else str(view.payoff.amount),
        next_statement_on=view.next_statement_on.isoformat(),
        next_due_on=(
            None if view.next_due_on is None else view.next_due_on.isoformat()
        ),
        schedule=None if view.schedule is None else _schedule_response(view.schedule),
        projection=(
            None if view.projection is None else _projection_response(view.projection)
        ),
        performance=(
            None
            if view.performance is None
            else _performance_response(view.performance)
        ),
    )


def _accrual_response(result: AccrualResult) -> AccrualResponse:
    return AccrualResponse(
        account=_account_response(result.account),
        posted=[
            _transaction_response(AttributedTransaction(transaction=transaction))
            for transaction in result.posted
        ],
        skipped=result.skipped,
        accrued_through=(
            None
            if result.accrued_through is None
            else result.accrued_through.isoformat()
        ),
        reason=result.reason,
    )


def _net_worth_response(figure: NetWorth) -> NetWorthResponse:
    return NetWorthResponse(
        currency=figure.currency.value,
        assets=str(figure.assets),
        liabilities=str(figure.liabilities),
        total=str(figure.total),
    )


def _movement_filter(
    *,
    user_id: UserId,
    account_id: str | None,
    unassigned: bool | None,
    origin: TransactionOrigin | None,
    direction: MovementDirection | None,
    search: str | None,
    merchant_id: str | None,
    category: str | None,
    since: int | None,
    until: int | None,
    transfers: TransferView,
    currency: Currency | None = None,
) -> MovementFilter:
    """The filters `/transactions`, `/summary` and `/trends` share, read once.

    Every surface takes them so a bucket in a report can be opened as the list
    of movements behind it, and two readings of one query string would be two
    chances for those answers to disagree.
    """
    return MovementFilter(
        user_id=user_id,
        account_id=None if account_id is None else _account_id(account_id),
        unassigned=unassigned,
        origin=origin,
        direction=direction,
        search=search,
        merchant_id=merchant_id,
        category=category,
        since=None if since is None else PosixTime.from_epoch_seconds(since),
        until=None if until is None else PosixTime.from_epoch_seconds(until),
        transfers=transfers,
        currency=currency,
    )


def _attributed(
    transaction: Transaction,
    merchants: MerchantDirectory,
) -> AttributedTransaction:
    """A movement a write just produced, read back the way a list reads it.

    Without this a `PATCH` would answer `merchant: null` for a counterparty
    that plainly has one, and a client refreshing its cache from the response
    would drop the attribution until the next full reload.
    """
    attributed = merchants.attribute(
        user_id=transaction.user_id,
        counterparties=[transaction.counterparty],
    )

    return AttributedTransaction(
        transaction=transaction,
        merchant=attributed.get(transaction.counterparty),
    )


def _known_category(
    category: str | None,
    merchants: MerchantDirectory,
) -> str | None:
    """Refuse a category that names nothing, rather than answering nothing.

    An unknown value would filter every movement out and return an empty page,
    which on a money screen reads as "you spent nothing here" — the one wrong
    answer worse than an error.
    """
    if category is None or category in merchants.categories():
        return category

    raise HTTPException(
        # Renamed in Starlette 1.x to match RFC 9110, which calls 422
        # "Unprocessable Content". Same number, same response.
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Unknown category: {category!r}",
    )


def _known_timezone(name: str) -> str:
    """Refuse a timezone rather than quietly falling back to UTC, which would
    move somebody's late-evening spending into the following month.
    """
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown timezone: {name!r}",
        ) from error

    return name


def _transaction_response(entry: AttributedTransaction) -> TransactionResponse:
    transaction = entry.transaction
    stated = transaction.stated
    merchant = entry.merchant

    return TransactionResponse(
        id=transaction.id.value,
        direction=transaction.direction.value,
        amount=str(transaction.amount.amount),
        currency=transaction.amount.currency.value,
        occurred_at=transaction.occurred_at.as_epoch_seconds(),
        counterparty=transaction.counterparty,
        bank=transaction.bank,
        origin=transaction.origin.value,
        status=transaction.status.value,
        account_id=(
            None
            if transaction.account_id is None
            else str(transaction.account_id.value)
        ),
        note=transaction.note,
        stated=(
            None
            if stated is None
            else StatedResponse(
                amount=str(stated.amount.amount),
                currency=stated.amount.currency.value,
                occurred_at=stated.occurred_at.as_epoch_seconds(),
                counterparty=stated.counterparty,
            )
        ),
        merchant=(
            None
            if merchant is None
            else MerchantResponse(
                id=merchant.merchant_id,
                display_name=merchant.display_name,
                category=merchant.category,
                needs_review=merchant.needs_review,
            )
        ),
        transfer=(
            None
            if (leg := transaction.transfer) is None
            else TransferResponse(
                id=leg.transfer_id.value,
                role=leg.role.value,
                external=leg.counterpart_is_external,
                counterpart_movement_id=(
                    None if leg.counterpart_id is None else leg.counterpart_id.value
                ),
                counterpart_instrument_kind=leg.counterpart_instrument_kind,
                counterpart_last_four=leg.counterpart_last_four,
            )
        ),
    )


def _summary_response(
    summary: SpendingSummary,
    *,
    timezone: str,
) -> SpendingSummaryResponse:
    return SpendingSummaryResponse(
        group_by=summary.group_by.value,
        timezone=timezone,
        order=summary.order.value,
        totals=[_totals_response(figure) for figure in summary.totals],
        groups=[_group_response(group) for group in summary.groups],
        others=(None if summary.others is None else _group_response(summary.others)),
        folded=summary.folded,
        previous_totals=(
            None
            if summary.previous_totals is None
            else [_totals_response(figure) for figure in summary.previous_totals]
        ),
        previous_starts_at=(
            None
            if summary.previous_since is None
            else summary.previous_since.as_epoch_seconds()
        ),
        previous_ends_at=(
            None
            if summary.previous_until is None
            else summary.previous_until.as_epoch_seconds()
        ),
    )


def _group_response(group: SummaryGroup) -> SummaryGroupResponse:
    return SummaryGroupResponse(
        key=group.key,
        label=group.label,
        totals=[_totals_response(figure) for figure in group.totals],
        movements=group.movements,
        previous_totals=(
            None
            if group.previous_totals is None
            else [_totals_response(figure) for figure in group.previous_totals]
        ),
    )


def _trend_response(trend: SpendingTrend) -> SpendingTrendResponse:
    return SpendingTrendResponse(
        interval=trend.interval.value,
        dimension=trend.dimension.value,
        timezone=trend.timezone,
        starts_at=trend.starts_at.as_epoch_seconds(),
        ends_at=trend.ends_at.as_epoch_seconds(),
        buckets=[_trend_bucket_response(bucket) for bucket in trend.buckets],
        series=[_trend_series_response(series) for series in trend.series],
        others=(None if trend.others is None else _trend_series_response(trend.others)),
        folded=trend.folded,
        totals=[_totals_response(figure) for figure in trend.totals],
    )


def _trend_bucket_response(bucket: TrendBucket) -> TrendBucketResponse:
    return TrendBucketResponse(
        key=bucket.key,
        starts_at=bucket.starts_at.as_epoch_seconds(),
        ends_at=bucket.ends_at.as_epoch_seconds(),
        partial=bucket.partial,
    )


def _trend_series_response(series: TrendSeries) -> TrendSeriesResponse:
    return TrendSeriesResponse(
        key=series.key,
        label=series.label,
        points=[
            TrendPointResponse(
                bucket=point.bucket,
                totals=[_totals_response(figure) for figure in point.totals],
            )
            for point in series.points
        ],
        totals=[_totals_response(figure) for figure in series.totals],
        movements=series.movements,
    )


def _history_response(history: FinancialHistory) -> FinancialHistoryResponse:
    return FinancialHistoryResponse(
        timezone=history.timezone,
        months=[_month_response(point) for point in history.months],
        comparison=_comparison_response(history.comparison),
    )


def _month_response(point: MonthlyPoint) -> MonthlyPointResponse:
    return MonthlyPointResponse(
        key=point.key,
        starts_at=point.starts_at.as_epoch_seconds(),
        ends_at=point.ends_at.as_epoch_seconds(),
        partial=point.partial,
        totals=[_totals_response(figure) for figure in point.totals],
        net_worth=[_net_worth_response(figure) for figure in point.net_worth],
    )


def _comparison_response(comparison: PeriodComparison) -> PeriodComparisonResponse:
    return PeriodComparisonResponse(
        key=comparison.key,
        starts_at=comparison.starts_at.as_epoch_seconds(),
        through=comparison.through.as_epoch_seconds(),
        previous_key=comparison.previous_key,
        previous_starts_at=comparison.previous_starts_at.as_epoch_seconds(),
        previous_through=comparison.previous_through.as_epoch_seconds(),
        clamped=comparison.clamped,
        totals=[_totals_response(figure) for figure in comparison.totals],
        previous_totals=[
            _totals_response(figure) for figure in comparison.previous_totals
        ],
        net_worth=[_net_worth_response(figure) for figure in comparison.net_worth],
        previous_net_worth=[
            _net_worth_response(figure) for figure in comparison.previous_net_worth
        ],
    )


def _totals_response(figure: SpendingTotals) -> SpendingTotalsResponse:
    return SpendingTotalsResponse(
        currency=figure.currency.value,
        incoming=str(figure.incoming),
        outgoing=str(figure.outgoing),
        net=str(figure.net),
        movements=figure.movements,
    )


@contextlib.contextmanager
def _domain_errors() -> Generator[None]:
    """Turn the refusals the model makes into the answers HTTP has for them."""
    try:
        yield
    except (AccountNotFoundError, TransactionNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except AccountAlreadyExistsError as error:
        # Not a bad request: what refuses it is the state of the user's
        # accounts, not the shape of the request.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except AccountClosedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except TransactionAlreadyAssignedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except CurrencyMismatchError as error:
        # Never converted: an exchange rate is a fact about a moment nobody
        # recorded here.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except FinancingTermsError as error:
        # The terms cannot describe a loan or an investment: a rate typed as a
        # percentage, a cut on the 45th, an insurance quoted on a principal
        # nobody stated. A sentence the owner has to read and act on, so it
        # goes back with its reason rather than as a balance that quietly
        # grows twenty times a month.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except NotFinancedError as error:
        # The request is well formed and the account exists; what refuses it
        # is that nobody has said what it costs. 409, because no rewriting of
        # the query would answer it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except TransferLegError as error:
        # The request is well formed and the movement exists; what refuses it
        # is that this row is half of one fact. 409 rather than 400: nothing
        # about the body could be rewritten to make it work.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
