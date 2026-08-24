from __future__ import annotations

import dataclasses

from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Money, PosixTime, UserId


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
