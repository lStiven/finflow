from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
import uuid

from personal_finance.contexts.financial.domain.financing import (
    AmortizationStyle,
    ChargeBasis,
    InterestRate,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordMovementCommand:
    """One movement of money a bank announced, in Financial's own words.

    Everything here has already been read out of ingestion's vocabulary at the
    boundary, which is why nothing on it is an ingestion type. The instrument
    stays a pair of raw strings on purpose: what it means for routing —
    whether it can find an account, and what kind of account it would open —
    is Financial's decision, and `Transaction.from_alert` is where it is made.

    Deliberately absent: the notification id and anything else naming the
    email. What makes a movement the same movement is its content.
    """

    user_id: UserId
    bank: str
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    instrument_kind: str | None = None
    last_four: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RecordTransferCommand:
    """One alert that moved money between two instruments of the same owner.

    Two movements, one command: the sides have to be built together or they
    stop being a pair. Both instruments are required in full — a side without
    last four digits can never find its account, and a transfer with one
    routable side is exactly the half-recorded state this whole path exists to
    prevent.

    `source` is where the money left and `destination` where it arrived. On a
    card payment that is the account and the card, in that order; swapping
    them moves both balances the wrong way.
    """

    user_id: UserId
    bank: str
    amount: Money
    occurred_at: PosixTime
    source_instrument_kind: str
    source_last_four: str
    destination_instrument_kind: str
    destination_last_four: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class OpenAccountCommand:
    """An account its owner declared.

    The instrument is what makes the account start collecting: given a bank, a
    kind of instrument and its last four digits, every alert arriving under
    them lands here — including the ones that arrived before this moment. An
    account without one never matches an alert, which is right for cash and
    for a mortgage that emails nothing.
    """

    user_id: UserId
    name: str
    kind: AccountKind
    currency: Currency
    opening_balance: Money | None = None
    bank: str | None = None
    instrument_kind: InstrumentKind | None = None
    last_four: str | None = None
    # Only a liability has one, and it is not the opening balance: the balance
    # is what has been spent, this is what may be.
    credit_limit: Money | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LinkInstrumentCommand:
    """Teach an existing account another of the names its alerts arrive under.

    One real account emails as a debit card for purchases and as an account
    number for transfers. Linking is always the owner's decision: deciding it
    automatically would be guessing about somebody's money.
    """

    user_id: UserId
    account_id: AccountId
    bank: str
    instrument_kind: InstrumentKind
    last_four: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UnlinkInstrumentCommand:
    """Stop an account from answering to one of the names it was given.

    The correction for a card declared on the wrong account: linking is the
    owner's decision and so is undoing it. Named by the same three parts the
    linking took, not by the stored key — that key is a storage format, and a
    caller that had to spell it would be one that breaks when it changes.

    The movements that arrived under it go back to unassigned rather than
    staying on an account that no longer claims them, which is what lets the
    right account adopt them the moment it links the same card.
    """

    user_id: UserId
    account_id: AccountId
    bank: str
    instrument_kind: InstrumentKind
    last_four: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RenameAccountCommand:
    user_id: UserId
    account_id: AccountId
    name: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RestateBalanceCommand:
    """Say what the account holds now, and let the opening balance follow.

    The number is today's, not the starting one: somebody declaring an
    account rarely remembers what it held before the alerts Finflow already
    has, but their bank shows them the current figure. Signed, because an
    account legitimately goes below zero.
    """

    user_id: UserId
    account_id: AccountId
    # Bare amount, like the credit limit: the account carries the currency.
    balance: Decimal


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SetCreditLimitCommand:
    """State or restate what a card may owe.

    Its own command rather than a field on the rename above: a limit is a fact
    about the account that changes on the bank's schedule, not a correction to
    what the owner called it. `None` clears it.
    """

    user_id: UserId
    account_id: AccountId
    # Bare amount: the account carries the currency, and asking a caller to
    # restate it only creates a way to get it wrong.
    credit_limit: Decimal | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CloseAccountCommand:
    user_id: UserId
    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReopenAccountCommand:
    """Take movements again on an account that was closed by mistake.

    Closing is not a delete and this is not an undelete: the history stayed
    where it was the whole time. What comes back is only what closing took
    away — new movements, and being adopted by an alert again.
    """

    user_id: UserId
    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnterTransactionCommand:
    """Money the user says moved, that no alert announced.

    The path for an automatic payment the bank never emails, for cash, for
    anything a parser could not be expected to see. The account is optional:
    somebody watching only what comes in and goes out has none.
    """

    user_id: UserId
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    account_id: AccountId | None = None
    bank: str = ""
    note: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmScheduledChargeCommand:
    """One charge of a declared bill, the moment its owner says it happened.

    Deliberately close to `EnterTransactionCommand` — it *is* a movement
    somebody is asserting — and different in the one way that matters: the
    identity comes from `bill_id` and `period` rather than being fresh, so
    confirming the same period twice is one charge and not two.

    `amount` and `occurred_at` are what actually happened, which need not be
    what the bill projected: the gym raised its price, and the 4th was a
    Saturday. Neither takes part in the identity, so correcting either by
    confirming again cannot write a second row.
    """

    user_id: UserId
    bill_id: uuid.UUID
    period: dt.date
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    account_id: AccountId | None = None
    note: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EnterTransferLegCommand:
    """The owner's side of money they moved between their own balances.

    For a card paid from another bank, from a wallet or in cash — anything
    whose other side this app does not hold, and which is therefore neither
    spending nor income no matter how the single alert reads.

    No `direction`: `role` decides it, because a source is money leaving and a
    destination is money arriving, always. And the account is required, unlike
    a plain manual entry — a leg names no instrument, so nothing could ever
    adopt it later.
    """

    user_id: UserId
    role: TransferRole
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    account_id: AccountId
    bank: str = ""
    note: str | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class EditTransactionCommand:
    """A correction. Every field left None is left alone.

    `account_id` is three-valued on purpose: absent leaves the movement where
    it is, a value moves it, and `detach` takes it off the account holding it.
    """

    user_id: UserId
    transaction_id: str
    amount: Money | None = None
    occurred_at: PosixTime | None = None
    counterparty: str | None = None
    note: str | None = None
    account_id: AccountId | None = None
    detach: bool = False


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeleteTransactionCommand:
    """Erase a movement and give back whatever it took from a balance.

    The correction for a movement that should never have been recorded at
    all: a duplicate entered by hand, an alert for a purchase that was
    reversed, a test row. Not the same thing as detaching it — detached, the
    movement still exists and still shows up in what came in and what went
    out. This one leaves nothing behind.
    """

    user_id: UserId
    transaction_id: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ChargeDraft:
    """One recurring charge, before it knows what currency it is in.

    Bare amounts, like `RestateBalanceCommand.balance` and
    `SetCreditLimitCommand.credit_limit`: the account carries the currency,
    and asking a caller to restate it only creates a way to get it wrong. The
    use case pairs each figure with the account's own currency, which is the
    only place both are known at once.
    """

    name: str
    basis: ChargeBasis
    amount: Decimal | None = None
    rate: Decimal | None = None
    base: Decimal | None = None
    charged_to_balance: bool = True


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SetLoanTermsCommand:
    """What a loan costs, so what is owed can be more than what is unpaid.

    None of this can be read from a bank alert. An alert says a payment was
    made; it never says what the payment was made of, and the difference is
    the whole reason this command exists — 2 000 000 paid against 60 000 000
    owed does not leave 58 000 000, because the month charged interest first
    and the insurance after it.

    `accrue_from` is where the arithmetic starts, and the two right answers
    are far apart. Left out, it starts **today**: somebody declaring a
    mortgage they have paid for three years states the balance their bank
    shows, and that figure already contains those three years of interest —
    charging them again would double the debt. Set to the disbursement date,
    with the original amount as the opening balance, the history is rebuilt
    from the beginning instead.
    """

    user_id: UserId
    account_id: AccountId
    rate: InterestRate
    disbursed_on: dt.date
    term_months: int
    # La fecha de corte: the day interest is charged and the statement closes.
    statement_day: int
    # When the instalment is due. None means the cut itself.
    payment_day: int | None = None
    style: AmortizationStyle = AmortizationStyle.FRENCH
    principal: Decimal | None = None
    installment: Decimal | None = None
    installment_covers_charges: bool = False
    charges: Sequence[ChargeDraft] = ()
    accrue_from: dt.date | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SetInvestmentTermsCommand:
    """How an investment earns, when it earns by a rate at all.

    A CDT and a remunerated savings account have a rate and their value can be
    computed. Shares and a fund whose unit price moves have none, and their
    terms carry none: what they are worth is what the market says, which is
    what `RevalueAccountCommand` is for.
    """

    user_id: UserId
    account_id: AccountId
    opened_on: dt.date
    statement_day: int
    rate: InterestRate | None = None
    # El vencimiento of a CDT. Nothing accrues past it.
    matures_on: dt.date | None = None
    charges: Sequence[ChargeDraft] = ()
    accrue_from: dt.date | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ClearFinancingCommand:
    """Stop computing charges, keeping every period already posted.

    The rows stay because they are movements like any other and the balance is
    their running total. What stops is the future.
    """

    user_id: UserId
    account_id: AccountId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccrueFinancingCommand:
    """Post whatever the closed periods charged, up to a day.

    `account_id` left out sweeps every financed account this user holds, which
    is the shape a scheduled run wants. `through` left out means today, read in
    `timezone` — a cut on the 15th is the 15th where the owner lives, and a
    period closed in UTC would charge a Bogotá mortgage five hours early on
    the last day of some months.

    Running it twice changes nothing: each charge is identified by its account
    and its period, so the second attempt writes a key the ledger already
    holds and is refused there rather than here.
    """

    user_id: UserId
    account_id: AccountId | None = None
    through: dt.date | None = None
    timezone: str = "America/Bogota"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RevalueAccountCommand:
    """State what an investment is worth now, and record the difference.

    The counterpart of a rate, for everything a rate cannot describe: a share
    price, a fund's unit value, a property. The gap between what the ledger
    says and what the owner says is recorded **as a movement**, not folded
    into the opening balance the way a plain restatement is — and that is the
    entire point. A gain nobody can see as a row is a gain nobody can
    attribute, and an investment whose return is invisible is indistinguishable
    from a savings account.
    """

    user_id: UserId
    account_id: AccountId
    # Signed: an investment can be worth less than nothing only in theory, but
    # the movement recording the fall is an ordinary one.
    market_value: Decimal
    occurred_at: PosixTime | None = None
