from __future__ import annotations

import dataclasses

from personal_finance.contexts.identity.application.ports import InboxRegistration


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserCommand:
    email: str
    password: str
    # Optional: an account can be created with nothing approved yet, and the
    # sender list set later through `UpdateApprovedSendersCommand`. The
    # forwarding address itself is always assigned, regardless.
    inbox: InboxRegistration = dataclasses.field(default_factory=InboxRegistration)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class LoginCommand:
    email: str
    password: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UpdateApprovedSendersCommand:
    inbox: InboxRegistration
