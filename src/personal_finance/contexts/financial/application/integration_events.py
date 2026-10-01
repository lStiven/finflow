"""What financial publishes to the rest of the system.

This module is the context's outward contract. Nothing here is derived
automatically from a domain object: every field is listed by hand, so adding
one to an aggregate can never silently widen what other contexts receive.

Two facts leave, and they are the two a reader outside this context cannot
reconstruct on its own:

* money moved — a movement exists, for this amount, with this counterparty;
* an account's running total changed because of it.

Everything else stays in. An account being opened, renamed, closed or
reopened, a fingerprint linked or unlinked, financing set or cleared, a
balance rebuilt or restated, a movement edited, unassigned or erased: all of
it is this context's own lifecycle. Publishing it would turn how Financial
keeps its books into a contract somebody else can depend on.

`TransactionAssigned` stays in for a subtler reason: it says a movement found
its account, which is bookkeeping, and the balance change it causes is
already published as its own fact with the figures on it.

And a warning about that second fact, because its name invites the wrong
reading: `AccountBalanceChanged` is *the balance after a movement*, not a
replication stream. A balance also moves when a movement is erased, when the
total is rebuilt from the ledger, and when the owner restates it — none of
which are published, because none of them is money moving and an alert about
one would be noise. A subscriber that mirrors balances from this will drift.
Whoever needs the current balance asks the API for it.
"""

from __future__ import annotations

import uuid

from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    TransactionRecorded,
)
from personal_finance.shared.application.integration import IntegrationEvent
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import JsonValue, PosixTime


SOURCE = "finflow.financial"

MOVEMENT_RECORDED = "MovementRecorded"
ACCOUNT_BALANCE_CHANGED = "AccountBalanceChanged"
VERSION = 1


class FinancialIntegrationEventTranslator:
    def translate(self, event: Event) -> IntegrationEvent | None:
        match event:
            case TransactionRecorded():
                return _event(
                    detail_type=MOVEMENT_RECORDED,
                    event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    payload={
                        "movement_id": str(event.movement_id.value),
                        "user_id": str(event.user_id.value),
                        "direction": event.direction.value,
                        # A string, like everywhere else that money crosses
                        # the bus: a JSON float rounds a cent away.
                        "amount": str(event.amount.amount),
                        "currency": event.amount.currency.value,
                        # When the money moved, not when this was recorded —
                        # an alert can arrive days late, and a subscriber
                        # writing "acabas de gastar" needs to know the
                        # difference.
                        #
                        # Not `occurred_at`: the transport writes the
                        # envelope's own `occurred_at` into the same detail
                        # object, so a payload key by that name is silently
                        # overwritten with the moment the fact was recorded.
                        # Ingestion escapes it by nesting its payload; this
                        # one is flat, so the name has to differ.
                        "movement_occurred_at": (
                            event.movement_occurred_at.as_epoch_seconds()
                        ),
                        "counterparty": event.counterparty,
                        "bank": event.bank,
                        "origin": event.origin.value,
                        # Whether the movement landed outside every balance —
                        # read off the account it landed on, never off the
                        # fingerprint. The fingerprint is how an alert finds
                        # an account, and a bill or a hand-written movement on
                        # an account has none: reading it here called every
                        # one of those "sin cuenta asignada". The fingerprint
                        # itself stays in: matching alerts to accounts is
                        # this context's business.
                        "unassigned": event.account_id is None,
                    },
                )
            case AccountBalanceChanged():
                return _event(
                    detail_type=ACCOUNT_BALANCE_CHANGED,
                    event_id=event.event_id,
                    occurred_at=event.occurred_at,
                    payload={
                        "account_id": str(event.account_id.value),
                        "user_id": str(event.user_id.value),
                        # What moved it, so a subscriber can tie this to the
                        # `MovementRecorded` it already saw instead of
                        # guessing from the amount.
                        "movement_id": str(event.movement_id.value),
                        "direction": event.direction.value,
                        "amount": str(event.amount.amount),
                        # Signed, because a balance has a sign and a movement
                        # does not: -1200.50 on a savings account is an
                        # overdraft, and splitting it into a magnitude and a
                        # flag invites a reader to drop the flag.
                        "balance": str(event.balance.signed_amount),
                        # One currency for both figures: an amount in another
                        # one cannot reach a balance — `Balance` refuses the
                        # arithmetic — so by the time this event exists they
                        # necessarily agree.
                        "currency": event.balance.currency.value,
                    },
                )
            case _:
                return None


def _event(
    *,
    detail_type: str,
    event_id: uuid.UUID,
    occurred_at: PosixTime,
    payload: dict[str, JsonValue],
) -> IntegrationEvent:
    return IntegrationEvent(
        source=SOURCE,
        detail_type=detail_type,
        version=VERSION,
        event_id=event_id,
        payload=payload,
        occurred_at=occurred_at,
    )
