"""Whether a movement the ledger already holds is a declared charge.

The safety net under the automatic charge, and the answer to a question the
manual half never had to ask. While somebody confirms each charge themselves,
a duplicate is visible: they typed "paid" on the 6th and the bank's email for
the same money is right there. **In automatic nobody is looking.** The clock
says the gym was due on the 4th, and if the bank also announced it the app
would take the money twice — the very drift this feature exists to remove,
with the sign flipped and this app as its author.

So before anything is written, the window is read: if a movement in the
ledger looks like this charge, the charge is **answered by that movement** and
nothing new is written at all.

Nothing here writes and nothing here decides on its own. It ranks; the caller
decides, and the rule it applies is deliberately timid — **only an unambiguous
match links itself**. One candidate that is the right merchant for about the
right money is the gym. Two candidates are a question, and a question that
moves money is a question for a person.

The two qualities are not degrees of confidence in the same thing, they are
different evidence:

* `CERTAIN` — the merchant matches *and* the amount is what the bill says,
  give or take — a tenth, or half for a bill declared as varying. This is
  what may be linked without asking.
* `LIKELY` — one of the two. The merchant matches but the figure moved a lot
  (the electricity bill), or the figure is exactly right but nothing has ever
  attributed that spelling to the bill's name (the ordinary case for a bill
  somebody typed: "Gimnasio" against `PAGO PSE GYMSA`). Worth showing,
  never worth writing.

What is excluded is as important as what matches. This app's **own** rows —
an accrual, a charge another bill already wrote — can never be the evidence
that a charge happened: they would make the feature confirm itself. Neither
can a transfer between the owner's own accounts, which is not spending at all,
nor a movement already answering for some other charge, since one payment
settles one thing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
import enum

from personal_finance.contexts.financial.domain.bills import (
    MATCH_WINDOW_DAYS,
    BillStatus,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    counterparty_key,
)
from personal_finance.shared.domain.value_objects import Money, ValueObject


# How far the figure may sit from what the bill projects and still be "about
# right". A tenth: enough for the yearly price rise and the rounding a bank
# does on a foreign charge, and not enough for two different charges at one
# merchant to be taken for each other.
AMOUNT_TOLERANCE = Decimal("0.10")

# The same question for a bill its owner said varies: half the average either
# way, so 60 000 to 180 000 on a 120 000 electricity bill. Wide enough for a
# hot month, and still a bound — twice the figure to the same utility is a
# debt being settled, not this month — though an arrears payment under one
# and a half times it does get through, and is undone by unlinking it.
VARIABLE_AMOUNT_TOLERANCE = Decimal("0.50")

# How many candidates one charge offers. A charge with more plausible answers
# than this is not a list to choose from, it is a merchant somebody buys from
# all the time — and the choice belongs on the movement, not here.
MAX_CANDIDATES = 4


class MatchQuality(enum.Enum):
    """How much this movement looks like the charge.

    Explicit strings: it crosses the API to a screen that renders the two
    differently, so reordering the members must not change what a client
    reads.
    """

    #: Right merchant, right money. Linkable without asking, when it is alone.
    CERTAIN = "certain"
    #: One of the two. Shown to its owner, never acted on.
    LIKELY = "likely"


@dataclasses.dataclass(frozen=True, slots=True)
class ChargeCandidate(ValueObject):
    """A movement that could be answering for one charge.

    Carries what a person needs to recognise it — who it was with, how much,
    and when — because the answer to "is this the gym?" is not in an id.
    """

    movement_id: str
    counterparty: str
    amount: Money
    occurred_on: dt.date
    quality: MatchQuality


#: The category Merchant files a spelling under when it knows nothing; it says
#: nothing about who the merchant is, so it never rules a candidate out.
UNCATEGORIZED = "uncategorized"


@dataclasses.dataclass(frozen=True, slots=True)
class KnownMerchant(ValueObject):
    """Who a movement's counterparty is, as much as matching needs.

    Plain strings, Merchant's own vocabulary carried through: Financial
    compares them and never reads meaning into them. `confirmed` is the owner
    having reviewed it — a guess Merchant made on its own is not.
    """

    merchant_id: str
    category: str | None
    confirmed: bool


def bill_keys(bill: ScheduledBill, *merchant_ids: str | None) -> frozenset[str]:
    """Every counterparty key this bill could be recognised under.

    The folded name always, for a movement somebody typed with the bill's own
    words. And every merchant the bill is known through — the one its owner
    named (while it still exists), which is how "Luz casa mamá" is recognised
    in `COMP ELECTRICIDAD TU`; and the one that owns the bill's name, which is
    how "Gimnasio" is recognised in `GYMSA*BOG`. Added, never one in place of
    the other: naming a merchant must not cost a spelling the name already
    had.
    """
    keys = {counterparty_key(bill.name, None)}
    keys.update(
        counterparty_key(bill.name, merchant_id)
        for merchant_id in merchant_ids
        if merchant_id is not None
    )

    return frozenset(keys)


def candidates_for(
    bill: ScheduledBill,
    *,
    period: dt.date,
    movements: Sequence[Transaction],
    keys: frozenset[str],
    merchant_ids: Mapping[str, str],
    zone: dt.tzinfo,
    taken: frozenset[str] = frozenset(),
    known: Mapping[str, KnownMerchant] | None = None,
    named_merchant: str | None = None,
) -> tuple[ChargeCandidate, ...]:
    """The movements that could be this charge, best first.

    `known` describes each counterparty's merchant, when Merchant has one, and
    `named_merchant` is the merchant the bill names (while it exists). With
    them, a movement that only shares a figure with the bill is left out when
    it is plainly someone else's — see `_someone_else`.

    `keys` is what `bill_keys` answered for this bill and `merchant_ids` maps
    a counterparty spelling to the merchant that owns it — both handed in,
    because resolving a merchant is a question for another context and a
    domain function that could ask one would be holding a connection.

    `taken` is the movements already answering for some charge, this bill's or
    another's. Without it one month's charge could settle three of them.

    Empty for a paused bill and for a period this bill is not charged on: both
    are charges that do not exist, and a match against one would be an answer
    to nothing.
    """
    if bill.status is BillStatus.PAUSED or not bill.occurs_on(period):
        return ()

    found = [
        candidate
        for movement in movements
        if (
            candidate := _candidate(
                movement,
                bill=bill,
                period=period,
                keys=keys,
                merchant_ids=merchant_ids,
                zone=zone,
                taken=taken,
                known=known or {},
                named_merchant=named_merchant,
            )
        )
        is not None
    ]
    found.sort(
        key=lambda each: (
            # Certain before likely, then the closest figure, then the closest
            # day. The order a person would read them in, and the order that
            # decides which one a screen offers first.
            each.quality is not MatchQuality.CERTAIN,
            abs(each.amount.amount - bill.amount.amount),
            abs((each.occurred_on - period).days),
            each.movement_id,
        ),
    )

    return tuple(found[:MAX_CANDIDATES])


# How much clearer one pairing has to be than the next before it is taken
# without asking, in relative distance from the bill's figure: ten points.
# 2 % against 31 % is the electricity of two houses telling themselves apart;
# 2 % against 3 % is a coin toss, and stays a question.
PAIRING_MARGIN = Decimal("0.10")


def resolve_contests(
    charges: Sequence[tuple[Money, Sequence[ChargeCandidate]]],
) -> list[tuple[ChargeCandidate, ...]]:
    """Settle which certain match each open charge keeps, across all of them.

    Each entry is one open charge — the figure its bill projects, and its
    candidates. A certain match **stays certain only when the two choose each
    other clearly**: the movement is nearer this charge's figure than any
    other charge it is certain for, and this charge's figure is nearer that
    movement than any other movement certain for it — both by at least
    `PAIRING_MARGIN`. Every other certain match becomes likely: a question.

    And the charge's other certain payments must each be clearly another
    charge's: pairing settles a contest *between* bills, and never picks
    between two payments for a single bill — that stays a question.

    That is what tells several bills to one merchant apart. Two houses' light
    paid to Celsia the same week, one bill near 155 000 and one near 120 000:
    157 731 and 119 914 each sit far nearer one of them, so each is linked to
    its own. Two figures nearly alike are a coin toss, and stay a proposal —
    and so do identical bills, which nothing can tell apart. A match with no
    rival anywhere is untouched, so everything that linked before still does.
    """
    nearest_charges: dict[str, list[tuple[Decimal, int]]] = {}
    nearest_movements: dict[int, list[tuple[Decimal, str]]] = {}

    for index, (projected, candidates) in enumerate(charges):
        for candidate in candidates:
            if candidate.quality is not MatchQuality.CERTAIN:
                continue

            distance = _distance(candidate.amount, projected)
            nearest_charges.setdefault(candidate.movement_id, []).append(
                (distance, index),
            )
            nearest_movements.setdefault(index, []).append(
                (distance, candidate.movement_id),
            )

    def first_charge(movement_id: str) -> int | None:
        ranked = nearest_charges[movement_id]
        best = min(ranked, key=lambda pair: pair[0])[1]

        return best if _clearly_first(ranked, best) else None

    def chosen(index: int, movement_id: str) -> bool:
        if not (
            first_charge(movement_id) == index
            and _clearly_first(nearest_movements[index], movement_id)
        ):
            return False

        # Every other payment this charge could take has to be clearly some
        # *other* charge's. Otherwise one bill with two payments — Centroaguas
        # charging twice against a single declaration — would keep the nearer
        # without asking, and that is a guess, not a pairing.
        return all(
            first_charge(other) not in (None, index)
            for _, other in nearest_movements[index]
            if other != movement_id
        )

    return [
        tuple(
            candidate
            if candidate.quality is not MatchQuality.CERTAIN
            or chosen(index, candidate.movement_id)
            else dataclasses.replace(candidate, quality=MatchQuality.LIKELY)
            for candidate in candidates
        )
        for index, (_, candidates) in enumerate(charges)
    ]


def _distance(charged: Money, projected: Money) -> Decimal:
    return abs(charged.amount - projected.amount) / projected.amount


def _clearly_first[T](ranked: Sequence[tuple[Decimal, T]], wanted: T) -> bool:
    """Whether `wanted` is the nearest, and by the margin, or the only one."""
    ordered = sorted(ranked, key=lambda pair: pair[0])

    if ordered[0][1] != wanted:
        return False

    return len(ordered) == 1 or ordered[1][0] - ordered[0][0] >= PAIRING_MARGIN


def only_certain(candidates: Sequence[ChargeCandidate]) -> ChargeCandidate | None:
    """The one candidate good enough to link without asking, if there is one.

    None when there are none, and — the part that matters — None when there
    are two. An automatic charge that had to pick between them would be
    guessing which of somebody's movements paid for what, and being wrong
    means a real charge left unaccounted while its money is filed as another.
    """
    certain = [
        candidate
        for candidate in candidates
        if candidate.quality is MatchQuality.CERTAIN
    ]

    if len(certain) != 1:
        return None

    return certain[0]


def _candidate(
    movement: Transaction,
    *,
    bill: ScheduledBill,
    period: dt.date,
    keys: frozenset[str],
    merchant_ids: Mapping[str, str],
    zone: dt.tzinfo,
    taken: frozenset[str],
    known: Mapping[str, KnownMerchant],
    named_merchant: str | None,
) -> ChargeCandidate | None:
    """This movement as a candidate for this charge, or None if it cannot be.

    The hard refusals come first and none of them is a matter of degree: a
    movement this app wrote, a transfer, one already spoken for, the wrong
    direction, the wrong currency, the wrong account or the wrong week is not
    a weak match — it is a different fact.
    """
    if movement.origin.is_self_written or movement.is_transfer:
        return None

    if movement.id.value in taken:
        return None

    if movement.direction is not bill.direction:
        return None

    if movement.amount.currency is not bill.amount.currency:
        return None

    if not _same_account(movement, bill):
        return None

    occurred_on = movement.occurred_at.to_datetime().astimezone(zone).date()

    if abs((occurred_on - period).days) > MATCH_WINDOW_DAYS:
        return None

    same_merchant = (
        counterparty_key(
            movement.counterparty,
            merchant_ids.get(movement.counterparty),
        )
        in keys
    )
    close_enough = _within_tolerance(
        movement.amount,
        bill.amount,
        tolerance=AMOUNT_TOLERANCE,
    )
    # Only through the merchant: on its own, a figure half a bill away is any
    # purchase that week, and would put every one of them up as a proposal.
    varied_as_declared = (
        bill.variable_amount
        and same_merchant
        and _within_tolerance(
            movement.amount,
            bill.amount,
            tolerance=VARIABLE_AMOUNT_TOLERANCE,
        )
    )

    if not same_merchant and not close_enough:
        return None

    if not same_merchant and _someone_else(
        known.get(movement.counterparty),
        bill=bill,
        named_merchant=named_merchant,
    ):
        return None

    # A movement two charges of the same bill both reach is never certain,
    # however well it matches either of them. A weekly bill's charges are
    # seven days apart and this window is five on each side, so the overlap
    # is ordinary rather than exotic — and picking the nearer one would leave
    # the other to be charged automatically for money that already left.
    # Both periods offer it, and a person decides.
    contested = len(bill.charges_around(occurred_on)) > 1

    return ChargeCandidate(
        movement_id=movement.id.value,
        counterparty=movement.counterparty,
        amount=movement.amount,
        occurred_on=occurred_on,
        quality=(
            MatchQuality.CERTAIN
            if same_merchant and (close_enough or varied_as_declared) and not contested
            else MatchQuality.LIKELY
        ),
    )


def _someone_else(
    merchant: KnownMerchant | None,
    *,
    bill: ScheduledBill,
    named_merchant: str | None,
) -> bool:
    """Whether a movement that only shares the bill's figure is plainly not it.

    A figure alone is weak evidence, and it was proposing games bought on
    Eneba as the electricity: same week, a similar amount, an entirely
    different kind of spending. Two things say "someone else" outright —

    * **a different kind of spending**: the movement's merchant is filed
      under a category, the bill under another (neither "uncategorized",
      which says nothing about who anybody is); or
    * **a different merchant its owner already confirmed**, when the bill
      names whom it is paid to. A merchant Merchant made up on its own may yet
      be the same company under a new spelling, so only a reviewed one rules
      the movement out.

    Anything less certain stays a proposal, as before: a movement with no
    merchant yet, or with one nobody has looked at, may well be the bill.
    """
    if merchant is None:
        return False

    bill_category = bill.category

    if (
        merchant.category not in (None, UNCATEGORIZED)
        and bill_category not in (None, UNCATEGORIZED)
        and merchant.category != bill_category
    ):
        return True

    return (
        named_merchant is not None
        and merchant.confirmed
        and merchant.merchant_id != named_merchant
    )


def _same_account(movement: Transaction, bill: ScheduledBill) -> bool:
    """Whether the two can be talking about the same account.

    A mismatch refuses; an absence does not. A movement no account answered
    for is the ordinary state of half this ledger — nobody has to declare
    their accounts for any of this to work — and refusing it would mean the
    feature only ever reconciles for people who did.
    """
    if bill.account_id is None or movement.account_id is None:
        return True

    return movement.account_id == bill.account_id


def _within_tolerance(charged: Money, projected: Money, *, tolerance: Decimal) -> bool:
    return abs(charged.amount - projected.amount) <= projected.amount * tolerance
