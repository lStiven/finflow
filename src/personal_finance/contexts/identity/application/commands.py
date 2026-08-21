from __future__ import annotations

import dataclasses

from personal_finance.contexts.identity.application.ports import InboxRegistration


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserCommand:
    email: str
    password: str
    # Optional on purpose: an account can be created bare and inboxes
    # attached later through `AddInboxesCommand`.
    inboxes: tuple[InboxRegistration, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LoginCommand:
    email: str
    password: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AddInboxesCommand:
    inboxes: tuple[InboxRegistration, ...]
