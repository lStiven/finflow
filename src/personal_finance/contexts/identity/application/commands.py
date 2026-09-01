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
    # Not optional: the address has to have proved it is reachable before an
    # account is built on it, and this is that proof. It comes from
    # `ConfirmEmailVerificationCommand` and is spent here.
    verification_token: str
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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RequestEmailVerificationCommand:
    """Ask for a code to be mailed to an address that wants an account."""

    email: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmEmailVerificationCommand:
    """The code, typed back in by whoever received it."""

    email: str
    code: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RequestPasswordResetCommand:
    email: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ResetPasswordCommand:
    """The secret out of the emailed link, and what to set instead."""

    token: str
    new_password: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ChangePasswordCommand:
    """A password change by somebody who can still log in.

    The current password is asked for even though the caller is already
    authenticated: a token left behind on a shared machine must not be enough
    to take the account over.
    """

    current_password: str
    new_password: str
