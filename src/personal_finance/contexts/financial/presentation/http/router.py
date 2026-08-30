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
from decimal import Decimal
import functools
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from personal_finance.contexts.financial.application.commands import (
    CloseAccountCommand,
    EditTransactionCommand,
    EnterTransactionCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RenameAccountCommand,
    RestateBalanceCommand,
    SetCreditLimitCommand,
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
    MAX_HISTORY_MONTHS,
    MAX_PAGE_SIZE,
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
    SpendingSummary,
    SpendingTotals,
    SummarizeSpendingUseCase,
    SummaryGroup,
    SummaryGrouping,
    SummaryQuery,
    TransactionQuery,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    TransactionAlreadyAssignedError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransactionOrigin,
    TransactionStatus,
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
    # `asset` | `liability` — what decides whether this balance adds to net
    # worth or subtracts from it.
    category: str
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
    # `2026-08` for a month, otherwise the merchant, category or account id.
    # Null is the bucket the grouping could not place — a movement no account
    # claimed, or a counterparty no merchant owns yet. It belongs in the
    # answer: without it the groups stop adding up to `totals`.
    key: str | None
    label: str
    totals: list[SpendingTotalsResponse]
    movements: int


class SpendingSummaryResponse(BaseModel):
    group_by: str
    timezone: str
    # Over everything the filter matched, so a period total needs no second
    # call and no adding up of the buckets.
    totals: list[SpendingTotalsResponse]
    # Months run newest first; every other grouping runs busiest first, by
    # movement count — ordering by amount would compare two currencies, which
    # nothing here has a rate for.
    groups: list[SummaryGroupResponse]


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
    """An account kind, and which side of net worth it lands on.

    `category` is derived from the kind and never chosen, so a client can
    group the dropdown — and say "money you owe" against a balance — without
    duplicating the rule that decides it.
    """

    category: str


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


def get_manage_accounts_use_case() -> ManageAccountsUseCase:
    return _build_manage_accounts()


def get_manage_transactions_use_case() -> ManageTransactionsUseCase:
    return _build_manage_transactions()


def get_list_accounts_use_case() -> ListAccountsUseCase:
    return _build_list_accounts()


def get_account_use_case() -> GetAccountUseCase:
    return _build_get_account()


def get_list_transactions_use_case() -> ListTransactionsUseCase:
    return _build_list_transactions()


def get_transaction_use_case() -> GetTransactionUseCase:
    return _build_get_transaction()


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
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionListResponse:
    """Movements, newest first, each with the merchant behind its text.

    `merchant_id` and `category` are Merchant's answer, so a movement whose
    counterparty nobody has resolved yet matches neither — it is unknown, not
    uncategorized. `from` is included and `to` is not, so two consecutive
    months can be asked for without one movement landing in both.
    """
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
            ),
            limit=limit,
            offset=offset,
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
    timezone: Annotated[str, Query(max_length=64)] = DEFAULT_TIMEZONE,
) -> SpendingSummaryResponse:
    """What a period adds up to, broken down by month, category, merchant or
    account — and totalled per currency, never across them.

    It takes the same filters as `/transactions`, so any bucket here can be
    opened as a list by repeating the query with the bucket's key. `timezone`
    only affects `month`, and it matters: a purchase at 8pm on the 31st falls
    in the next month once it is read in UTC.
    """
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
            ),
            group_by=group_by,
            timezone=_known_timezone(timezone),
        ),
    )

    return _summary_response(summary, timezone=timezone)


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
) -> MovementFilter:
    """The filters `/transactions` and `/summary` share, read once.

    Both surfaces take them so a bucket in the summary can be opened as the
    list of movements behind it, and two readings of one query string would be
    two chances for those answers to disagree.
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
    )


def _summary_response(
    summary: SpendingSummary,
    *,
    timezone: str,
) -> SpendingSummaryResponse:
    return SpendingSummaryResponse(
        group_by=summary.group_by.value,
        timezone=timezone,
        totals=[_totals_response(figure) for figure in summary.totals],
        groups=[_group_response(group) for group in summary.groups],
    )


def _group_response(group: SummaryGroup) -> SummaryGroupResponse:
    return SummaryGroupResponse(
        key=group.key,
        label=group.label,
        totals=[_totals_response(figure) for figure in group.totals],
        movements=group.movements,
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
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
