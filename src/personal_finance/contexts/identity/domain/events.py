from __future__ import annotations

import dataclasses

from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UserRegistered(Event):
    user_id: UserId
    email: Email


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PasswordChanged(Event):
    """Somebody's password was replaced, by a reset or by their own request.

    Stays inside this context — the translator maps no such event, so it
    reaches the local audit log and nothing else. Which account it happened
    to is worth recording; nothing about the password itself is.
    """

    user_id: UserId
    email: Email
