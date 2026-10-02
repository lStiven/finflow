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
  give or take. This is what may be linked without asking.
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


def bill_keys(bill: ScheduledBill, merchant_id: str | None) -> frozenset[str]:
    """Every counterparty key this bill could be recognised under.

    Two, when a merchant owns the bill's name: through the merchant, which is
    how "Gimnasio" is recognised in a charge that says `GYMSA*BOG`, and
    through the folded name itself, which is the only thing that works for the
    ordinary case where nothing has ever attributed it.
    """
    text = counterparty_key(bill.name, None)

    if merchant_id is None:
        return frozenset({text})

    return frozenset({counterparty_key(bill.name, merchant_id), text})


def candidates_for(
    bill: ScheduledBill,
    *,
    period: dt.date,
    movements: Sequence[Transaction],
    keys: frozenset[str],
    merchant_ids: Mapping[str, str],
    zone: dt.tzinfo,
    taken: frozenset[str] = frozenset(),
) -> tuple[ChargeCandidate, ...]:
    """The movements that could be this charge, best first.

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
    close_enough = _within_tolerance(movement.amount, bill.amount)

    if not same_merchant and not close_enough:
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
            if same_merchant and close_enough and not contested
            else MatchQuality.LIKELY
        ),
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


def _within_tolerance(charged: Money, projected: Money) -> bool:
    return abs(charged.amount - projected.amount) <= projected.amount * AMOUNT_TOLERANCE
