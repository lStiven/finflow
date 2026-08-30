from __future__ import annotations

import dataclasses

from personal_finance.contexts.identity.application.ports import InboxRegistration


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserCommand:
    email: str
    password: str
    # Optional: an account is identified by its email, so a name is something
    # the owner may add now, later through `UpdateProfileCommand`, or never.
    name: str | None = None
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UpdateProfileCommand:
    """Only the name, for now: the email is the account's identity and the key
    its record lives under, so changing it is not an edit of this shape.
    """

    name: str
