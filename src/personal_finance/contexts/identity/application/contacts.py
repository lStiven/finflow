"""Where to write to one account — Identity's published answer for a context
that has to tell somebody something and knows only their id.

Alerts is the caller, through its own adapter, when it has to email a person
about their account. Nothing here sends anything: the address is Identity's
to keep, what to say is the caller's.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccountContact:
    """Plain strings on purpose: a caller in another context must not come
    to depend on this context's value objects."""

    user_id: UserId
    email: str
    #: None for an account that never set one.
    name: str | None


class AccountContactDirectory(Protocol):
    def find_contact(self, user_id: UserId) -> AccountContact | None:
        """The account's address and name, or None when there is no such
        account. May lag a just-made change by a moment."""
        ...


class GetAccountContactUseCase:
    def __init__(self, *, directory: AccountContactDirectory) -> None:
        self._directory = directory

    def execute(self, *, user_id: UserId) -> AccountContact | None:
        return self._directory.find_contact(user_id)
