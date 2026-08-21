from __future__ import annotations

import dataclasses

from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UserRegistered(Event):
    user_id: UserId
    email: Email
