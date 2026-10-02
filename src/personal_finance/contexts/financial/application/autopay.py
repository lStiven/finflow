"""Settling the charges nobody answered for, when the app is next opened.

Three different answers, and which one a charge gets is decided by evidence
rather than by a setting:

* **A movement already paid it.** The bank did announce the charge after all,
  or its owner paid it from an account this app reads. Then the charge is
  answered *by that movement* and **nothing is written to the ledger** — this
  runs for every active bill, armed or not, because recognising money that is
  already recorded is not an action on somebody's behalf, it is the app
  reading what it has. It is also the only thing standing between a confirmed
  charge and the bank's own email for the same money.
* **Nothing matched and the bill charges itself.** Then the charge is written,
  exactly as pressing "Pagado" writes it, through the very same use case.
* **Something matched but not clearly enough.** Then nothing happens and the
  charge comes back as a *proposal*: one tap links it, one tap dismisses it.
  A question that moves money is a question for a person.

**Lazy, not scheduled**, like the accruals in `financing.py` and for the same
reason: a daily trigger needs a way to walk every user, which this deployment
does not have yet. So this runs when somebody opens the screen, which means
the charge lands when they are there to see it — which is the moment an undo
is worth something.

Two bounds keep a long absence from becoming a surprise. A charge is only
written once its **match window has closed** (`MATCH_WINDOW_DAYS`), so a
movement arriving four days late is still recognised as the charge instead of
becoming a second one; and only while it is still recent
(`AUTOPAY_LOOKBACK_DAYS`), so an app opened after three months does not post a
quarter of charges in one go.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
import enum
import logging

from personal_finance.contexts.financial.application.bills import (
    ConfirmChargeCommand,
    SettleBillChargeUseCase,
    SettledCharge,
    charges_between,
    read_payments,
)
from personal_finance.contexts.financial.application.financing import (
    today_in,
    zone_of,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    TransactionNotFoundError,
)
from personal_finance.contexts.financial.application.ports import (
    ChargeLookup,
    MerchantDirectory,
    MovementHistory,
    ScheduledBillRepository,
)
from personal_finance.contexts.financial.domain.bills import (
    AUTOPAY_LOOKBACK_DAYS,
    MATCH_WINDOW_DAYS,
    BillId,
    BillOccurrence,
    BillStatus,
    ScheduledBill,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.exceptions import (
    FinancialDomainError,
)
from personal_finance.contexts.financial.domain.reconciliation import (
    ChargeCandidate,
    bill_keys,
    candidates_for,
    only_certain,
)
from personal_finance.shared.domain.value_objects import UserId


_logger = logging.getLogger(__name__)


class SettlementAction(enum.Enum):
    """What this run did about one charge."""

    #: Answered by a movement the ledger already held. Nothing was written.
    MATCHED = "matched"
    #: Written into the ledger, because the bill charges itself and nothing
    #: out there looked like it.
    CHARGED = "charged"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AutomaticSettlement:
    """One charge this run answered for, and how."""

    action: SettlementAction
    settled: SettledCharge


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ChargeProposal:
    """A charge with movements that could be it, and no clear answer.

    Carries the bill's name because this is read as a list across every bill,
    not inside one card: "«Gimnasio», 4 de septiembre" is the question, and
    an id is not.
    """

    bill_id: BillId
    bill_name: str
    occurrence: BillOccurrence
    candidates: tuple[ChargeCandidate, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SettlementView:
    today: dt.date
    settled: tuple[AutomaticSettlement, ...]
    proposals: tuple[ChargeProposal, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SettleDueChargesCommand:
    user_id: UserId
    timezone: str


class SettleDueChargesUseCase:
    """What the bills screen runs when it opens.

    Reads through `MovementHistory` — the port that can only list — so the
    search for a matching movement cannot reach `record` or `remove`, and
    writes only through `SettleBillChargeUseCase`, which is what the two
    buttons on that screen already use. Nothing here has a ledger of its own:
    an automatic charge that took a private path would be a second way for
    money to appear, and the first one to drift.
    """

    def __init__(
        self,
        *,
        bills: ScheduledBillRepository,
        charges: ChargeLookup,
        ledger: MovementHistory,
        settle: SettleBillChargeUseCase,
        merchants: MerchantDirectory | None = None,
    ) -> None:
        self._bills = bills
        self._charges = charges
        self._ledger = ledger
        self._settle = settle
        self._merchants = merchants

    def execute(self, command: SettleDueChargesCommand) -> SettlementView:
        zone = zone_of(command.timezone)
        today = today_in(zone)
        since = today - dt.timedelta(days=AUTOPAY_LOOKBACK_DAYS)
        # Past today, because a domiciled charge is often taken a day or two
        # early. A charge whose window is still open cannot be *written* — see
        # `charges_itself_on` — but it can certainly already have been paid.
        until = today + dt.timedelta(days=MATCH_WINDOW_DAYS)

        declared = list(self._bills.list_by_user(command.user_id))
        active = [bill for bill in declared if bill.status is BillStatus.ACTIVE]

        if not active:
            return SettlementView(today=today, settled=(), proposals=())

        payments = read_payments(
            charges_between(active, since=since, until=until, today=today),
            user_id=command.user_id,
            charges=self._charges,
        )
        movements = self._movements(
            command.user_id,
            since=since,
            until=until,
            zone=zone,
        )
        merchant_ids = self._merchant_ids(
            command.user_id,
            counterparties=[
                *(movement.counterparty for movement in movements),
                *(bill.name for bill in active),
            ],
        )
        # Every movement already answering for a charge, across every bill —
        # paused ones too. One payment settles one thing, and a paused bill's
        # link is still a claim on that movement.
        taken = {movement for bill in declared for movement in bill.linked.values()}

        settled: list[AutomaticSettlement] = []
        proposals: list[ChargeProposal] = []

        for bill in active:
            keys = bill_keys(bill, merchant_ids.get(bill.name))

            for occurrence in bill.occurrences(
                since=since,
                until=until,
                today=today,
                payments=payments.get(bill.id, {}),
            ):
                if occurrence.state.is_settled:
                    continue

                candidates = candidates_for(
                    bill,
                    period=occurrence.due_on,
                    movements=movements,
                    keys=keys,
                    merchant_ids=merchant_ids,
                    zone=zone,
                    taken=frozenset(taken),
                )
                answered = self._answer(
                    bill,
                    occurrence,
                    candidates=candidates,
                    today=today,
                )

                if answered is None:
                    if candidates:
                        proposals.append(
                            ChargeProposal(
                                bill_id=bill.id,
                                bill_name=bill.name,
                                occurrence=occurrence,
                                candidates=candidates,
                            ),
                        )

                    continue

                settled.append(answered)

                paid_with = answered.settled.occurrence.payment

                # So the next charge in this same run cannot be settled by
                # the movement this one just claimed.
                if answered.action is SettlementAction.MATCHED and paid_with:
                    taken.add(paid_with.movement_id)

        return SettlementView(
            today=today,
            settled=tuple(settled),
            proposals=tuple(proposals),
        )

    def _answer(
        self,
        bill: ScheduledBill,
        occurrence: BillOccurrence,
        *,
        candidates: Sequence[ChargeCandidate],
        today: dt.date,
    ) -> AutomaticSettlement | None:
        """Link, charge, or leave it for its owner.

        Every refusal the two use cases can raise is caught and the charge is
        left alone. A closed account, a bill amended between the read and the
        write, a movement somebody linked elsewhere a second ago, a movement
        the listing returned and the lookup no longer finds: none of them is
        worth failing the screen over, because the honest outcome of all of
        them is the same — the charge stays unpaid, visibly, and the person
        looking at it decides. **This runs on every visit to the screen**, so
        one unlucky charge must not be able to 404 the whole page.

        What is *not* caught is anything else: a table that is not answering
        must not read as "nothing was due".
        """
        certain = only_certain(candidates)

        try:
            if certain is not None:
                return AutomaticSettlement(
                    action=SettlementAction.MATCHED,
                    settled=self._settle.link(
                        user_id=bill.user_id,
                        bill_id=bill.id,
                        period=occurrence.due_on,
                        movement_id=certain.movement_id,
                    ),
                )

            if candidates or not bill.charges_itself_on(occurrence.due_on, today=today):
                return None

            return AutomaticSettlement(
                action=SettlementAction.CHARGED,
                settled=self._settle.confirm(
                    ConfirmChargeCommand(
                        user_id=bill.user_id,
                        bill_id=bill.id,
                        period=occurrence.due_on,
                    ),
                ),
            )
        except (
            ValueError,
            AccountNotFoundError,
            TransactionNotFoundError,
            # The base, so a currency mismatch and a closed account are
            # covered by what they have in common rather than by a list that
            # has to be kept in step with the domain.
            FinancialDomainError,
        ):
            _logger.warning(
                "could not settle a due charge automatically",
                extra={"bill": str(bill.id.value), "period": occurrence.due_on},
                exc_info=True,
            )

            return None

    def _movements(
        self,
        user_id: UserId,
        *,
        since: dt.date,
        until: dt.date,
        zone: dt.tzinfo,
    ) -> list[Transaction]:
        """The movements close enough to any of these charges to be one.

        Widened by the match window at both ends: a charge due on the first
        day of the span can be paid five days before it.
        """
        first = since - dt.timedelta(days=MATCH_WINDOW_DAYS)
        last = until + dt.timedelta(days=MATCH_WINDOW_DAYS)

        return [
            movement
            for movement in self._ledger.list_all(user_id)
            if first <= _day_of(movement, zone) <= last
        ]

    def _merchant_ids(
        self,
        user_id: UserId,
        *,
        counterparties: Sequence[str],
    ) -> Mapping[str, str]:
        """Which merchant owns each spelling, in one round trip.

        Optional, like everywhere else in this context: without the directory
        every comparison falls back to the folded text, so matching still
        works and simply recognises fewer of the same merchant's spellings.
        An enrichment that is down must not take a feature with it.
        """
        if self._merchants is None:
            return {}

        wanted = sorted({name for name in counterparties if name})

        if not wanted:
            return {}

        return {
            counterparty: attribution.merchant_id
            for counterparty, attribution in self._merchants.attribute(
                user_id=user_id,
                counterparties=wanted,
            ).items()
        }


def _day_of(movement: Transaction, zone: dt.tzinfo) -> dt.date:
    """The calendar day this movement happened on, where its owner lives.

    Never in UTC, for the reason the detector gives: a charge at nine in the
    evening in Bogotá is the 15th there and the 16th in UTC, and five days of
    window spent on that difference is a day of window lost.
    """
    return movement.occurred_at.to_datetime().astimezone(zone).date()
