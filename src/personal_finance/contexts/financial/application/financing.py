"""Making an account's own arithmetic happen, and reading what it will do.

Three things live here, and the split between them is the design:

* **Declaring the terms.** What a loan costs and how an investment earns are
  facts only the owner has. Nothing about them can be read out of a bank
  alert, because an alert says a payment was made and never says what the
  payment was made of.
* **Posting what a closed period charged.** A month of interest, the insurance
  it carried, the withholding on what a CDT earned — each becomes an ordinary
  ledger row, so the balance stays the running total of things somebody can
  read. Idempotent by construction: a charge is identified by its account and
  its period, so a second run writes a key the ledger already holds.
* **Reading what happens next.** The amortization table, what it would cost to
  settle today, what a fixed-income position is worth at maturity. All of it
  computed from the balance the ledger has right now and none of it stored: a
  projection that outlived the payment it assumed would be a lie with a
  timestamp on it.

**Where the balance for a period comes from.** Not from the account's current
total — pricing a year of periods that way would charge every month the same
interest. The ledger is replayed to each cut instead, which is the same
authority `rebuild` uses, and the accrual rows already posted are part of that
replay. That is what makes the interest compound: a period is charged on a
balance that already carries the month before it.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from personal_finance.contexts.financial.application.commands import (
    AccrueFinancingCommand,
    ChargeDraft,
    ClearFinancingCommand,
    RevalueAccountCommand,
    SetInvestmentTermsCommand,
    SetLoanTermsCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
)
from personal_finance.contexts.financial.application.ports import (
    AccountRepository,
    TransactionLedger,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    FinancingTermsError,
)
from personal_finance.contexts.financial.domain.financing import (
    MAX_DATE,
    MIN_DATE,
    VALUATION_LABEL,
    AccruedPeriod,
    InvestmentProjection,
    InvestmentTerms,
    LoanSchedule,
    LoanTerms,
    RecurringCharge,
    StatementPeriod,
    accrue_period,
    first_cut_after,
    last_cut_on_or_before,
    on_day,
    project_investment,
    project_loan,
    statement_periods,
    valuation_item,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountId,
    Balance,
    MovementDirection,
    TransactionOrigin,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import Money, PosixTime, UserId


DEFAULT_TIMEZONE = "America/Bogota"

# How far a schedule looks ahead when the caller does not say. Twelve rows is
# a year, which is what somebody checking a mortgage against its statement
# wants; the whole term is available by asking for it.
DEFAULT_SCHEDULE_PERIODS = 12
MAX_SCHEDULE_PERIODS = 600

# How many times one investment may make the exact same move — the same pair of
# figures, on the same day — before the next one is refused. Each turn past the
# first costs one round trip to discover it is taken, and a dozen is already
# far past anybody correcting a typo.
MAX_VALUATION_TURNS = 12


class ValuationTurnsExhaustedError(Exception):
    """Raised when one move has been repeated all day and cannot take another turn.

    Its own error rather than a quiet non-answer: the request was refused and
    the balance did not move, which is exactly what the old collision reported
    as success.
    """


class NotFinancedError(Exception):
    """Raised when an account is asked for arithmetic it has no terms for.

    Its own error rather than an empty answer: a schedule for an account whose
    rate nobody stated is not "no rows", it is a question that cannot be
    answered yet, and the two look identical on a screen.
    """


def zone_of(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Unknown timezone: {name!r}") from error


def local_midnight(day: dt.date, zone: dt.tzinfo) -> PosixTime:
    """The instant a calendar day begins where the owner lives.

    Every boundary in this module is a date somebody reads off a statement —
    "corte el 15" — and a date is only an instant once a timezone says so. In
    UTC a Bogotá cut would close five hours early, which on the last day of a
    month moves a whole period's interest into the wrong month.
    """
    return PosixTime.from_datetime(
        dt.datetime.combine(day, dt.time.min, tzinfo=zone),
    )


def today_in(zone: dt.tzinfo) -> dt.date:
    return dt.datetime.now(tz=zone).date()


class ManageFinancingUseCase:
    """Declaring, correcting and clearing what an account computes.

    None of these touch a balance, and that is deliberate even though every
    one of them changes what the balance will do. Terms describe the future;
    the periods already posted are rows in the ledger and stay exactly as they
    were, because a rate corrected today did not change what last March
    actually charged.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._events = event_publisher

    def set_loan(self, command: SetLoanTermsCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.set_loan_terms(
            LoanTerms(
                rate=command.rate,
                disbursed_on=command.disbursed_on,
                term_months=command.term_months,
                statement_day=command.statement_day,
                payment_day=command.payment_day,
                style=command.style,
                principal=_money(command.principal, account),
                installment=_money(command.installment, account),
                installment_covers_charges=command.installment_covers_charges,
                charges=_charges(command.charges, account),
            ),
            accrue_from=self._anchor(account, command.accrue_from),
        )
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def set_investment(self, command: SetInvestmentTermsCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.set_investment_terms(
            InvestmentTerms(
                opened_on=command.opened_on,
                statement_day=command.statement_day,
                rate=command.rate,
                matures_on=command.matures_on,
                charges=_charges(command.charges, account),
            ),
            accrue_from=self._anchor(account, command.accrue_from),
        )
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def clear(self, command: ClearFinancingCommand) -> Account:
        account = self._load(command.user_id, command.account_id)
        account.clear_financing()
        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return account

    def _anchor(self, account: Account, stated: dt.date | None) -> dt.date:
        """Where the arithmetic starts when the owner did not say.

        Today, and the reason is the number they typed. Somebody declaring a
        mortgage they have been paying for three years states the balance
        their bank shows them, and that figure already contains those three
        years of interest: charging them again would double the debt. Somebody
        rebuilding the history from disbursement says so explicitly, and gets
        it.

        An account already accruing keeps its cursor rather than restarting:
        the periods behind it are in the ledger, and moving the mark back
        would walk them again for nothing.
        """
        if stated is not None:
            return stated

        if account.accrued_through is not None:
            return account.accrued_through

        return today_in(zone_of(DEFAULT_TIMEZONE))

    def _load(self, user_id: UserId, account_id: AccountId) -> Account:
        account = self._accounts.find(user_id=user_id, account_id=account_id)

        if account is None:
            raise AccountNotFoundError(f"No account {account_id.value} for this user")

        return account


def _money(amount: Decimal | None, account: Account) -> Money | None:
    """Pair a bare figure with the only currency it could be in.

    The account's, always. A caller restating it would only be restating
    something this already knows, and a caller getting it wrong would be
    declaring a mortgage off by a factor of four thousand.
    """
    return None if amount is None else Money(amount=amount, currency=account.currency)


def _charges(
    drafts: Sequence[ChargeDraft],
    account: Account,
) -> tuple[RecurringCharge, ...]:
    return tuple(
        RecurringCharge(
            name=draft.name,
            basis=draft.basis,
            amount=_money(draft.amount, account),
            rate=draft.rate,
            base=_money(draft.base, account),
            charged_to_balance=draft.charged_to_balance,
        )
        for draft in drafts
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccrualResult:
    """What one account's accrual actually wrote.

    `posted` is the rows written now; `skipped` counts the ones a previous run
    had already written, which is the ordinary outcome of a schedule that
    fired twice and is worth being able to see rather than infer from silence.
    """

    account: Account
    posted: Sequence[Transaction] = ()
    skipped: int = 0
    accrued_through: dt.date | None = None
    reason: str | None = None


class AccrueFinancingUseCase:
    """Turns the periods that closed since last time into ledger rows.

    The order is the whole design, and it mirrors `RecordMovementUseCase`:

    1. **Walk the closed periods**, oldest first. A period still running has
       charged nothing yet, and posting a part-month would have to be taken
       back — on a ledger whose entire premise is that nothing ever is.
    2. **Replay the ledger to each cut** for the balance that period is priced
       against. The rows this run writes are part of that replay, which is
       what makes interest compound on interest.
    3. **Apply and write each charge conditionally**, the row and the balance
       change together, exactly as an alert is written.
    4. **Advance the cursor last.** A crash before it leaves periods already
       written and a mark that has not moved: the next run walks them again
       and every row is refused by its own key. Advancing first would skip
       them for good, which is the one failure this ordering exists to
       prevent.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
        event_publisher: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._ledger = ledger
        self._events = event_publisher

    def execute(self, command: AccrueFinancingCommand) -> Sequence[AccrualResult]:
        zone = zone_of(command.timezone)
        today = today_in(zone)
        # Never past today, whatever was asked for. A future date would post
        # interest for months that have not happened — and the cursor only
        # ever moves forward, so nothing would charge them again once they
        # did. `through` narrows the window; it cannot widen it.
        through = today if command.through is None else min(command.through, today)

        if command.account_id is not None:
            account = self._accounts.find(
                user_id=command.user_id,
                account_id=command.account_id,
            )

            if account is None:
                raise AccountNotFoundError(
                    f"No account {command.account_id.value} for this user",
                )

            return [self._accrue(account, through=through, zone=zone)]

        return [
            self._accrue(account, through=through, zone=zone)
            for account in self._accounts.list_by_user(command.user_id)
            if account.accrues and not account.is_closed
        ]

    def _accrue(
        self,
        account: Account,
        *,
        through: dt.date,
        zone: dt.tzinfo,
    ) -> AccrualResult:
        refusal = _cannot_accrue(account)

        if refusal is not None:
            return AccrualResult(account=account, reason=refusal)

        started = account.accrued_through or account.financing_started_on
        statement_day = account.statement_day

        if started is None or statement_day is None:  # pragma: no cover - guarded above
            return AccrualResult(account=account, reason="no terms")

        periods = statement_periods(
            since=started,
            through=min(through, _stops_on(account) or through),
            statement_day=statement_day,
        )

        if not periods:
            return AccrualResult(
                account=account,
                accrued_through=account.accrued_through,
                reason="no period has closed since the last run",
            )

        posted: list[Transaction] = []
        skipped = 0
        movements = sorted(
            self._ledger.list_movements(
                user_id=account.user_id,
                account_id=account.id,
            ),
            key=lambda movement: movement.occurred_at.as_epoch_seconds(),
        )
        replayed = account.opening_balance
        index = 0

        for period in periods:
            cut = local_midnight(period.ends_on, zone).as_epoch_seconds()

            while (
                index < len(movements)
                and movements[index].occurred_at.as_epoch_seconds() < cut
            ):
                replayed = account.balance_after(
                    [movements[index].as_movement()],
                    starting=replayed,
                )
                index += 1

            written, refused = self._post(
                account,
                period=period,
                opening=replayed,
                zone=zone,
            )
            skipped += refused

            for transaction in written:
                replayed = account.balance_after(
                    [transaction.as_movement()],
                    starting=replayed,
                )

            posted.extend(written)
            account.mark_accrued_through(period.ends_on)

        self._accounts.save(account)
        self._events.publish(account.pull_events())

        return AccrualResult(
            account=account,
            posted=posted,
            skipped=skipped,
            accrued_through=account.accrued_through,
        )

    def _post(
        self,
        account: Account,
        *,
        period: StatementPeriod,
        opening: Balance,
        zone: dt.tzinfo,
    ) -> tuple[list[Transaction], int]:
        """Write every charge one period produced, each on its own key."""
        if opening.signed_amount <= 0:
            # Nothing is charged on a settled loan or an empty position, not
            # even a flat fee: a finished loan that keeps charging is a debt
            # growing with nobody watching it.
            return ([], 0)

        accrued = account.price_period(period, opening=opening.amount)
        occurred_at = local_midnight(period.ends_on, zone)
        written: list[Transaction] = []
        refused = 0

        for posting in account.postings_for(accrued):
            transaction = Transaction.accrue(
                user_id=account.user_id,
                account_id=account.id,
                item=posting.item,
                label=posting.label,
                direction=posting.direction,
                amount=posting.amount,
                occurred_at=occurred_at,
                period_end=period.ends_on,
                bank=account.bank or "",
            )

            if _record(account, transaction, self._ledger):
                self._events.publish(transaction.pull_events())
                written.append(transaction)
            else:
                transaction.pull_events()
                refused += 1

        return (written, refused)


def _cannot_accrue(account: Account) -> str | None:
    """Why this account is left alone, in words a caller can hand on."""
    if account.financing is None:
        return "the account has no terms"

    if not account.accrues:
        return "the account earns at no stated rate, so its value is restated"

    if account.is_closed:
        return "the account is closed"

    return None


def _stops_on(account: Account) -> dt.date | None:
    """The last day anything is charged, when the product has one.

    A CDT stops earning at its vencimiento because the money stopped being
    invested. A loan has no such day: past its term it is in arrears, and
    arrears charge interest like every other month.
    """
    investment = account.investment

    return None if investment is None else investment.matures_on


def _record(
    account: Account,
    transaction: Transaction,
    ledger: TransactionLedger,
) -> bool:
    """Apply one computed charge to its account and write both, or neither.

    The same shape as every other write in this context: the aggregate is
    applied in memory to learn how far the balance moves, and the row and that
    movement go out as one conditional write. A refusal means a previous run
    already posted this period, so the in-memory balance is put back — the
    stored one never moved.
    """
    before = account.balance
    applied = account.movements_applied
    account.apply(transaction.as_movement())
    delta = account.balance.signed_amount - before.signed_amount

    if ledger.record(transaction=transaction, balance_delta=delta):
        return True

    account.balance = before
    account.movements_applied = applied
    account.pull_events()

    return False


class RevalueAccountUseCase:
    """States what an investment is worth, and records the difference as one.

    The alternative — restating the balance — solves the opening balance
    backwards so the ledger still adds up, which is right for correcting a
    savings account whose history is incomplete and wrong for an investment
    whose whole point is the gain. Under a restatement the gain lands in the
    opening balance and every report answers that the position returned
    nothing. Here it lands as a movement, so it can be seen, attributed and
    summed like anything else.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
        event_publisher: EventPublisher,
    ) -> None:
        self._accounts = accounts
        self._ledger = ledger
        self._events = event_publisher

    def execute(
        self, command: RevalueAccountCommand
    ) -> tuple[Account, Transaction | None]:
        account = self._holding(command)
        stated = Balance.from_signed(command.market_value, account.currency)
        occurred_at = command.occurred_at or PosixTime.now()

        for turn in range(1, MAX_VALUATION_TURNS + 1):
            delta = stated.signed_amount - account.balance.signed_amount

            if delta == 0:
                # Nothing moved, so nothing is recorded. This is also what
                # makes a double submit harmless: the second request sees the
                # value it just set and has nothing left to say.
                return (account, None)

            transaction = _valuation(
                account,
                stated=command.market_value,
                delta=delta,
                occurred_at=occurred_at,
                turn=turn,
            )

            if _record(account, transaction, self._ledger):
                self._events.publish(transaction.pull_events())
                self._events.publish(account.pull_events())

                return (account, transaction)

            transaction.pull_events()
            # This move, on this day, at this turn, is already written — and
            # the key cannot say which of the two things that means. The
            # balance can, so read it again, which the repository does
            # consistently for this reason. Already at the figure asked for,
            # and this was the same request arriving twice: nothing is left to
            # record, the same answer a second identical submit gets when it
            # arrives late enough to see the value it set. Anywhere else, and
            # it is a real move back to a figure this fund passed through
            # earlier today, which needs a turn of its own.
            account = self._holding(command)

            if account.balance.signed_amount == stated.signed_amount:
                return (account, None)

        raise ValuationTurnsExhaustedError(
            f"This value has been stated from the same balance "
            f"{MAX_VALUATION_TURNS} times today; try again tomorrow",
        )

    def _holding(self, command: RevalueAccountCommand) -> Account:
        account = self._accounts.find(
            user_id=command.user_id,
            account_id=command.account_id,
        )

        if account is None:
            raise AccountNotFoundError(
                f"No account {command.account_id.value} for this user",
            )

        if account.category is not AccountCategory.ASSET:
            raise FinancingTermsError(
                "Only something held has a market value: what is owed is "
                "restated, not revalued",
            )

        return account


def _valuation(
    account: Account,
    *,
    stated: Decimal,
    delta: Decimal,
    occurred_at: PosixTime,
    turn: int,
) -> Transaction:
    """The row one revaluation leaves: the gap between the two figures."""
    return Transaction.accrue(
        user_id=account.user_id,
        account_id=account.id,
        item=valuation_item(
            held=account.balance.signed_amount,
            stated=stated,
            turn=turn,
        ),
        label=VALUATION_LABEL,
        direction=(
            MovementDirection.INCOMING if delta > 0 else MovementDirection.OUTGOING
        ),
        amount=Money(amount=abs(delta), currency=account.currency),
        occurred_at=occurred_at,
        period_end=occurred_at.to_datetime()
        .astimezone(
            zone_of(DEFAULT_TIMEZONE),
        )
        .date(),
        bank=account.bank or "",
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InvestmentPerformance:
    """What a position has been given, what it has given back, and the gain.

    Every figure is read off the ledger rather than derived from the balance,
    which is the only way the gain is separable at all: a contribution and a
    return both raise the same number, and only the row says which it was.
    """

    contributed: Money
    withdrawn: Money
    # Net of everything the position charged — a management fee and the
    # withholding are both charges, and a return quoted before them is not a
    # return anybody received. A `Balance` rather than `Money` because it is
    # the one figure here that can be negative, and an investment that lost
    # money reported as a gain is worse than no figure at all.
    earned: Balance


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class FinancingView:
    """Everything an owner needs to see about an account that computes.

    `pending_interest` is the part nobody has been charged yet: the days
    between **the last cut** and today, priced at the same rate. It carries no
    charges, and that is not an omission — insurance is charged whole on the
    cut day, so a part-month owes none of it, and adding it would overstate a
    payoff by the one figure somebody is most likely to check.

    `periods_due` is the other half of that sentence and the reason it is
    measured from the cut rather than from the cursor. Months that closed and
    were never posted are not "interest running": they are charges the ledger
    is missing, and rolling them into one prorated figure would both mislabel
    them and understate them, since they compound. So they are counted
    instead, and the answer to a non-zero count is to post them.
    """

    account: Account
    as_of: dt.date
    # Interest accrued since the last cut and not yet posted.
    pending_interest: Money
    # Closed statement periods whose charges are not in the ledger yet. Zero
    # whenever the accrual is up to date, which is the ordinary state.
    periods_due: int
    # What settling today would take: the balance plus what it has accrued
    # since the last statement. None on an account holding nothing.
    payoff: Money | None
    next_statement_on: dt.date
    next_due_on: dt.date | None
    schedule: LoanSchedule | None = None
    projection: InvestmentProjection | None = None
    performance: InvestmentPerformance | None = None


class ReadFinancingUseCase:
    """What the account does next, computed fresh every time it is asked.

    Nothing here is stored, and that is the point: a table assumes every
    instalment is paid on the day it is due, so the moment a real payment
    lands — early, late, larger — the balance it starts from moves and the
    whole table with it. Storing one would be keeping a lie with a timestamp.
    """

    def __init__(
        self,
        *,
        accounts: AccountRepository,
        ledger: TransactionLedger,
    ) -> None:
        self._accounts = accounts
        self._ledger = ledger

    def execute(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
        periods: int = DEFAULT_SCHEDULE_PERIODS,
        as_of: dt.date | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> FinancingView:
        account = self._accounts.find(user_id=user_id, account_id=account_id)

        if account is None:
            raise AccountNotFoundError(f"No account {account_id.value} for this user")

        terms = account.financing

        if terms is None:
            raise NotFinancedError(
                f"Account {account_id.value} states no terms: there is no rate "
                "to project it at",
            )

        day = as_of or today_in(zone_of(timezone))

        if day < MIN_DATE or day > MAX_DATE:
            # The walk that builds a schedule adds months to this, and a date
            # at the edge of the calendar makes that raise from inside
            # `datetime` rather than come back as a refusal.
            raise FinancingTermsError(
                f"A schedule cannot be projected from {day.isoformat()}",
            )

        horizon = min(max(periods, 1), MAX_SCHEDULE_PERIODS)
        pending = self._pending(account, as_of=day)
        due = self._periods_due(account, as_of=day)
        outstanding = account.outstanding
        payoff = (
            None
            if outstanding is None
            else Money(
                amount=outstanding.amount + pending.interest.amount,
                currency=account.currency,
            )
        )
        next_cut = first_cut_after(day, terms.statement_day)

        return FinancingView(
            account=account,
            as_of=day,
            pending_interest=pending.interest,
            periods_due=due,
            payoff=payoff,
            next_statement_on=next_cut,
            next_due_on=(
                on_day(next_cut.year, next_cut.month, account.loan.due_day)
                if account.loan is not None
                else None
            ),
            schedule=(
                None
                if account.loan is None or payoff is None
                else project_loan(
                    terms=account.loan,
                    outstanding=payoff,
                    as_of=day,
                    periods=horizon,
                )
            ),
            projection=(
                None
                if account.investment is None or payoff is None
                else project_investment(
                    terms=account.investment,
                    value=payoff,
                    as_of=day,
                    periods=horizon,
                )
            ),
            performance=(
                None if account.investment is None else self._performance(account)
            ),
        )

    def _periods_due(self, account: Account, *, as_of: dt.date) -> int:
        """How many closed periods the ledger is still missing."""
        since = account.accrued_through or account.financing_started_on
        terms = account.financing

        if since is None or terms is None or not account.accrues:
            return 0

        return len(
            statement_periods(
                since=since,
                through=min(as_of, _stops_on(account) or as_of),
                statement_day=terms.statement_day,
            ),
        )

    def _pending(self, account: Account, *, as_of: dt.date) -> AccruedPeriod:
        """Interest for the days since **the last cut**, which nobody owes yet.

        Measured from the cut and not from the posting cursor, however far
        behind that has fallen. Interest between two cuts is charged whole on
        the second of them, so a cursor six months back does not mean six
        months of "running interest" — it means six charges the ledger never
        got, which compound and which `periods_due` counts instead. Rolling
        them in here would put a wrong number under a label that reads as a
        few days.

        Prorated 30/360 on the balance as it stands. An estimate, and said to
        be one: a payment landing tomorrow changes it, which is exactly why it
        is not written anywhere.
        """
        currency = account.currency
        nothing = AccruedPeriod(
            period=StatementPeriod(
                starts_on=as_of,
                ends_on=as_of + dt.timedelta(days=1),
            ),
            opening_balance=Money(amount=Decimal(0), currency=currency),
            interest=Money(amount=Decimal(0), currency=currency),
        )
        outstanding = account.outstanding
        terms = account.financing

        if outstanding is None or terms is None or not account.accrues:
            return nothing

        posted = account.accrued_through or account.financing_started_on
        cut = last_cut_on_or_before(as_of, terms.statement_day)
        # The later of the two: nothing before the cut is "running", and
        # nothing before the cursor has been left uncharged.
        since = cut if posted is None else max(posted, cut)

        if since >= as_of:
            return nothing

        return accrue_period(
            period=StatementPeriod(starts_on=since, ends_on=as_of, partial=True),
            opening=outstanding,
            rate=terms.rate,
        )

    def _performance(self, account: Account) -> InvestmentPerformance:
        currency = account.currency
        contributed = Decimal(0)
        withdrawn = Decimal(0)
        earned = Decimal(0)

        for movement in self._ledger.list_movements(
            user_id=account.user_id,
            account_id=account.id,
        ):
            if movement.amount.currency is not currency:
                continue

            incoming = movement.direction is MovementDirection.INCOMING

            if movement.origin is TransactionOrigin.ACCRUAL:
                earned += (
                    movement.amount.amount if incoming else -movement.amount.amount
                )
            elif incoming:
                contributed += movement.amount.amount
            else:
                withdrawn += movement.amount.amount

        return InvestmentPerformance(
            contributed=Money(amount=contributed, currency=currency),
            withdrawn=Money(amount=withdrawn, currency=currency),
            earned=Balance.from_signed(earned, currency),
        )
