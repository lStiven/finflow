from __future__ import annotations

from collections.abc import Iterable
import dataclasses
import datetime as dt
from decimal import Decimal
import enum
import hashlib
import re
from typing import Self
import unicodedata
import uuid

from personal_finance.contexts.financial.domain.exceptions import (
    CurrencyMismatchError,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    JsonValue,
    Money,
    PosixTime,
    UserId,
    ValueObject,
)


class AccountCategory(enum.Enum):
    """Which side of net worth an account sits on.

    Net worth is the sum of assets minus the sum of liabilities, so this is
    what decides a balance's meaning: 1.2M on a savings account is money you
    have, the same 1.2M on a credit card is money you owe.
    """

    ASSET = "asset"
    LIABILITY = "liability"


class AccountKind(enum.Enum):
    """What kind of account this is.

    Explicit string values: the kind is persisted, so reordering the members
    must not rewrite anybody's data.
    """

    SAVINGS = "savings"
    CHECKING = "checking"
    CASH = "cash"
    INVESTMENT = "investment"
    CREDIT_CARD = "credit_card"
    LOAN = "loan"
    MORTGAGE = "mortgage"

    @property
    def category(self) -> AccountCategory:
        """Derived, never chosen: nobody gets to declare a mortgage an asset."""
        return (
            AccountCategory.LIABILITY
            if self in _LIABILITY_KINDS
            else AccountCategory.ASSET
        )

    @property
    def informational(self) -> bool:
        """Whether this account is **watched rather than counted**.

        Finflow answers one question: where the money somebody spends goes. A
        loan and a mortgage are outside it, and deliberately so. Their owner
        already knows what they owe — the bank tells them every month, at the
        figure the bank considers authoritative — and the payment that services
        them is money leaving a real account, where it is already recorded as
        it leaves. Counting the debt in net worth would put a number nobody is
        managing here in front of every other figure; counting its interest as
        spending would count the same cuota twice, once where it left and once
        where it landed.

        So these accounts keep a balance, a schedule and a history of their
        own, and take part in no total. `category` is a separate question and
        still answers `LIABILITY`: what decides which way a debt moves when a
        payment arrives is not what decides whether the debt is summed.
        """
        return self in _INFORMATIONAL_KINDS


_LIABILITY_KINDS = frozenset(
    {AccountKind.CREDIT_CARD, AccountKind.LOAN, AccountKind.MORTGAGE},
)

# A credit card is **not** here, and the difference is what the account is
# for. A card is how money is spent — every purchase on it is a purchase
# Finflow exists to show — and what it owes is that spending, unpaid. A
# mortgage is a commitment somebody already has, serviced from an account this
# app is watching anyway.
_INFORMATIONAL_KINDS = frozenset({AccountKind.LOAN, AccountKind.MORTGAGE})


class InstrumentKind(enum.Enum):
    """The sort of card or account a movement arrived through.

    Financial's own vocabulary, mapped at the boundary from whatever the
    publishing context calls its instruments — the same arrangement as
    `MovementDirection`. Neither context gets to change the other's meaning by
    editing its own.

    Not the same thing as `AccountKind`, and the difference is the whole point
    of this type existing. `AccountKind` is what the owner calls the account;
    this is what the *bank* calls the thing the money moved through, and only
    these words ever appear in an alert. A savings account is declared
    `SAVINGS` and its transfers arrive as `ACCOUNT` — spelling the fingerprint
    with the account kind produces a key no alert can ever match, and the
    owner finds out only by noticing that nothing was ever assigned.
    """

    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    SAVINGS_ACCOUNT = "savings_account"
    CHECKING_ACCOUNT = "checking_account"
    ACCOUNT = "account"


@dataclasses.dataclass(frozen=True, slots=True)
class AccountId(ValueObject):
    value: uuid.UUID

    @classmethod
    def new(cls) -> Self:
        return cls(value=uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> Self:
        try:
            return cls(value=uuid.UUID(value))
        except ValueError as error:
            raise ValueError(f"Invalid account id: {value!r}") from error

    def to_dict(self) -> JsonValue:
        return str(self.value)


def normalize_last_four(value: str) -> str:
    """The trailing digits of a card or account, as everything must spell them.

    Trailing, not verbatim: a template parser prints what its bank printed and
    the LLM fallback may hand back more of the number, so the same card would
    otherwise fingerprint two ways and split one balance into two. ASCII
    digits only — `str.isdigit` alone accepts `²` and Arabic-Indic numerals,
    and this text comes from an untrusted email.
    """
    digits = value.strip()

    if not digits or not digits.isascii() or not digits.isdigit():
        raise ValueError(f"Last four must be digits: {value!r}")

    return digits[-4:]


def _canonical(parts: Iterable[str]) -> str:
    """Join parts so no field's content can imitate the separator.

    Length-prefixed rather than merely delimited: `bank` and `counterparty`
    come out of an untrusted email, and a crafted separator inside either one
    would otherwise let two different records canonicalize to the same string
    — on a key that routes money to an account, or decides whether a movement
    is new, that means somebody's money landing on the wrong balance or never
    landing at all.
    """
    return "".join(f"{len(part)}:{part}|" for part in parts)


@dataclasses.dataclass(frozen=True, slots=True)
class AccountFingerprint(ValueObject):
    """How an incoming movement finds the account it belongs to.

    Built from what a bank alert exposes — the institution, the sort of
    instrument, and its last four digits. The last four are required: without
    them "a savings account at Bancolombia" would match every savings account
    the user holds there, and two real accounts would silently merge into one
    wrong balance. A movement whose instrument has no last four stays
    unassigned instead.

    Built through `from_parts` so every caller normalizes the same way; the
    constructor stays open for reading a stored value back.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Account fingerprint cannot be empty")

        # Stripped, not merely checked: this is a stored key, and a value that
        # picks up padding on a round-trip would stop matching its own row.
        object.__setattr__(self, "value", value)

    @classmethod
    def from_parts(
        cls,
        *,
        bank: str,
        instrument_kind: InstrumentKind,
        last_four: str,
    ) -> Self:
        """The key an owner declares an account under.

        Takes the enum, not a string, and that is the whole difference from
        `from_alert`. A key spelled with a word no alert ever produces is not
        an error anybody sees: it is an account that silently matches nothing,
        forever. Declaring is the side where the vocabulary can be closed,
        because it is a person choosing from a list.
        """
        return cls._build(
            bank=bank,
            instrument=instrument_kind.value,
            last_four=last_four,
        )

    @classmethod
    def from_alert(
        cls,
        *,
        bank: str,
        instrument_kind: str,
        last_four: str,
    ) -> Self:
        """The key a movement waits under, in the bank's own words.

        Free text on purpose, and not narrowed to `InstrumentKind`: a bank
        naming an instrument this context has never enumerated still describes
        a real account, and storing the key it gave is what lets that movement
        be adopted the day the word is added — retroactively, with no
        migration. Narrowing here would throw the routing information away at
        the only moment it exists.
        """
        return cls._build(
            bank=bank,
            instrument=instrument_kind.strip().lower(),
            last_four=last_four,
        )

    @classmethod
    def _build(cls, *, bank: str, instrument: str, last_four: str) -> Self:
        institution = bank.strip().lower()

        if not institution:
            raise ValueError("Account fingerprint requires a bank")

        if not instrument:
            raise ValueError("Account fingerprint requires an instrument kind")

        return cls(
            value=_canonical(
                (institution, instrument, normalize_last_four(last_four)),
            ),
        )

    def to_dict(self) -> JsonValue:
        return self.value


class BalanceSign(enum.Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


@dataclasses.dataclass(frozen=True, slots=True)
class Balance(ValueObject):
    """What an account holds, as an unsigned amount plus its sign.

    `Money` stays unsigned everywhere in this codebase — direction belongs to
    the movement, not to the quantity — but a balance still needs a sign of
    its own: an account discovered from a bank alert starts at zero with its
    real opening balance unknown, so the first purchase legitimately takes the
    running total below zero. That is a known-incomplete history, not an
    error, and refusing to represent it would mean inventing a number.

    On a liability the amount is what is owed: `POSITIVE` 1.2M on a credit
    card means a 1.2M debt, and `AccountCategory` is what makes it subtract.
    """

    amount: Money
    sign: BalanceSign = BalanceSign.POSITIVE

    def __post_init__(self) -> None:
        # Zero has no direction; without this, two equal balances could differ.
        if self.amount.amount == 0:
            object.__setattr__(self, "sign", BalanceSign.POSITIVE)

    @classmethod
    def zero(cls, currency: Currency) -> Self:
        return cls(amount=Money(amount=Decimal("0"), currency=currency))

    @classmethod
    def from_signed(cls, amount: Decimal, currency: Currency) -> Self:
        return cls(
            amount=Money(amount=abs(amount), currency=currency),
            sign=BalanceSign.NEGATIVE if amount < 0 else BalanceSign.POSITIVE,
        )

    @property
    def currency(self) -> Currency:
        return self.amount.currency

    @property
    def is_negative(self) -> bool:
        return self.sign is BalanceSign.NEGATIVE

    @property
    def signed_amount(self) -> Decimal:
        return -self.amount.amount if self.is_negative else self.amount.amount

    def plus(self, amount: Money) -> Self:
        return self._moved_by(amount, Decimal(1))

    def minus(self, amount: Money) -> Self:
        return self._moved_by(amount, Decimal(-1))

    def _moved_by(self, amount: Money, factor: Decimal) -> Self:
        if amount.currency is not self.currency:
            raise CurrencyMismatchError(
                f"Cannot move {amount.currency.value} on a "
                f"{self.currency.value} balance",
            )

        return type(self).from_signed(
            self.signed_amount + (amount.amount * factor),
            self.currency,
        )

    def to_dict(self) -> JsonValue:
        return {
            "amount": str(self.signed_amount),
            "currency": self.currency.value,
        }


class TransactionOrigin(enum.Enum):
    """Where a movement came from, which decides what may be trusted about it.

    A bank alert states a fact somebody else recorded; a manual entry is the
    user's own claim. Automatic payments that never email are exactly why the
    second exists.

    An **accrual** is neither: nobody typed it and no bank announced it. It is
    what this app computed from the terms the owner declared — a month of
    interest on a mortgage, the insurance that month carried, what a CDT
    earned. Its own origin because it is the one a reader has to be able to
    tell apart: an interest charge is real money leaving somebody's net worth,
    but it is the only movement in the ledger whose authority is an
    arithmetic rather than a fact, so a wrong rate is corrected by restating
    the terms rather than by arguing with the bank.

    A **scheduled** charge is a declared bill somebody confirmed: the owner
    said the gym would be charged on the 4th, and then said it was. Deliberately
    not `MANUAL`, which it otherwise resembles, because two later pieces of
    this feature need to tell them apart — the detector must not propose
    declaring a bill it is looking at the charges of, and an automatic charge
    has to be distinguishable from money its owner typed. And deliberately not
    `ACCRUAL`, which is excluded from alerts on purpose: a charge landing on a
    bill's due date is exactly the moment its owner wants to hear about it.
    """

    BANK_ALERT = "bank_alert"
    MANUAL = "manual"
    ACCRUAL = "accrual"
    SCHEDULED = "scheduled"


class TransactionStatus(enum.Enum):
    # No account answers to this movement's instrument, or the alert named
    # none it could use. Kept and visible, never guessed at.
    UNASSIGNED = "unassigned"
    # On an account, and counted in its balance.
    ASSIGNED = "assigned"


@dataclasses.dataclass(frozen=True, slots=True)
class MovementId(ValueObject):
    """Identity of one movement of money.

    Deliberately not the identity of the email that announced it: the same
    notification can be delivered twice, and two different notifications can
    describe the same purchase. What makes a movement the same movement is
    Financial's own business.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Movement id cannot be empty")

        object.__setattr__(self, "value", value)

    @classmethod
    def new(cls) -> Self:
        """Identity for a movement no bank announced.

        Random, unlike a movement read from an alert: two manual entries with
        the same amount, merchant and date are two entries, because somebody
        meant to record both. There is nothing to deduplicate against.
        """
        return cls(value=uuid.uuid4().hex)

    @classmethod
    def from_fingerprint(cls, fingerprint: MovementFingerprint) -> Self:
        """A movement a bank announced is identified by what it is.

        Deriving the id from the fingerprint is what keeps reprocessing safe
        all the way down to storage: the same alert read twice produces the
        same ledger row, so rejecting a duplicate is a conditional insert on
        that row's own key rather than a second bookkeeping row that has to
        stay in step with it.
        """
        return cls(value=fingerprint.value)

    def to_dict(self) -> JsonValue:
        return self.value


class MovementDirection(enum.Enum):
    """Which way the money went, in Financial's own vocabulary.

    Ingestion has an enum that happens to look identical today. Mapping
    between them at the boundary is the point: neither context gets to change
    the other's meaning by editing its own.
    """

    OUTGOING = "outgoing"
    INCOMING = "incoming"

    @classmethod
    def from_alert(cls, value: str) -> MovementDirection:
        """Read a direction out of an alert, or refuse the alert.

        The one field with no safe default. An unreadable instrument costs an
        unassigned movement somebody can still fix; a guessed direction moves
        a real balance the wrong way and looks entirely correct doing it.
        """
        try:
            return cls(value.strip().lower())
        except ValueError as error:
            raise ValueError(f"Unknown movement direction: {value!r}") from error


_NON_ALPHANUMERIC = re.compile(r"[\W_]+")

# What the fingerprint writes where an instrument would go when the alert
# named none. Empty on purpose: a present instrument is never empty once
# stripped, so no alert can spell this sentinel and pass itself off as
# instrument-less. Private, too — ingestion publishes its own `NO_INSTRUMENT`
# with a different value, and importing the wrong one would change every
# derived `MovementId`.
_ABSENT_INSTRUMENT = ""


def normalize_counterparty(value: str) -> str:
    """The other side of a movement, folded just enough to compare it.

    Conservative on purpose: case, accents, punctuation and repeated spaces
    are noise banks add inconsistently, and nothing beyond that is touched.
    Folding harder — dropping store numbers, say — would let two real
    purchases at two branches collapse into one, and a movement that never
    reaches the ledger is invisible, while a duplicate is a row a user can see
    and delete. Merchant does its own, deliberately more aggressive grouping;
    this one is Financial's and stays here rather than crossing the boundary.
    """
    stripped = value.strip()

    if not stripped:
        raise ValueError("Movement counterparty cannot be empty")

    # Case-folded last, after decomposing: NFKD turns compatibility characters
    # into ordinary cased letters (`№` -> `No`, `™` -> `TM`), and a fold done
    # first never sees them — leaving one merchant spelled two ways with two
    # fingerprints and a doubled expense. Folding last also still expands `ß`
    # into `ss`, because `\W` keeps it rather than dropping it as punctuation.
    decomposed = unicodedata.normalize("NFKD", stripped)
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    # `\W` keeps letters and digits in every script rather than ASCII alone: a
    # merchant named outside the Latin alphabet must normalize like any other,
    # not slip through unfolded.
    folded = _NON_ALPHANUMERIC.sub(" ", unaccented).strip().casefold()

    # A counterparty of pure punctuation (`***`) folds away to nothing. It is
    # still a real movement, so its raw text stands in — folded and collapsed
    # as far as it can be — instead of being refused or sharing one
    # fingerprint with every other such alert.
    return folded or " ".join(stripped.casefold().split())


def _canonical_amount(amount: Money) -> str:
    """`50000` and `50000.00` are the same money and must fingerprint alike.

    A template parser prints what its bank printed; the LLM fallback may add
    or drop trailing zeros. Trailing zeros are therefore stripped by hand
    rather than through `Decimal.normalize`, which rounds at the ambient
    context's precision: this key has to be byte-identical across processes
    and restarts, and a library that changed `getcontext()` would otherwise
    make one movement fingerprint two ways.
    """
    text = f"{amount.amount:f}"

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"


def _instrument_last_four(value: str | None) -> str:
    """The card digits a fingerprint uses, or the sentinel when there are none.

    Never raises, unlike `normalize_last_four`. Blank arrives from a JSON
    payload meaning "no digits", and ingestion gates `last_four` on bare
    `str.isdigit`, which accepts numerals this cannot use — an alert can
    therefore reach Financial carrying digits it must refuse. The movement is
    real either way: losing its instrument costs an unassigned movement
    somebody can still see and fix, while refusing the alert loses the money.
    """
    if value is None or not value.strip():
        return _ABSENT_INSTRUMENT

    try:
        return normalize_last_four(value)
    except ValueError:
        return _ABSENT_INSTRUMENT


@dataclasses.dataclass(frozen=True, slots=True)
class MovementFingerprint(ValueObject):
    """What makes two bank alerts the same movement of money.

    Deliberately not the email's `message_id`: a bank can announce one
    purchase in two messages, the same message can be parsed more than once,
    and SQS delivers at least once — under all three the money moved exactly
    once. This is the key the ledger writes conditionally, so it is the only
    thing standing between a redelivery and a doubled expense.

    The key is user, bank, instrument, direction, amount, time and
    counterparty. It resolves to the second, but **in practice to the
    minute**: both extraction paths stop there — the template parser reads
    `HH:MM` and the LLM prompt asks for `HH:MM`. So two identical charges on
    one card, at one merchant, for one amount, inside a single minute are
    indistinguishable from one charge announced twice, and this key calls them
    one movement. That is a knowingly accepted loss, not an oversight: the
    same data cannot say which it is, and idempotency under at-least-once
    delivery is the requirement that has to hold. Making that second charge
    visible belongs to the ledger, which can record what announced each row.

    A temporary authorization and its later posting are *not* handled here:
    they differ in time, and often in amount, so no key comparing fields can
    catch them. That is settlement, a decision `Transaction` makes with both
    records in hand, not something to approximate by loosening this.

    `kind` is left out on purpose. It is a classification, not an identity —
    one bank's QR payment is another's card purchase, and a re-parse that
    reclassifies a movement must not turn it into a second one.

    Hashed rather than readable so no amount, counterparty or card digits sit
    in plaintext wherever an id is logged — this value reaches ledger keys and
    `AccountBalanceChanged`. Obfuscation, not confidentiality: the inputs are
    enumerable, so anyone already holding the logs could confirm a guess. An
    HMAC would close that, at the price of a secret inside a domain value
    object whose loss would re-apply every movement ever stored.

    Built through `from_movement` so every caller normalizes the same way; the
    constructor stays open for reading a stored value back.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Movement fingerprint cannot be empty")

        object.__setattr__(self, "value", value)

    @classmethod
    def from_movement(
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
        institution = bank.strip().lower()

        if not institution:
            raise ValueError("Movement fingerprint requires a bank")

        instrument = (
            _ABSENT_INSTRUMENT
            if instrument_kind is None
            else instrument_kind.strip().lower()
        )
        digits = _instrument_last_four(last_four)

        canonical = _canonical(
            (
                # The user is part of the key, not a filter applied after it:
                # two people forwarding alerts for the same shared card must
                # never land on one another's ledger row.
                str(user_id.value),
                institution,
                instrument,
                digits,
                direction.value,
                _canonical_amount(amount),
                amount.currency.value,
                str(occurred_at.as_epoch_seconds()),
                normalize_counterparty(counterparty),
            ),
        )

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    @classmethod
    def from_accrual(
        cls,
        *,
        user_id: UserId,
        account_id: AccountId,
        item: str,
        period_end: dt.date,
    ) -> Self:
        """What makes two computed charges the same charge.

        The account, what was charged, and the period it closed — and
        deliberately **not the amount**. A month of interest is charged once
        per account per period whatever the arithmetic later says it came to,
        so a second run over a period already posted writes the same key and
        the ledger's conditional insert refuses it. Including the amount would
        mean a balance corrected after the fact posting the same month's
        interest twice, at two figures, with the debt carrying both.

        `item` is a stable key, not the words a reader sees: `interest`, or a
        charge's name folded. Renaming a charge on the terms therefore leaves
        every period already posted alone and only changes what the next one
        is filed under — which is why the terms refuse two charges sharing a
        name, since those would be one key and the second would never be
        written at all.
        """
        canonical = _canonical(
            (
                _ACCRUAL_TAG,
                str(user_id.value),
                str(account_id.value),
                item.strip().casefold(),
                period_end.isoformat(),
            ),
        )

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    @classmethod
    def from_schedule(
        cls,
        *,
        user_id: UserId,
        bill_id: uuid.UUID,
        period: dt.date,
    ) -> Self:
        """What makes two confirmations of one declared charge the same charge.

        The bill and the period it is the charge of — and, exactly as in
        `from_accrual`, **not the amount**. The gym raises its price and the
        owner confirms 130 000 where the bill still says 120 000; that is the
        same September charge, corrected, not a second one. Including the
        amount would make confirming twice at two figures write two rows and
        take the money twice.

        Not the date either, for the same reason and a sharper one: the money
        moves on the 6th when the 4th is a Saturday, so a key that included
        when it moved would let one charge be confirmed once per day it could
        plausibly have landed on. `period` is the occurrence's own day on the
        calendar — its identity — and the day the money actually moved is
        recorded on the row rather than in its key.

        The **bill** stands where the account would. A bill may name no
        account at all, and two bills charged from one account on one day are
        two charges; keying on the account would collapse them into one and
        lose the second silently.

        Tagged, so this can never canonicalize to what `from_accrual` or
        `from_movement` produce. Changing the tag re-identifies every charge
        ever confirmed, and every one of them would become confirmable a
        second time.
        """
        canonical = _canonical(
            (
                _SCHEDULED_BILL_TAG,
                str(user_id.value),
                str(bill_id),
                period.isoformat(),
            ),
        )

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    @classmethod
    def from_transfer_leg(
        cls,
        *,
        user_id: UserId,
        account_id: AccountId,
        role: TransferRole,
        amount: Money,
        occurred_at: PosixTime,
        counterparty: str,
    ) -> Self:
        """What makes one hand-entered transfer leg the same leg.

        Derived rather than random, unlike an ordinary manual entry, and the
        difference is what the amount means. Two coffees of the same price on
        the same day are two purchases and both belong in a ledger; two
        identical payments to one card in the same minute are a double submit,
        and taken as two the debt falls twice — a wrong balance nobody sees,
        which is exactly what the alert-derived path already refuses to
        produce. So this path gets the same protection by the same mechanism:
        the id comes from the content, and the ledger's conditional write
        rejects the second attempt on its own key.

        The **account** stands where an alert's instrument would, and has to:
        a leg names no card, so without it two cards paid from one wallet for
        one amount on one day would collapse into a single row.

        Tagged, so this can never canonicalize to what `from_movement`
        produces. `bank` is deliberately absent — it is a label here, not part
        of which balance moved, and including it would make the same payment
        typed with and without one into two rows.
        """
        canonical = _canonical(
            (
                _TRANSFER_LEG_TAG,
                str(user_id.value),
                str(account_id.value),
                role.value,
                _canonical_amount(amount),
                amount.currency.value,
                str(occurred_at.as_epoch_seconds()),
                normalize_counterparty(counterparty),
            ),
        )

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def to_dict(self) -> JsonValue:
        return self.value


# Prefixed into the canonical form above so a leg and an alert can never hash
# alike. Changing it re-identifies every leg entered after it, and the same
# payment would then be enterable a second time.
_TRANSFER_LEG_TAG = "transfer-leg"

# The same contract for a computed charge: changing this re-identifies every
# accrual, and every period already posted would be posted a second time.
_ACCRUAL_TAG = "accrual"

# And for a declared bill's charge. Same contract again: change it and every
# charge already confirmed becomes confirmable a second time, at which point
# the money leaves twice.
_SCHEDULED_BILL_TAG = "scheduled-bill"


class TransferRole(enum.Enum):
    """Which side of a transfer one movement is.

    `SOURCE` is where the money left, `DESTINATION` where it arrived. On a
    card payment the destination is the card, and money "arriving" on a
    liability is its debt falling — the same rule `Account.apply` already
    uses, unchanged.
    """

    SOURCE = "source"
    DESTINATION = "destination"


@dataclasses.dataclass(frozen=True, slots=True)
class TransferId(ValueObject):
    """What ties the two sides of one transfer together.

    Derived from the transfer itself rather than generated, for the same
    reason `MovementId` is: the same alert read twice has to produce the same
    pair, or a redelivery would write a second transfer whose sides duplicate
    the first one's. Every part that identifies the movement goes in — who,
    which bank, how much, when, and both instruments — so two card payments
    of the same amount on the same day from different accounts stay distinct.

    Hashed like the movement fingerprint, and for the same reason: this value
    is stored on both rows and reaches logs, and card digits should not sit
    there in plaintext.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Transfer id cannot be empty")

        object.__setattr__(self, "value", value)

    @classmethod
    def from_lone_leg(cls, fingerprint: MovementFingerprint) -> Self:
        """Identity for a transfer only one side of which this app holds.

        Derived from that side, because it *is* the transfer: there is no
        second row to tie to this one, and a random id would make the same
        payment entered twice produce two transfers whose single legs claim
        the same money. Hashed again rather than reused verbatim so a transfer
        and a movement never share an id, and tagged so this can never equal
        what `from_parts` builds for a pair.
        """
        canonical = _canonical((_LONE_TRANSFER_TAG, fingerprint.value))

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    @classmethod
    def from_parts(
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
    ) -> Self:
        canonical = _canonical(
            (
                str(user_id.value),
                bank.strip().lower(),
                _canonical_amount(amount),
                amount.currency.value,
                str(occurred_at.as_epoch_seconds()),
                source_instrument_kind.strip().lower(),
                _instrument_last_four(source_last_four),
                destination_instrument_kind.strip().lower(),
                _instrument_last_four(destination_last_four),
            ),
        )

        return cls(value=hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def to_dict(self) -> JsonValue:
        return self.value


# Same contract as `_TRANSFER_LEG_TAG`: changing it re-identifies transfers.
_LONE_TRANSFER_TAG = "lone-transfer"


def transfer_counterparty(*, instrument_kind: str, last_four: str) -> str:
    """How one side of a transfer names the other.

    A transfer has no counterparty in the ordinary sense — the other side is
    another of the owner's own accounts — but every movement needs one, and
    this text is part of the movement fingerprint. So it is built from the
    instrument rather than from anything a bank wrote: **changing this format
    changes the identity of every transfer leg recorded after it**, and the
    same email would then be written a second time. Clients render the
    structured `TransferLeg` instead of parsing this.
    """
    return f"{instrument_kind.strip().lower()} *{last_four.strip()}"


@dataclasses.dataclass(frozen=True, slots=True)
class TransferLeg(ValueObject):
    """What one movement knows about being half of a transfer.

    Carried on the row rather than looked up, so reading a movement never
    needs a second query to find out that it is not spending — which is the
    one thing a summary must know about it.

    The other side is not always here. Paying a card from an account at the
    same bank produces both legs from one alert, but paying it from another
    bank, from a wallet or in cash produces a movement whose counterpart this
    app never sees — and that movement is still not income. So the three
    fields describing the other side are optional, and **optional together**:
    a leg either names a movement in this ledger or names nothing at all.
    Half a description is the state this class exists to make impossible,
    because a leg holding an id nothing can resolve is worse than one that
    says plainly that the other side is elsewhere.
    """

    transfer_id: TransferId
    role: TransferRole
    # The other side's movement id. Both sides of an alert-derived transfer
    # are built together and both ids come from content, so this is known
    # without asking storage. None when the other side is outside this app —
    # see `counterpart_is_external`.
    counterpart_id: MovementId | None = None
    counterpart_instrument_kind: str | None = None
    counterpart_last_four: str | None = None

    def __post_init__(self) -> None:
        described = (
            self.counterpart_id,
            self.counterpart_instrument_kind,
            self.counterpart_last_four,
        )

        if any(part is not None for part in described) and not all(
            part is not None for part in described
        ):
            raise ValueError(
                "A transfer leg describes the other side fully or not at all",
            )

        if (
            self.counterpart_instrument_kind is not None
            and not self.counterpart_instrument_kind.strip()
        ):
            raise ValueError("A transfer leg names the other side's instrument")

    @property
    def counterpart_is_external(self) -> bool:
        """Whether the other side of this transfer is outside this app.

        True for a card paid from a bank the user has not declared, from a
        wallet, or in cash. It changes nothing about what this leg does to a
        balance and nothing about it being kept out of spending totals — only
        whether there is a second row to point a reader at.
        """
        return self.counterpart_id is None


@dataclasses.dataclass(frozen=True, slots=True)
class StatedMovement(ValueObject):
    """What the bank said, before anybody corrected it.

    Kept so a corrected movement can still be told apart from one the parser
    read right the first time. Without it, a balance that looks wrong gives no
    way to know whether the alert or the correction was the mistake.
    """

    amount: Money
    occurred_at: PosixTime
    counterparty: str


@dataclasses.dataclass(frozen=True, slots=True)
class LedgerMovement(ValueObject):
    """One accepted movement of money, ready to hit an account.

    Carries its own identity so the balance change can be tied back to the
    ledger row that caused it — the balance is a running total of these, and
    a total nobody can retrace is a total nobody can repair.
    """

    movement_id: MovementId
    direction: MovementDirection
    amount: Money
    occurred_at: PosixTime
