from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
from decimal import Decimal
from typing import Protocol

from personal_finance.contexts.financial.domain.bills import BillId, ScheduledBill
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantAttribution:
    """Who a movement was with, as Merchant already decided it.

    Plain strings, deliberately. The id and the category are another
    context's vocabulary: Financial groups by them and hands them on, and
    never reads meaning into either — importing Merchant's enum would give
    that context a veto over renaming its own members.
    """

    merchant_id: str
    display_name: str
    category: str
    # Whether Merchant still wants somebody to look at this grouping, so a
    # movement can say "attributed, but nobody has confirmed it".
    needs_review: bool


class UnknownMerchantCategoryError(Exception):
    """Raised when a movement names a category its owner does not have.

    Financial's own word for it. Which categories exist is Merchant's answer,
    but a refusal has to cross the port as something this context already
    knows about, or the protocol would be handing every caller a dependency on
    another context's exceptions.
    """


class MerchantDirectory(Protocol):
    """Reads the counterparty text on a movement back as a merchant.

    Financial stores what the bank wrote, because that is the fact it was
    given and it must survive somebody regrouping their merchants later. The
    join to a canonical merchant is therefore made when the answer is read,
    which is also what makes a correction retroactive for free: renaming a
    merchant or moving a spelling changes every past movement's attribution
    at once, with nothing to re-process.
    """

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        """Keyed by the exact counterparty text handed in.

        Absent from the mapping means no merchant owns that spelling yet —
        an ordinary answer while the sighting is still on merchant's queue,
        and a permanent one for a movement entered by hand under a name
        nothing else has ever seen.

        Must not raise for a counterparty it cannot resolve: an attribution
        is an enrichment, and a movement with none is still a movement.
        """
        ...

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        """Every category value this user's movements can come back with.

        Financial reads none of them; it only needs the vocabulary to refuse a
        `category` filter that names nothing. Without it a typo answers 200
        with an empty page, which on a money screen is indistinguishable from
        "you spent nothing here".

        Per user because half of that vocabulary is theirs: the categories the
        app ships are the same for everybody, and the ones somebody wrote for
        themselves are not.
        """
        ...

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        """File this counterparty text under this category, and say who it is.

        `occurred_at` is the movement's own time, not the moment of the call:
        a merchant this creates is first seen when the spending happened, so
        a purchase entered a month late does not read as a merchant discovered
        today.

        The answer to a movement entered by hand having no merchant at all.
        Nothing in the automatic path ever learns a name except from a bank
        email, so a purchase somebody typed in themselves stayed outside every
        breakdown by category however many times they entered it.

        Financial asks; Merchant decides what that means — whether a merchant
        is created, or an existing one refiled. None comes back when the text
        could never be a merchant in the first place, which is not a failure:
        the movement is recorded either way.

        Raises `UnknownMerchantCategoryError` when the category is not one of
        this user's.
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BalanceReversal:
    """How far one account's running total moves when movements are erased.

    Signed, and computed by `Account` before it is handed here, for the same
    reason `record`'s `balance_delta` is: what erasing an outgoing movement
    does to a credit card is not what it does to a savings account, and that
    rule belongs to the aggregate.

    `movements` is how many rows this account loses, so the tally beside the
    balance moves with it instead of drifting one count at a time.
    """

    account_id: AccountId
    delta: Decimal
    movements: int


class AccountRepository(Protocol):
    """Persistence port for `Account`.

    Every method takes the owner. `AccountFingerprint` carries no user of its
    own — unlike `MovementFingerprint`, which does — so two people at one bank
    whose cards end in the same four digits produce the same fingerprint. The
    scoping is what keeps their money apart, and an implementation that could
    answer without knowing whose data it is asked for would be one query away
    from putting one person's spending on another's balance.
    """

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        """Load one account, or None when this user has no such account."""
        ...

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        """Resolve a bank/instrument pair to the account that answers to it.

        The hot path: every parsed alert asks this, so an implementation must
        not scan. One account answers to several fingerprints — a checking
        account emails as a debit card for purchases and as an account number
        for transfers.
        """
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        """Every account this user owns, for their own list and net worth."""
        ...

    def save(self, account: Account) -> None:
        """Store what the owner changed: name, closure, fingerprints.

        Must not write the balance. That number is moved by the ledger's
        atomic add, and writing back a value read moments earlier would
        discard any movement that landed in between — leaving a ledger row on
        record whose effect vanished, with nothing to trigger a repair. A
        rename must not be able to lose an expense.
        """
        ...

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        """Store an account that stopped answering to one of its keys.

        Its own method rather than a `save`, because a fingerprint is stored
        twice: on the account, and as the entry `find_by_fingerprint` reads.
        `save` only ever adds the second one — it cannot know which key went
        missing — so a plain save would leave the entry behind, still routing
        every new alert to an account that no longer claims it.

        Both halves have to land together for the same reason. Left with the
        entry alone, the account would take movements it does not match; left
        with the account alone, the key would be free for another account to
        claim while this one still refuses it.
        """
        ...

    def overwrite_balance(self, account: Account) -> None:
        """Store a balance that was recomputed from the ledger.

        The repair path, and the only writer of that number besides the
        ledger's own atomic add.
        """
        ...

    def restate_balance(self, account: Account) -> None:
        """Store a balance its owner stated, with the opening balance behind it.

        One write, not `save` followed by `overwrite_balance`: those own
        different halves of the same sum, and a crash between them would
        leave an opening balance that does not explain the balance beside
        it, with no replay scheduled to notice.
        """
        ...

    def add(self, account: Account) -> bool:
        """Create an account and the fingerprints it answers to, or lose.

        Returns False when something already holds that identity — two
        workers can be opening the same account from the same redelivered
        alert at the same moment, and only one of them may win. The loser
        re-reads and uses the account that won rather than creating a second
        one holding half the movements.
        """
        ...


class TransactionLedger(Protocol):
    """Persistence port for `Transaction`, and the balance it moves.

    The ledger is the authority; a balance is a running total of what is
    written here. That is why this is one method and not two: the row and the
    balance change it causes have to land together, or a balance moves with
    nothing behind it to explain or repair it.
    """

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        """Write the ledger row and its balance change in one atomic write.

        Returns False when the row already existed, which is how a redelivery
        is recognised: the movement's identity is derived from its content, so
        the same alert read twice writes the same key. Nothing is applied on
        that path — not the row, not the balance.

        `balance_delta` is how far the account's running total moves, signed:
        what an outgoing movement does to a credit card is not what it does to
        a savings account, and that rule belongs to `Account`, which has
        already applied it by the time this is called. It must be None exactly
        when the transaction is unassigned, and set otherwise.
        """
        ...

    def save(self, transaction: Transaction) -> None:
        """Replace a movement that already exists.

        For corrections and for moving a movement between accounts. Never
        moves a balance by itself — `record` owns the atomic write that does,
        and a balance touched here would be one with no row behind it.
        """
        ...

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        """Erase these rows and unwind the balances they moved, in one write.

        The mirror of `record`, and one method for the same reason: a balance
        may not move without the ledger agreeing in the same instant. Split in
        two, an erasure that failed between them would leave an account
        carrying a movement that is no longer there, with nothing left to
        replay and no way for the caller to retry — the row it would need is
        already gone.

        A sequence of movements because the two sides of a transfer state a
        single fact: erasing one and failing on the other would leave a row
        claiming money moved to a movement that no longer exists. They belong
        to the same user, so an implementation can hold them in one write.

        `reversals` names one entry per account losing rows, never an account
        that is not there any more — an erasure must not be refused because
        the account a movement used to sit on has since gone.
        """
        ...

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        """Load one movement, for reading a balance back to its rows."""
        ...

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        """Every movement waiting for the account that answers to this key.

        What makes declaring an account retroactive: the alerts that arrived
        before it existed are still here, and they belong to it.
        """
        ...

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        """Every movement on one account, oldest first.

        What `Account.rebuild` replays when a running total is suspected of
        having drifted from the rows behind it.
        """
        ...

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        """Movements no account answered for, for the user to place by hand."""
        ...

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        """Everything this user has, assigned or not.

        The whole point for somebody who declared no accounts: what came in
        and what went out is a complete answer on its own.
        """
        ...


class ScheduledBillRepository(Protocol):
    """Persistence port for `ScheduledBill`.

    Every method takes the owner, for the reason `AccountRepository` gives:
    an implementation that could answer without knowing whose data it is
    asked for would be one query away from showing somebody another person's
    commitments.

    There is no `find_by_*` beyond the id. A bill is read by its owner from a
    list of a handful of rows — nobody has two hundred subscriptions — so the
    listing is the hot path and there is nothing here to index.
    """

    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        """Load one bill, or None when this user has no such bill."""
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[ScheduledBill]:
        """Every bill this user declared, paused ones included.

        Paused ones too: they predict nothing, but they are still the record
        of what a cancelled subscription used to cost, and a list that hid
        them would offer no way to bring one back.
        """
        ...

    def save(self, bill: ScheduledBill) -> None:
        """Store a bill, new or amended.

        A plain put, unlike `AccountRepository.save`: nothing else writes
        these rows and none of their fields is a running total, so there is
        no half of the record that a write could quietly discard.
        """
        ...

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        """Forget a bill. False when there was nothing to forget.

        Deleting is right here, where closing an account would be wrong: a
        closed account still explains movements that are in the ledger, and a
        deleted bill explains nothing, because it never wrote anything. What
        it *did* write, once confirming exists, are ordinary movements that
        stand on their own.
        """
        ...


class ChargeLookup(Protocol):
    """Which of a handful of named movements the ledger already holds.

    Narrow like `AccountLookup`, and for the same reason: reading a bill's
    charges is a question about what exists, and a use case that had the whole
    `TransactionLedger` to ask it with would be one typo away from recording
    or erasing money it has no business touching. `DynamoDBTransactionLedger`
    satisfies both, so nothing extra is wired up.

    This is the whole of how "paid" is answered. A confirmed charge's id comes
    from its bill and its period, so the row *is* the record — there is no
    second copy anywhere, and none to fall out of step when somebody deletes
    the movement.
    """

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        """The movements among these ids that exist, keyed by id.

        Absent from the mapping means no such row, which is the ordinary
        answer for a charge nobody has confirmed. One call rather than one per
        id: a month of bills is tens of ids, and asking for them one at a time
        would put a round trip per charge behind a screen somebody opens to
        read two numbers.
        """
        ...


class AccountLookup(Protocol):
    """The two questions bills ask about accounts, and nothing more.

    Deliberately narrower than `AccountRepository`, which `DynamoDBAccount\
Repository` satisfies anyway. Declaring the wide one here would let a use case
    that has no business moving money reach `restate_balance` and
    `overwrite_balance` — and would make every test of it build a fake that
    can rewrite balances in order to ask whether an account exists.
    """

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        """Whether this id names an account of this user's, for a bill to
        point at."""
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        """Every account, so a listing can tell which bills are frozen without
        asking once per bill."""
        ...
