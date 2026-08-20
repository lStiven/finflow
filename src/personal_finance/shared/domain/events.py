from __future__ import annotations

import dataclasses
import uuid

from personal_finance.shared.domain.value_objects import PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Base class for domain events.

    `kw_only=True` lets subclasses add required fields after the defaulted
    ones declared here, regardless of declaration order.
    """

    event_id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)
    occurred_at: PosixTime = dataclasses.field(default_factory=PosixTime.now)
