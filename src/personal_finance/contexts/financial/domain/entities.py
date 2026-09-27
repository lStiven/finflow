from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
import datetime as dt
from decimal import Decimal
from typing import Self
import uuid

from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    AccountBalanceRebuilt,
    AccountBalanceRestated,
    AccountBalanceReversed,
    AccountClosed,
    AccountFinancingCleared,
    AccountFinancingSet,
    AccountFingerprintLinked,
    AccountFingerprintUnlinked,
    AccountOpened,
    AccountRenamed,
    AccountReopened,
    TransactionAssigned,
    TransactionEdited,
    TransactionErased,
    TransactionReclassified,
    TransactionRecorded,
    TransactionUnassigned,
)
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    FinancingTermsError,
    TransactionAlreadyAssignedError,
    TransferDeclarationError,
    TransferLegError,
)
from personal_finance.contexts.financial.domain.financing import (
    AccruedPeriod,
    InterestRate,
    InvestmentTerms,
    LoanTerms,
    PostedAccrual,
    RecurringCharge,
    StatementPeriod,
    accrue_period,
    postings,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    DeclarationRefusal,
    InstrumentKind,
    LedgerMovement,
    MovementDirection,
    MovementFingerprint,
    MovementId,
    StatedMovement,
    TransactionOrigin,
    TransactionStatus,
    TransferBasis,
    TransferId,
    TransferLeg,
    TransferRole,
    transfer_counterparty,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


MAX_ACCOUNT_NAME_LENGTH = 120

# The kinds whose balance moves on its own. A loan and a mortgage charge
# interest on what is owed and carry insurance the borrower did not choose,
# so what they owe next month is not what they owe now minus what they paid.
#
# A credit card is deliberately **not** here even though it charges interest
# too. Its interest is charged on whatever part of the statement was not paid
# in full, which nothing in this app knows: a card paid off every month costs
# nothing, and posting a month of interest on the balance would invent a debt
# for the people who owe none.
FINANCED_KINDS = frozenset({AccountKind.LOAN, AccountKind.MORTGAGE})


@dataclass(slots=True)
class Account(AggregateRoot[AccountId]):
    """One place a user's money sits, and what it holds right now.

    **Declared by its owner, never discovered.** Finflow works without a
    single account — every alert is still recorded, so what came in and what
    went out is visible on its own. An account is what somebody adds when
    they want more than that: a running state for one savings account, one
    card, one investment, without opening the bank's app to see it.

    Adding one is also what starts the association. An account carries the
    fingerprints its alerts arrive under — bank, instrument, last four — and
    every movement matching one of them lands on it, including the ones that
    already arrived before it existed.

    The balance is a running total, not the authority. The authority is the
    ledger of movements; this aggregate never decides whether it has seen a
    movement before, because that answer cannot be reached from memory when
    the queue is at-least-once and processes restart. `rebuild` recomputes the
    total from those rows, which is what makes both drift and a late
    adoption repairable.
    """

    user_id: UserId
    name: str
    kind: AccountKind
    currency: Currency
    opening_balance: Balance
    balance: Balance
    opened_at: PosixTime
    bank: str | None = None
    fingerprints: set[AccountFingerprint] = field(
        default_factory=lambda: set[AccountFingerprint](),
    )
    movements_applied: int = 0
    closed_at: PosixTime | None = None
    credit_limit: Money | None = None
    # What this account charges or earns on its own, and when. A loan or a
    # mortgage carries `loan`; an investment carries `investment`; everything
    # else carries neither, because nothing about a savings account's balance
    # can be computed — it is whatever the alerts say it is.
    loan: LoanTerms | None = None
    investment: InvestmentTerms | None = None
    # The last statement date whose charges are already in the ledger. A
    # cursor, not the authority: the authority is the row itself, whose
    # identity comes from the account and the period, so a run that crashed
    # after writing and before advancing this simply writes nothing the second
    # time. It only exists so a monthly job does not walk twenty years of
    # periods to discover it has nothing to do.
    accrued_through: dt.date | None = None

    def __post_init__(self) -> None:
        self.name = _valid_name(self.name)
        self._check_credit_limit(self.credit_limit)
        self._check_financing()

    @classmethod
    def open(
        cls,
        *,
        user_id: UserId,
        name: str,
        kind: AccountKind,
        currency: Currency,
        opened_at: PosixTime,
        opening_balance: Money | None = None,
        bank: str | None = None,
        instrument_kind: InstrumentKind | None = None,
        last_four: str | None = None,
        credit_limit: Money | None = None,
    ) -> Self:
        """Create the account its owner declared.

        The opening balance is what they say it holds today — zero when they
        do not know or do not care, which simply means the running total is
        movement since this moment. On a liability it is what is owed: on a
        credit card, what has been spent against the limit so far.

        `credit_limit` is that limit, and only a liability has one. The two
        together are what `available` needs; the limit is deliberately not the
        opening balance, which is the mistake the field exists to prevent.

        The instrument is optional: cash in a drawer and a mortgage that never
        emails have none, and an account without a fingerprint simply never
        matches an alert. Given one, all three parts are required — matching
        on bank and kind alone would merge every savings account somebody
        holds at one bank into a single wrong balance.
        """
        institution = (bank or "").strip().lower()
        opening = (
            Balance.zero(currency)
            if opening_balance is None
            else Balance(amount=opening_balance)
        )

        if opening.currency is not currency:
            raise CurrencyMismatchError(
                f"An opening balance in {opening.currency.value} cannot open a "
                f"{currency.value} account",
            )

        account = cls(
            id=AccountId.new(),
            user_id=user_id,
            name=name,
            kind=kind,
            currency=currency,
            opening_balance=opening,
            balance=opening,
            opened_at=opened_at,
            bank=institution or None,
            credit_limit=credit_limit,
        )
        account._announce_opening()

        if (instrument_kind is None) != (last_four is None):
            raise ValueError(
                "An instrument needs both its kind and its last four digits: "
                "half of one matches nothing, and the owner would only find "
                "out when their movements kept arriving unassigned",
            )

        if instrument_kind is not None and last_four is not None:
            account.link_fingerprint(
                AccountFingerprint.from_parts(
                    bank=institution,
                    instrument_kind=instrument_kind,
                    last_four=last_four,
                ),
            )

        return account

    @property
    def category(self) -> AccountCategory:
        return self.kind.category

    @property
    def informational(self) -> bool:
        """Watched rather than counted: outside net worth and every total.

        True for a loan and a mortgage. See `AccountKind.informational` for
        why, and note that it says nothing about the balance arithmetic —
        `category` still answers `LIABILITY`, so a payment arriving still
        lowers what is owed.
        """
        return self.kind.informational

    @property
    def available(self) -> Decimal | None:
        """What is left of the limit: the limit minus what is owed.

        `None` on anything without a limit, which is every asset and any
        liability whose owner did not state one — there is no number to
        report, and reporting the balance instead would read as credit
        somebody does not have.

        Signed, and it can go negative: a card can be over its limit, and that
        is a fact worth showing rather than clamping to zero.
        """
        if self.credit_limit is None:
            return None

        return self.credit_limit.amount - self.balance.signed_amount

    def set_credit_limit(self, limit: Money | None) -> None:
        """State or restate the limit. Banks change them; this is not an event
        about money, so the balance and the ledger are untouched.
        """
        self._check_credit_limit(limit)
        self.credit_limit = limit

    def _check_credit_limit(self, limit: Money | None) -> None:
        if limit is None:
            return

        if self.category is not AccountCategory.LIABILITY:
            raise ValueError(
                f"A {self.kind.value} account has no credit limit: a limit is "
                "what may be owed, and an asset owes nothing",
            )

        if limit.currency is not self.currency:
            raise CurrencyMismatchError(
                f"A {limit.currency.value} credit limit cannot sit on a "
                f"{self.currency.value} account",
            )

    # ------------------------------------------------------------ financing

    @property
    def financing(self) -> LoanTerms | InvestmentTerms | None:
        """The terms this account's balance moves under, whichever kind."""
        return self.loan if self.loan is not None else self.investment

    @property
    def accrues(self) -> bool:
        """Whether a closed period can be priced at all.

        False for an investment declared without a rate, which is not an
        omission: what a share is worth cannot be computed from anything, so
        its value is restated and the difference recorded instead.
        """
        terms = self.financing

        if terms is None:
            return False

        return True if isinstance(terms, LoanTerms) else terms.accrues

    @property
    def statement_day(self) -> int | None:
        """La fecha de corte: the day of the month a period closes on."""
        terms = self.financing

        return None if terms is None else terms.statement_day

    @property
    def financing_started_on(self) -> dt.date | None:
        """When this account began charging or earning: disbursement, or opening."""
        if self.loan is not None:
            return self.loan.disbursed_on

        return None if self.investment is None else self.investment.opened_on

    @property
    def rate(self) -> InterestRate | None:
        terms = self.financing

        if terms is None:
            return None

        return terms.rate

    @property
    def charges(self) -> tuple[RecurringCharge, ...]:
        terms = self.financing

        return () if terms is None else terms.charges

    @property
    def outstanding(self) -> Money | None:
        """What a period is charged against, or None when nothing is.

        The magnitude of the balance, but only while it is on the side its
        category expects — a debt that is owed, a holding that is held. A loan
        overpaid past zero and an investment in the red both accrue nothing:
        the arithmetic has no meaning there, and continuing it would grow a
        balance that should have stopped.
        """
        if self.balance.signed_amount <= 0:
            return None

        return self.balance.amount

    def set_loan_terms(self, terms: LoanTerms, *, accrue_from: dt.date) -> None:
        """State what this loan costs, so what is owed can be more than what
        is unpaid.

        `accrue_from` is where the charges start being computed, and it is
        asked for rather than assumed because the two sensible answers are far
        apart. Somebody declaring a mortgage they have paid for three years
        states today's balance and starts from today: the interest of those
        three years is already inside the figure their bank shows them, and
        posting it again would double a debt. Somebody entering a loan from
        its disbursement, with the original amount as the opening balance,
        starts there and gets the history rebuilt.
        """
        if self.kind not in FINANCED_KINDS:
            raise FinancingTermsError(
                f"A {self.kind.value} account has no loan terms: interest and "
                "an instalment describe money that was lent",
            )

        self._check_terms_currency(
            terms.charges,
            (terms.principal, terms.installment),
        )
        self.loan = terms
        self.investment = None
        self.accrued_through = accrue_from
        self._announce_financing()

    def set_investment_terms(
        self,
        terms: InvestmentTerms,
        *,
        accrue_from: dt.date,
    ) -> None:
        """State how this investment earns, when it earns by a rate at all."""
        if self.kind is not AccountKind.INVESTMENT:
            raise FinancingTermsError(
                f"A {self.kind.value} account has no investment terms",
            )

        self._check_terms_currency(terms.charges, ())
        self.investment = terms
        self.loan = None
        self.accrued_through = accrue_from
        self._announce_financing()

    def clear_financing(self) -> None:
        """Stop computing anything, keeping every period already posted.

        The rows stay: they are movements like any other and the balance is
        their running total, so taking them back would be inventing a
        different history. What stops is the future.
        """
        if self.financing is None:
            return

        self.loan = None
        self.investment = None
        self.accrued_through = None
        self.record_event(
            AccountFinancingCleared(account_id=self.id, user_id=self.user_id),
        )

    def mark_accrued_through(self, day: dt.date) -> None:
        """Move the cursor forward, never back.

        Backwards would re-walk periods already in the ledger. Every one of
        them would be refused by its own key, so the damage is wasted writes
        rather than a doubled charge — but only while the cut day is
        unchanged, and it is exactly the sort of guarantee that stops being
        true the day somebody edits their terms.
        """
        if self.accrued_through is None or day > self.accrued_through:
            self.accrued_through = day

    def price_period(self, period: StatementPeriod, *, opening: Money) -> AccruedPeriod:
        """What one closed period charged this account, on a stated balance.

        The balance is handed in rather than read off the account: pricing a
        run of periods means replaying the ledger to each cut, and an
        aggregate that answered from its current total would charge every
        month of a year the same interest.
        """
        terms = self.financing

        if terms is None:
            raise FinancingTermsError(
                f"Account {self.id.value} has no terms to price a period with",
            )

        if opening.currency is not self.currency:
            raise CurrencyMismatchError(
                f"A {opening.currency.value} balance cannot be priced on a "
                f"{self.currency.value} account",
            )

        return accrue_period(
            period=period,
            opening=opening,
            rate=terms.rate,
            charges=terms.charges,
            original_principal=(self.loan.principal if self.loan is not None else None),
        )

    def postings_for(self, accrued: AccruedPeriod) -> tuple[PostedAccrual, ...]:
        """The ledger rows that period becomes, each already pointed the right
        way for this side of net worth."""
        return postings(accrued, category=self.category)

    def _check_financing(self) -> None:
        if self.loan is not None and self.investment is not None:
            raise FinancingTermsError(
                "An account is a loan or an investment, never both",
            )

        if self.loan is not None and self.kind not in FINANCED_KINDS:
            raise FinancingTermsError(
                f"A {self.kind.value} account has no loan terms",
            )

        if self.investment is not None and self.kind is not AccountKind.INVESTMENT:
            raise FinancingTermsError(
                f"A {self.kind.value} account has no investment terms",
            )

    def _check_terms_currency(
        self,
        charges: Sequence[RecurringCharge],
        amounts: Sequence[Money | None],
    ) -> None:
        """Every figure on the terms is in the account's own currency.

        Refused rather than converted, like everywhere else money meets money
        here: an exchange rate is a fact about a moment nobody recorded, and a
        mortgage priced in the wrong unit is a debt off by four thousand.
        """
        stated = [amount for amount in amounts if amount is not None]
        stated.extend(charge.amount for charge in charges if charge.amount is not None)
        stated.extend(charge.base for charge in charges if charge.base is not None)

        for amount in stated:
            if amount.currency is not self.currency:
                raise CurrencyMismatchError(
                    f"A figure in {amount.currency.value} cannot describe a "
                    f"{self.currency.value} account",
                )

    def _announce_financing(self) -> None:
        terms = self.financing
        rate = self.rate
        self.record_event(
            AccountFinancingSet(
                account_id=self.id,
                user_id=self.user_id,
                kind=self.kind,
                rate=None if rate is None else rate.effective_annual,
                statement_day=None if terms is None else terms.statement_day,
                accrued_through=self.accrued_through,
            ),
        )

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    def matches(self, fingerprint: AccountFingerprint) -> bool:
        return fingerprint in self.fingerprints

    def link_fingerprint(self, fingerprint: AccountFingerprint) -> None:
        """Teach the account to answer to another of its bank's names."""
        if fingerprint in self.fingerprints:
            return

        self.fingerprints.add(fingerprint)
        self.record_event(
            AccountFingerprintLinked(
                account_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
            ),
        )

    def unlink_fingerprint(self, fingerprint: AccountFingerprint) -> None:
        """Stop answering to one of its bank's names.

        The correction for a card declared on the wrong account, which until
        now could only be added. Idempotent for a key this account does not
        hold: the caller's intent is that it stop matching, and it already
        does not.

        The movements that arrived under the key are the caller's to release,
        and they are released *after* this — an account that still matched
        while its rows were being let go would adopt the next alert onto a
        balance nobody is going to replay.
        """
        if fingerprint not in self.fingerprints:
            return

        self.fingerprints.discard(fingerprint)
        self.record_event(
            AccountFingerprintUnlinked(
                account_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
            ),
        )

    def apply(self, movement: LedgerMovement) -> None:
        """Move the balance by one movement the ledger already accepted.

        Direction alone does not say which way the number goes: spending on a
        credit card *raises* what that account holds, because what it holds is
        debt. Direction crossed with category is the whole rule.
        """
        if self.is_closed:
            raise AccountClosedError(
                f"Account {self.id.value} is closed and takes no movements",
            )

        self.balance = self._moved(self.balance, movement)
        self.movements_applied += 1
        self.record_event(
            AccountBalanceChanged(
                account_id=self.id,
                user_id=self.user_id,
                movement_id=movement.movement_id,
                direction=movement.direction,
                amount=movement.amount,
                balance=self.balance,
            ),
        )

    def reverse(self, movement: LedgerMovement) -> None:
        """Take a movement back off the balance, exactly as far as it moved it.

        The inverse of `apply`, and the same rule read backwards: what
        spending did to a credit card is what erasing that spending has to
        undo, so the direction is crossed with the category here too rather
        than negated by a caller who would have to know the rule again.

        Allowed on a closed account, unlike `apply`. A closed account stops
        taking *new* movements; removing one that should never have been on it
        is a correction of what is already there, which is why `rebuild` and
        `restate_balance` are allowed on one as well. Refusing here would
        leave a wrong row on a closed account with nothing that could ever
        take it off.
        """
        self.balance = self._moved(self.balance, movement, backwards=True)
        # Floored rather than allowed below zero: the count is a running tally
        # like the balance, and a negative one would be reported to the owner
        # as a fact about their account instead of as the bug it is.
        self.movements_applied = max(0, self.movements_applied - 1)
        self.record_event(
            AccountBalanceReversed(
                account_id=self.id,
                user_id=self.user_id,
                movement_id=movement.movement_id,
                direction=movement.direction,
                amount=movement.amount,
                balance=self.balance,
            ),
        )

    # TODO: some banks state the resulting balance in the alert itself. Decide
    # whether that number reconciles the running total (and how to tell a
    # stale alert from a current one, given they arrive out of order) or is
    # only kept for reference. Unknown which local banks do this — left open
    # deliberately rather than guessed at.

    def balance_after(
        self,
        movements: Iterable[LedgerMovement],
        *,
        starting: Balance | None = None,
    ) -> Balance:
        """What this account would hold having applied exactly these movements.

        Pure: it answers a question without becoming the answer. Two callers
        need it and neither should own a second copy of the rule — `rebuild`
        repairs the running total with it, and reading history replays the
        ledger up to a past instant with it. Because the opening balance is
        solved backwards whenever somebody restates what an account holds,
        `opening_balance` plus every movement up to an instant *is* the
        balance at that instant; there is nothing to snapshot.

        `starting` resumes from a balance already reached, so a caller walking
        a run of instants folds each movement once instead of replaying the
        whole ledger per instant. Left out, it starts where the account did.
        """
        balance = self.opening_balance if starting is None else starting
        for movement in movements:
            balance = self._moved(balance, movement)
        return balance

    def rebuild(self, movements: Iterable[LedgerMovement]) -> None:
        """Recompute the balance from the ledger, oldest movement first.

        The repair path for a running total that drifted. It is also what
        keeps the incremental balance honest: if replaying the ledger does not
        reproduce it, the number was wrong and now it is not.
        """
        # Replayed into locals, not onto the account: a movement in the wrong
        # currency must abort the repair, not leave half of one behind. A
        # replay is not new money moving either, so it records one event at
        # the end rather than one per movement — and a closed account can
        # still be repaired.
        replayed = list(movements)
        balance = self.balance_after(replayed)

        self.balance = balance
        self.movements_applied = len(replayed)
        self.record_event(
            AccountBalanceRebuilt(
                account_id=self.id,
                user_id=self.user_id,
                balance=self.balance,
                movements_applied=self.movements_applied,
            ),
        )

    def restate_balance(
        self,
        stated: Balance,
        movements: Iterable[LedgerMovement],
    ) -> None:
        """Set the balance to what its owner says it holds, and solve the
        opening balance backwards so the ledger still adds up to it.

        Takes today's number, not the starting one, because today's is the
        one somebody can actually check: their bank shows it, while what the
        account held before its first alert arrived is usually unknowable by
        the time Finflow sees any of this. The opening balance is derived
        from it rather than asked for.

        This does not touch a single movement. The ledger stays the
        authority — every row still counts exactly once, and an alert that
        arrives afterwards moves this balance the same way it always would.
        Allowed on a closed account for the same reason `rebuild` is: a
        correction of what was already there is not new money moving.
        """
        if stated.currency is not self.currency:
            raise CurrencyMismatchError(
                f"A {stated.currency.value} balance cannot restate a "
                f"{self.currency.value} account",
            )

        # Summed into locals, like a replay: a movement in a currency this
        # account does not hold must abort the restatement rather than leave
        # a balance behind that no opening balance explains.
        moved = Balance.zero(self.currency)
        applied = 0

        for movement in movements:
            moved = self._moved(moved, movement)
            applied += 1

        self.opening_balance = Balance.from_signed(
            stated.signed_amount - moved.signed_amount,
            self.currency,
        )
        self.balance = stated
        self.movements_applied = applied
        self.record_event(
            AccountBalanceRestated(
                account_id=self.id,
                user_id=self.user_id,
                opening_balance=self.opening_balance,
                balance=self.balance,
                movements_applied=self.movements_applied,
            ),
        )

    def rename(self, name: str) -> None:
        self.name = _valid_name(name)
        self.record_event(
            AccountRenamed(
                account_id=self.id,
                user_id=self.user_id,
                name=self.name,
            ),
        )

    def close(self, closed_at: PosixTime) -> None:
        """Stop taking movements, keeping the history and the balance.

        Not a delete: a closed account still explains past transactions, and
        a paid-off loan closing at zero is exactly what should be visible.
        """
        if self.is_closed:
            return

        self.closed_at = closed_at
        self.record_event(
            AccountClosed(account_id=self.id, user_id=self.user_id),
        )

    def reopen(self) -> None:
        """Take movements again, after a closure that turned out to be wrong.

        Nothing about the history changes: the movements recorded while it was
        closed — a correction, a replay — are still there, and the balance
        still explains them. What comes back is only the ability to take new
        ones, and to be adopted by an alert again.
        """
        if not self.is_closed:
            return

        self.closed_at = None
        self.record_event(
            AccountReopened(account_id=self.id, user_id=self.user_id),
        )

    def _moved(
        self,
        balance: Balance,
        movement: LedgerMovement,
        *,
        backwards: bool = False,
    ) -> Balance:
        """Where the balance lands after one movement. The rule, alone.

        `backwards` undoes it instead, so erasing a movement cannot end up
        using a second copy of the rule that drifts from this one.
        """
        grows = (
            movement.direction is MovementDirection.INCOMING
            if self.category is AccountCategory.ASSET
            else movement.direction is MovementDirection.OUTGOING
        )

        if backwards:
            grows = not grows

        return (
            balance.plus(movement.amount) if grows else balance.minus(movement.amount)
        )

    def _announce_opening(self) -> None:
        self.record_event(
            AccountOpened(
                account_id=self.id,
                user_id=self.user_id,
                name=self.name,
                kind=self.kind,
                category=self.category,
                currency=self.currency,
                opening_balance=self.opening_balance,
                bank=self.bank,
            ),
        )


@dataclass(slots=True)
class Transaction(AggregateRoot[MovementId]):
    """One movement of money, as Financial understands it.

    Two things produce one. A **bank alert**, whose identity is derived from
    its own content, so the same alert read twice is the same transaction —
    that is what lets the ledger reject a redelivery with a plain conditional
    insert. And a **manual entry**, for the money that never emails: an
    automatic payment, cash, a transfer the bank stayed quiet about. A manual
    entry gets a random identity, because two identical ones are two entries
    somebody meant to record.

    A transaction that matches no account is **unassigned**, not discarded
    and not guessed at. That is the ordinary state for anyone using Finflow
    only to watch what comes in and goes out: with no accounts declared,
    every movement is unassigned and the record is still complete.

    Editing keeps `stated`, what the bank originally said. The identity never
    moves with it — it is derived from the bank's own words, so a redelivery
    of an alert somebody has since corrected still lands on the same row
    instead of becoming a second expense.
    """

    user_id: UserId
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
    counterparty: str
    bank: str
    origin: TransactionOrigin = TransactionOrigin.BANK_ALERT
    account_fingerprint: AccountFingerprint | None = None
    account_id: AccountId | None = None
    # What the bank stated, kept from the first edit onwards. Absent while
    # nothing has been corrected, and on anything entered by hand — there is
    # no earlier version of a claim its author just made.
    stated: StatedMovement | None = None
    note: str | None = None
    # Set on both rows of a transfer between two of the owner's own accounts,
    # and on nothing else. Present means this movement is not spending and not
    # income: it is one half of money that never left.
    transfer: TransferLeg | None = None

    @classmethod
    def from_alert(
        cls,
        *,
        user_id: UserId,
        bank: str,
        direction: MovementDirection,
        amount: Money,
        occurred_at: PosixTime,
        counterparty: str,
        instrument_kind: str | None = None,
        last_four: str | None = None,
    ) -> Self:
        """Build the movement one bank alert describes.

        Takes what the boundary already read into Financial's vocabulary. An
        instrument this cannot use costs the movement its routing, never the
        movement itself: it is still recorded, unassigned, where the
        alternative is losing somebody's money because a bank abbreviated a
        card.
        """
        institution = bank.strip().lower()

        if not institution:
            raise ValueError("A transaction requires a bank")

        fingerprint = MovementFingerprint.from_movement(
            user_id=user_id,
            bank=institution,
            direction=direction,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=counterparty,
            instrument_kind=instrument_kind,
            last_four=last_four,
        )
        transaction = cls(
            id=MovementId.from_fingerprint(fingerprint),
            user_id=user_id,
            direction=direction,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=counterparty.strip(),
            bank=institution,
            origin=TransactionOrigin.BANK_ALERT,
            account_fingerprint=_account_fingerprint(
                bank=institution,
                instrument_kind=instrument_kind,
                last_four=last_four,
            ),
        )
        transaction._announce()

        return transaction

    @classmethod
    def as_transfer(
        cls,
        *,
        user_id: UserId,
        bank: str,
        amount: Money,
        occurred_at: PosixTime,
        source_instrument_kind: str,
        source_last_four: str,
        destination_instrument_kind: str,
        destination_last_four: str,
        origin: TransactionOrigin = TransactionOrigin.BANK_ALERT,
    ) -> tuple[Self, Self]:
        """Both sides of money that moved inside one person's own finances.

        Returned as a pair, and built here rather than by a caller assembling
        two movements, because the pairing is the rule: each side has to know
        the other's identity, both have to carry the same transfer id, and the
        directions have to be opposites. A caller free to build them
        separately is a caller free to build two outgoing sides, which is a
        card payment that empties an account and never pays the card.

        Each side keeps its own identity, derived from its own content exactly
        like any alert, so a redelivery rewrites the same two rows instead of
        adding a second pair — and so a side whose account is not declared yet
        can be adopted on its own, later, by the account that claims it.

        Nothing here decides what a balance does with a side. `Account.apply`
        already knows: money arriving on a liability is debt going down.
        """
        institution = bank.strip().lower()

        if not institution:
            raise ValueError("A transfer requires a bank")

        if (
            source_instrument_kind.strip().lower(),
            source_last_four.strip(),
        ) == (
            destination_instrument_kind.strip().lower(),
            destination_last_four.strip(),
        ):
            raise ValueError(
                "A transfer moves money between two different instruments",
            )

        transfer_id = TransferId.from_parts(
            user_id=user_id,
            bank=institution,
            amount=amount,
            occurred_at=occurred_at,
            source_instrument_kind=source_instrument_kind,
            source_last_four=source_last_four,
            destination_instrument_kind=destination_instrument_kind,
            destination_last_four=destination_last_four,
        )
        # Each side's counterparty is the other side's instrument, which is
        # what makes the two fingerprints differ even for a transfer between
        # two instruments of the same kind.
        out_counterparty = transfer_counterparty(
            instrument_kind=destination_instrument_kind,
            last_four=destination_last_four,
        )
        in_counterparty = transfer_counterparty(
            instrument_kind=source_instrument_kind,
            last_four=source_last_four,
        )
        out_id = MovementId.from_fingerprint(
            MovementFingerprint.from_movement(
                user_id=user_id,
                bank=institution,
                direction=MovementDirection.OUTGOING,
                amount=amount,
                occurred_at=occurred_at,
                counterparty=out_counterparty,
                instrument_kind=source_instrument_kind,
                last_four=source_last_four,
            ),
        )
        in_id = MovementId.from_fingerprint(
            MovementFingerprint.from_movement(
                user_id=user_id,
                bank=institution,
                direction=MovementDirection.INCOMING,
                amount=amount,
                occurred_at=occurred_at,
                counterparty=in_counterparty,
                instrument_kind=destination_instrument_kind,
                last_four=destination_last_four,
            ),
        )

        source = cls(
            id=out_id,
            user_id=user_id,
            direction=MovementDirection.OUTGOING,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=out_counterparty,
            bank=institution,
            origin=origin,
            account_fingerprint=_account_fingerprint(
                bank=institution,
                instrument_kind=source_instrument_kind,
                last_four=source_last_four,
            ),
            transfer=TransferLeg(
                transfer_id=transfer_id,
                role=TransferRole.SOURCE,
                counterpart_id=in_id,
                counterpart_instrument_kind=destination_instrument_kind,
                counterpart_last_four=destination_last_four,
            ),
        )
        destination = cls(
            id=in_id,
            user_id=user_id,
            direction=MovementDirection.INCOMING,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=in_counterparty,
            bank=institution,
            origin=origin,
            account_fingerprint=_account_fingerprint(
                bank=institution,
                instrument_kind=destination_instrument_kind,
                last_four=destination_last_four,
            ),
            transfer=TransferLeg(
                transfer_id=transfer_id,
                role=TransferRole.DESTINATION,
                counterpart_id=out_id,
                counterpart_instrument_kind=source_instrument_kind,
                counterpart_last_four=source_last_four,
            ),
        )
        source._announce()
        destination._announce()

        return source, destination

    @classmethod
    def enter_manually(
        cls,
        *,
        user_id: UserId,
        direction: MovementDirection,
        amount: Money,
        occurred_at: PosixTime,
        counterparty: str,
        account_id: AccountId | None = None,
        bank: str = "",
        note: str | None = None,
    ) -> Self:
        """Record money the user says moved, that no alert announced.

        This is the path for an automatic payment the bank never emails, for
        cash, for anything the parser could not be expected to see. The
        account is optional: somebody watching only what comes in and goes out
        has no accounts at all, and the movement is worth recording anyway.
        """
        transaction = cls(
            id=MovementId.new(),
            user_id=user_id,
            direction=direction,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=_valid_counterparty(counterparty),
            bank=bank.strip().lower(),
            origin=TransactionOrigin.MANUAL,
            account_id=account_id,
            note=note,
        )
        transaction._announce()

        return transaction

    @classmethod
    def enter_transfer_leg(
        cls,
        *,
        user_id: UserId,
        role: TransferRole,
        amount: Money,
        occurred_at: PosixTime,
        counterparty: str,
        account_id: AccountId,
        bank: str = "",
        note: str | None = None,
    ) -> Self:
        """The owner's side of money they moved between their own balances.

        Paying a credit card from an account at the same bank arrives as one
        alert naming both instruments, and `as_transfer` writes the pair. Paid
        from another bank, from a wallet or in cash, only one side of it is
        ever knowable here — and recorded as an ordinary manual entry that
        side is *wrong*, not merely incomplete: money arriving on a card would
        be counted as income, money leaving an account as an expense, and the
        month would report a payment nobody spent and nobody earned.

        So it is entered as a transfer leg whose counterpart is external. It
        moves its account's balance exactly like any movement — a card's debt
        falls because `Account.apply` reads an incoming movement on a
        liability that way, with nothing here to teach it — and it stays out
        of every total because `is_transfer` answers True.

        `role` decides the direction rather than the caller: the source of a
        transfer is money leaving and its destination is money arriving,
        always. A caller free to pair `SOURCE` with an incoming movement is a
        caller free to record a card payment that *raises* what is owed.

        The account is required, unlike a plain manual entry. A leg names no
        instrument, so no account can ever adopt it by matching the way an
        unassigned alert is adopted; entered without one it would be a row
        claiming a balance moved while no balance moved, which is the
        discrepancy this whole path exists to prevent.

        Its identity comes from its content, unlike a plain manual entry and
        like every alert: two coffees of one price are two purchases, but two
        identical payments to one card in one minute are a double submit, and
        counted twice the debt falls twice. See
        `MovementFingerprint.from_transfer_leg`.
        """
        fingerprint = MovementFingerprint.from_transfer_leg(
            user_id=user_id,
            account_id=account_id,
            role=role,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=_valid_counterparty(counterparty),
        )
        transaction = cls(
            id=MovementId.from_fingerprint(fingerprint),
            user_id=user_id,
            direction=(
                MovementDirection.OUTGOING
                if role is TransferRole.SOURCE
                else MovementDirection.INCOMING
            ),
            amount=amount,
            occurred_at=occurred_at,
            counterparty=_valid_counterparty(counterparty),
            bank=bank.strip().lower(),
            origin=TransactionOrigin.MANUAL,
            account_id=account_id,
            note=note,
            # No counterpart at all rather than a placeholder one: the other
            # side is not a movement this ledger can be asked for, and a
            # fabricated id would be a link every reader follows into nothing.
            transfer=TransferLeg(
                transfer_id=TransferId.from_lone_leg(fingerprint),
                role=role,
            ),
        )
        transaction._announce()

        return transaction

    @classmethod
    def accrue(
        cls,
        *,
        user_id: UserId,
        account_id: AccountId,
        item: str,
        label: str,
        direction: MovementDirection,
        amount: Money,
        occurred_at: PosixTime,
        period_end: dt.date,
        bank: str = "",
        note: str | None = None,
    ) -> Self:
        """One charge this app computed, as a row somebody can read.

        A month of interest on a mortgage, the insurance that month carried,
        what a CDT earned, the gap between what a fund was worth and what it
        is worth now. None of it was announced by anybody and none of it was
        typed — but all of it moved a balance, and a balance is the running
        total of its rows. A charge that moved money without leaving a row is
        the one thing that makes a balance unexplainable, so this exists
        rather than a quiet adjustment to the number.

        Its identity comes from the account and the period, never the amount.
        A period is charged once whatever the arithmetic later says it came
        to, so a second run writes the key the ledger already holds and is
        refused there — which is what makes running the accrual on a schedule,
        twice, or after a crash, all the same thing.

        The account is required. Everything here is a fact about one balance;
        without one there is nothing for the charge to be a charge on.
        """
        transaction = cls(
            id=MovementId.from_fingerprint(
                MovementFingerprint.from_accrual(
                    user_id=user_id,
                    account_id=account_id,
                    item=item,
                    period_end=period_end,
                ),
            ),
            user_id=user_id,
            direction=direction,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=_valid_counterparty(label),
            bank=bank.strip().lower(),
            origin=TransactionOrigin.ACCRUAL,
            account_id=account_id,
            note=note,
        )
        transaction._announce()

        return transaction

    @classmethod
    def confirm_scheduled(
        cls,
        *,
        user_id: UserId,
        bill_id: uuid.UUID,
        period: dt.date,
        direction: MovementDirection,
        amount: Money,
        occurred_at: PosixTime,
        counterparty: str,
        account_id: AccountId | None = None,
        note: str | None = None,
    ) -> Self:
        """One charge of a declared bill, the moment its owner confirms it.

        The gym is domiciled, so the bank stopped emailing about it and the
        charge exists nowhere until somebody says it happened. This is them
        saying so — and from here on it is an ordinary movement: it moves a
        balance, it counts as spending, it carries a merchant, and it is
        announced to whoever asked to be told.

        Its own origin rather than `MANUAL`, which it otherwise is. Two later
        pieces of this feature need to tell them apart, and neither could
        afterwards: a detector reading the history must not propose declaring
        a bill whose own charges it is looking at, and a charge posted
        automatically has to be distinguishable from one somebody typed.

        Its identity comes from the bill and the period, never the amount or
        the day the money moved — `MovementFingerprint.from_schedule` says
        why. So confirming September twice is refused by the ledger's own
        conditional write rather than by a check somebody has to remember to
        keep.

        The account is optional, unlike an accrual's: a bill may be paid in
        cash, and `ScheduledBill` lets it name no account at all. Unassigned,
        the charge is still a charge — visible, counted in what went out, and
        placeable later like any other movement.
        """
        transaction = cls(
            id=MovementId.from_fingerprint(
                MovementFingerprint.from_schedule(
                    user_id=user_id,
                    bill_id=bill_id,
                    period=period,
                ),
            ),
            user_id=user_id,
            direction=direction,
            amount=amount,
            occurred_at=occurred_at,
            counterparty=_valid_counterparty(counterparty),
            bank="",
            origin=TransactionOrigin.SCHEDULED,
            account_id=account_id,
            note=note,
        )
        transaction._announce()

        return transaction

    @property
    def status(self) -> TransactionStatus:
        """Derived, never stored: the link and the state cannot disagree."""
        return (
            TransactionStatus.UNASSIGNED
            if self.account_id is None
            else TransactionStatus.ASSIGNED
        )

    @property
    def is_transfer(self) -> bool:
        """One half of money that moved between the owner's own accounts.

        The one question a spending total has to ask: a transfer is neither an
        expense nor income, and counting either side would report money
        somebody never spent and never earned.
        """
        return self.transfer is not None

    @property
    def has_counterpart_movement(self) -> bool:
        """Whether the other side of this transfer is a row in this ledger.

        False both for ordinary spending and for a leg paid from outside the
        app. What it gates is correction: two rows stating one movement of
        money cannot be edited apart, while a lone leg has nothing to disagree
        with.
        """
        return self.transfer is not None and not self.transfer.counterpart_is_external

    @property
    def is_routable(self) -> bool:
        """Whether an account could ever claim this movement by matching.

        False means the alert named no instrument worth matching, so it waits
        for somebody to place it by hand rather than for an account to be
        declared.
        """
        return self.account_fingerprint is not None

    @property
    def needs_assignment(self) -> bool:
        return self.account_id is None

    def edit(
        self,
        *,
        amount: Money | None = None,
        occurred_at: PosixTime | None = None,
        counterparty: str | None = None,
        note: str | None = None,
    ) -> None:
        """Correct what this movement says, keeping what the bank said.

        A parser reads a merchant wrong, a date lands in the wrong month, an
        amount is off. Correcting it must not lose the original: the bank's
        own words are the only way to tell later whether the balance or the
        alert was wrong. Whatever is left as None is left alone, and an empty
        note clears the one that is there.

        Never touches identity. The id came from the bank's statement, so a
        redelivery of a corrected alert still writes the same row rather than
        arriving as a second expense.
        """
        replacement_amount = self.amount if amount is None else amount
        replacement_time = self.occurred_at if occurred_at is None else occurred_at
        replacement_party = (
            self.counterparty
            if counterparty is None
            else _valid_counterparty(counterparty)
        )

        if self.has_counterpart_movement and (
            replacement_amount != self.amount
            or replacement_time != self.occurred_at
            or replacement_party != self.counterparty
        ):
            # Refused rather than applied to both sides: this aggregate holds
            # one of them, and moving the other's balance from here would be a
            # write nothing in this transaction boundary can guarantee. What a
            # wrong transfer needs is to be re-read, not half-corrected.
            #
            # A leg whose counterpart is external is not refused, and the
            # difference is the whole reason it is asked about here rather
            # than `is_transfer`: there is no second row to fall out of step
            # with, so correcting a mistyped amount touches one balance and
            # leaves nothing inconsistent behind it.
            raise TransferLegError(
                "One side of a transfer cannot be corrected on its own: the "
                "two sides state one movement of money",
            )
        # A note is an annotation, not a claim about the movement, so it is
        # tracked apart: writing one must not fabricate a "the bank said
        # something different" record whose values match the live ones.
        corrects_the_statement = (
            replacement_amount != self.amount
            or replacement_time != self.occurred_at
            or replacement_party != self.counterparty
        )
        replacement_note = self.note if note is None else (note.strip() or None)

        if not corrects_the_statement and replacement_note == self.note:
            return

        if (
            corrects_the_statement
            and self.stated is None
            and self.origin is TransactionOrigin.BANK_ALERT
        ):
            # Captured once, on the first correction: later edits correct a
            # correction, and what matters is still what the bank stated.
            self.stated = StatedMovement(
                amount=self.amount,
                occurred_at=self.occurred_at,
                counterparty=self.counterparty,
            )

        self.amount = replacement_amount
        self.occurred_at = replacement_time
        self.counterparty = replacement_party
        self.note = replacement_note

        self.record_event(
            TransactionEdited(
                movement_id=self.id,
                user_id=self.user_id,
                amount=self.amount,
                movement_occurred_at=self.occurred_at,
                counterparty=self.counterparty,
            ),
        )

    def assign_to(self, account_id: AccountId) -> None:
        """Put this movement on an account.

        Idempotent for the account it already sits on — a redelivery must not
        record the same assignment twice — and refused for any other, because
        moving it means first taking the amount back off the balance that
        holds it. `unassign` is that step.
        """
        if self.account_id == account_id:
            return

        if self.account_id is not None:
            raise TransactionAlreadyAssignedError(
                f"Movement {self.id.value} already belongs to account "
                f"{self.account_id.value}",
            )

        self.account_id = account_id
        self.record_event(
            TransactionAssigned(
                movement_id=self.id,
                user_id=self.user_id,
                account_id=account_id,
            ),
        )

    def detach(self) -> None:
        """Take this movement off its account and leave it off.

        Refused on a transfer leg nothing could ever put back. A leg asserts
        that a balance moved; detached, it moves none, and it names no
        instrument for an account to claim it by — so unlike an alert waiting
        to be adopted, it would sit there permanently saying a payment
        happened while no balance shows it. Moving it to another account is
        still allowed, and is what a leg on the wrong one actually needs.
        """
        if self.is_transfer and not self.is_routable:
            raise TransferLegError(
                "A transfer leg cannot be left without an account: it states "
                "that a balance moved, and no account would ever adopt it",
            )

        self.unassign()

    def unassign(self) -> None:
        """Take this movement off the account holding it.

        The caller has to reverse the amount on that balance in the same
        write. Nothing here can do it: the aggregate does not know the
        balance, which is exactly what keeps a balance from moving without a
        ledger row behind it.

        Not the same thing as `detach`: this is also the first half of moving
        a movement between two accounts, which stays allowed for everything.
        """
        if self.account_id is None:
            return

        previous = self.account_id
        self.account_id = None
        self.record_event(
            TransactionUnassigned(
                movement_id=self.id,
                user_id=self.user_id,
                account_id=previous,
            ),
        )

    @property
    def declaration_refusal(self) -> DeclarationRefusal | None:
        """Why this movement cannot be declared a transfer, or None if it can.

        What the aggregate alone can answer. Whether a bill counts it as its
        charge is the bill's business, asked by the use case.
        """
        if self.transfer is not None:
            return DeclarationRefusal.ALREADY_TRANSFER

        if self.origin.is_self_written:
            return DeclarationRefusal.SELF_WRITTEN

        if self.account_id is None and not self.is_routable:
            return DeclarationRefusal.UNPLACEABLE

        return None

    def declare_transfer(self) -> None:
        """Say this movement was money moved to, or from, a balance of the
        owner's that this app does not hold.

        The lone case: a card paid at a bank nobody declared here. Its balance
        does not move — it already did when the movement was recorded — but
        it stops counting as spending or income, which is the whole of what
        was wrong with it.
        """
        role = TransferRole.of(self.direction)
        self._reclassify(
            TransferLeg(
                transfer_id=TransferId.declared(
                    source=self.id if role is TransferRole.SOURCE else None,
                    destination=self.id if role is TransferRole.DESTINATION else None,
                ),
                role=role,
                basis=TransferBasis.RECLASSIFIED,
            ),
        )

    def pair_with(self, other: Transaction) -> None:
        """Say this movement and `other` are the two sides of one transfer.

        Paying a Lulo account from Bancolombia, with both banks emailing:
        one movement left, another arrived, and until now the first counted
        as spending and the second as income. Neither balance moves here —
        both already did — so the only question is whether the two really are
        one fact, and the checks are the ones that fact implies: opposite
        directions, the same amount to the cent, two different accounts.

        Refused as a whole: both sides change or neither does.
        """
        if other.id == self.id:
            raise TransferDeclarationError("A movement cannot be its own other side")

        if other.user_id != self.user_id:
            raise TransferDeclarationError("Both sides of a transfer are one owner's")

        for movement in (self, other):
            if (refusal := movement.declaration_refusal) is not None:
                raise TransferDeclarationError(refusal.reason)

        if other.direction is self.direction:
            raise TransferDeclarationError(
                "Both movements go the same way, so neither is the other's side",
            )

        if other.amount != self.amount:
            # To the cent and in the same currency. A fee between the two is
            # real spending, and pairing them would make it vanish; the owner
            # corrects the amount first if the difference is a misreading.
            raise TransferDeclarationError(
                "The two movements are for different amounts",
            )

        if self.account_id is not None and self.account_id == other.account_id:
            raise TransferDeclarationError(
                "Both movements are on the same account, so no money changed sides",
            )

        source, destination = (
            (self, other)
            if self.direction is MovementDirection.OUTGOING
            else (other, self)
        )
        transfer_id = TransferId.declared(source=source.id, destination=destination.id)

        for movement, counterpart in ((source, destination), (destination, source)):
            movement._reclassify(
                TransferLeg(
                    transfer_id=transfer_id,
                    role=TransferRole.of(movement.direction),
                    counterpart_id=counterpart.id,
                    basis=TransferBasis.RECLASSIFIED,
                ),
            )

    def declare_counterpart(
        self,
        *,
        account_id: AccountId,
        counterparty: str,
        bank: str = "",
    ) -> Transaction:
        """Say this movement was paid into, or out of, `account_id` — and
        write that side, which no alert ever will.

        The case that started this: Bancolombia emails "Pagaste $X a BANCO
        COMERCIAL AV VILLAS", AV Villas emails a receipt nobody can read as a
        movement, and the card's debt never falls. The row returned is that
        fall — an incoming movement on the card, the same amount at the same
        moment — and this movement stops counting as spending.

        The written side moves its account's balance like any movement, and
        the caller stores it with that balance in one write. Its identity
        comes from this movement alone (see
        `MovementFingerprint.from_declared_counterpart`), so declaring twice
        is one row.

        `counterparty` is how the written side names this one — the name of
        the account the money came from, typically. It is only ever read by a
        person: nothing matches on it.
        """
        if (refusal := self.declaration_refusal) is not None:
            raise TransferDeclarationError(refusal.reason)

        if self.account_id == account_id:
            raise TransferDeclarationError(
                "The other side of a transfer is a different account",
            )

        counterpart_id = MovementId.from_fingerprint(
            MovementFingerprint.from_declared_counterpart(
                user_id=self.user_id,
                movement_id=self.id,
            ),
        )
        role = TransferRole.of(self.direction)
        source, destination = (
            (self.id, counterpart_id)
            if role is TransferRole.SOURCE
            else (counterpart_id, self.id)
        )
        transfer_id = TransferId.declared(source=source, destination=destination)
        written = Transaction(
            id=counterpart_id,
            user_id=self.user_id,
            direction=(
                MovementDirection.INCOMING
                if self.direction is MovementDirection.OUTGOING
                else MovementDirection.OUTGOING
            ),
            amount=self.amount,
            occurred_at=self.occurred_at,
            counterparty=_valid_counterparty(counterparty),
            bank=bank.strip().lower(),
            # MANUAL rather than an origin of its own. It is the owner's word,
            # which is exactly what MANUAL means, and every subscriber of
            # `MovementRecorded` already reads it — a new member would be
            # refused as malformed by any that had not learned it yet.
            origin=TransactionOrigin.MANUAL,
            account_id=account_id,
            transfer=TransferLeg(
                transfer_id=transfer_id,
                role=(
                    TransferRole.DESTINATION
                    if role is TransferRole.SOURCE
                    else TransferRole.SOURCE
                ),
                counterpart_id=self.id,
                basis=TransferBasis.COUNTERPART,
            ),
        )
        self._reclassify(
            TransferLeg(
                transfer_id=transfer_id,
                role=role,
                counterpart_id=counterpart_id,
                basis=TransferBasis.RECLASSIFIED,
            ),
        )
        written._announce()

        return written

    def undo_declaration(self) -> None:
        """Put back what this movement was before its owner called it a
        transfer: spending or income again, on the same balance it never
        left.

        Only a reclassified movement can be put back. One the bank stated as
        a transfer has no earlier self to return to, and a written
        counterpart is not restored but erased — the caller does that, with
        its balance, in the same write.
        """
        leg = self.transfer

        if leg is None:
            raise TransferDeclarationError("This movement is not a transfer")

        if leg.basis is not TransferBasis.RECLASSIFIED:
            raise TransferDeclarationError(
                "Only a movement its owner declared a transfer can be put back",
            )

        self.transfer = None
        self.record_event(
            TransactionReclassified(
                movement_id=self.id,
                user_id=self.user_id,
                transfer=False,
            ),
        )

    def _reclassify(self, leg: TransferLeg) -> None:
        if (refusal := self.declaration_refusal) is not None:
            raise TransferDeclarationError(refusal.reason)

        self.transfer = leg
        self.record_event(
            TransactionReclassified(
                movement_id=self.id,
                user_id=self.user_id,
                transfer=True,
            ),
        )

    def erase(self) -> None:
        """Record that this movement is leaving the ledger for good.

        The aggregate is not mutated, because there is nothing left to mutate:
        the row is about to stop existing. What this does is make the removal
        a fact somebody can read later, which no other event covers — a
        balance falling by two thousand with no row behind it is unexplainable
        otherwise.

        The caller has to take the amount back off the balance in the same
        breath, exactly as `unassign` requires: nothing here knows the
        balance, which is what keeps a balance from moving without the ledger
        agreeing.

        No refusal here for a leg whose counterpart is another row, unlike
        `edit`. What makes erasing one side safe is that the other side goes
        in the same write, and whether that row is still there is not a
        question one aggregate can answer — so the rule lives where both rows
        are held, in the use case that removes them.
        """
        self.record_event(
            TransactionErased(
                movement_id=self.id,
                user_id=self.user_id,
                direction=self.direction,
                amount=self.amount,
                account_id=self.account_id,
            ),
        )

    def as_movement(self) -> LedgerMovement:
        """The ledger row `Account.apply` moves a balance by."""
        return LedgerMovement(
            movement_id=self.id,
            direction=self.direction,
            amount=self.amount,
            occurred_at=self.occurred_at,
        )

    def _announce(self) -> None:
        self.record_event(
            TransactionRecorded(
                movement_id=self.id,
                user_id=self.user_id,
                direction=self.direction,
                amount=self.amount,
                movement_occurred_at=self.occurred_at,
                counterparty=self.counterparty,
                bank=self.bank,
                origin=self.origin,
                account_fingerprint=self.account_fingerprint,
            ),
        )


def _account_fingerprint(
    *,
    bank: str,
    instrument_kind: str | None,
    last_four: str | None,
) -> AccountFingerprint | None:
    """The key an account would have to answer to for this movement.

    None when the alert named no instrument, or named one without digits, or
    carried digits no account key can use. All three mean the same thing: no
    account can claim this movement by matching, so it waits for a person.

    An instrument spelled in a vocabulary this context has not enumerated
    still gets a key: what decides routing is whether an account was declared
    for it, not whether the word is familiar.
    """
    if instrument_kind is None or last_four is None:
        return None

    try:
        return AccountFingerprint.from_alert(
            bank=bank,
            instrument_kind=instrument_kind,
            last_four=last_four,
        )
    except ValueError:
        # Ingestion gates digits on bare `str.isdigit`, so an alert can carry
        # numerals no account key can use.
        return None


def _valid_counterparty(value: str) -> str:
    stripped = value.strip()

    if not stripped:
        raise ValueError("A transaction requires a counterparty")

    return stripped


def _valid_name(name: str) -> str:
    stripped = name.strip()

    if not stripped:
        raise ValueError("An account needs a name")

    if len(stripped) > MAX_ACCOUNT_NAME_LENGTH:
        raise ValueError(
            f"Account name exceeds {MAX_ACCOUNT_NAME_LENGTH} characters",
        )

    return stripped
