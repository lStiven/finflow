from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Self

from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    AccountBalanceRebuilt,
    AccountClosed,
    AccountFingerprintLinked,
    AccountOpened,
    AccountRenamed,
    TransactionAssigned,
    TransactionEdited,
    TransactionRecorded,
    TransactionUnassigned,
)
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
    TransactionAlreadyAssignedError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    InstrumentKind,
    LedgerMovement,
    MovementDirection,
    MovementFingerprint,
    MovementId,
    StatedMovement,
    TransactionOrigin,
    TransactionStatus,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


MAX_ACCOUNT_NAME_LENGTH = 120


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

    def __post_init__(self) -> None:
        self.name = _valid_name(self.name)
        self._check_credit_limit(self.credit_limit)

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

    # TODO: some banks state the resulting balance in the alert itself. Decide
    # whether that number reconciles the running total (and how to tell a
    # stale alert from a current one, given they arrive out of order) or is
    # only kept for reference. Unknown which local banks do this — left open
    # deliberately rather than guessed at.

    def rebuild(self, movements: Iterable[LedgerMovement]) -> None:
        """Recompute the balance from the ledger, oldest movement first.

        The repair path for a running total that drifted. It is also what
        keeps the incremental balance honest: if replaying the ledger does not
        reproduce it, the number was wrong and now it is not.
        """
        balance = self.opening_balance
        applied = 0

        # Replayed into locals, not onto the account: a movement in the wrong
        # currency must abort the repair, not leave half of one behind. A
        # replay is not new money moving either, so it records one event at
        # the end rather than one per movement — and a closed account can
        # still be repaired.
        for movement in movements:
            balance = self._moved(balance, movement)
            applied += 1

        self.balance = balance
        self.movements_applied = applied
        self.record_event(
            AccountBalanceRebuilt(
                account_id=self.id,
                user_id=self.user_id,
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

    def _moved(self, balance: Balance, movement: LedgerMovement) -> Balance:
        """Where the balance lands after one movement. The rule, alone."""
        grows = (
            movement.direction is MovementDirection.INCOMING
            if self.category is AccountCategory.ASSET
            else movement.direction is MovementDirection.OUTGOING
        )

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

    @property
    def status(self) -> TransactionStatus:
        """Derived, never stored: the link and the state cannot disagree."""
        return (
            TransactionStatus.UNASSIGNED
            if self.account_id is None
            else TransactionStatus.ASSIGNED
        )

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

    def unassign(self) -> None:
        """Take this movement off the account holding it.

        The caller has to reverse the amount on that balance in the same
        write. Nothing here can do it: the aggregate does not know the
        balance, which is exactly what keeps a balance from moving without a
        ledger row behind it.
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
