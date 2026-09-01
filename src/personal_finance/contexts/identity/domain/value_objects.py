from __future__ import annotations

import dataclasses

from personal_finance.shared.domain.value_objects import ValueObject


@dataclasses.dataclass(frozen=True, slots=True)
class Email(ValueObject):
    """A user's login identity.

    Identity keeps its own value object rather than importing ingestion's
    `EmailAddress`: each context owns its projection of what an email address
    means to it, and the two must stay free to evolve independently.
    """

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip().lower()

        if not value or "@" not in value:
            raise ValueError("Invalid email address")

        object.__setattr__(self, "value", value)


@dataclasses.dataclass(frozen=True, slots=True)
class PersonName(ValueObject):
    """What the user wants to be called.

    Not an identifier and never unique: two accounts may share a name, and an
    account may have none at all — `User.name` is optional precisely so an
    address and a password are still all it takes to sign up.
    """

    MAX_LENGTH = 80

    value: str

    def __post_init__(self) -> None:
        value = " ".join(self.value.split())

        if not value:
            raise ValueError("Name cannot be empty")

        if len(value) > self.MAX_LENGTH:
            raise ValueError(f"Name cannot exceed {self.MAX_LENGTH} characters")

        object.__setattr__(self, "value", value)

    def to_dict(self) -> str:
        return self.value


@dataclasses.dataclass(frozen=True, slots=True)
class PasswordHash(ValueObject):
    """An already-hashed password. Never constructed from plaintext directly —
    that conversion is `PasswordHasher`'s job, which is infrastructure.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Password hash cannot be empty")

    def to_dict(self) -> str:
        # Never serialize the hash itself into a log or an event payload.
        return "<redacted>"


@dataclasses.dataclass(frozen=True, slots=True)
class SecretHash(ValueObject):
    """The hash of a short-lived secret — a one-time code, a reset token.

    Separate from `PasswordHash` because the two are not interchangeable: a
    password hash is deliberately slow and salted per record, while a lookup
    key over a 256-bit random token has to be deterministic to be a key at
    all. What they share is that neither may ever be printed, so both redact
    themselves on serialization.
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("Secret hash cannot be empty")

    def to_dict(self) -> str:
        return "<redacted>"


@dataclasses.dataclass(frozen=True, slots=True)
class VerificationCode(ValueObject):
    """The one-time code emailed to prove an address is real and reachable.

    Digits only and fixed length, because it is read off a screen and typed
    back in by a person. The entropy that matters is not in the code — six
    digits is a million possibilities — but in the attempt cap and the short
    life the challenge that holds it enforces.
    """

    LENGTH = 6

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        # `str.isdigit` is true for non-ASCII digits too, and those would not
        # survive a round trip through the mail client that shows them.
        if len(value) != self.LENGTH or not (value.isascii() and value.isdigit()):
            raise ValueError(f"A verification code is {self.LENGTH} digits")

        object.__setattr__(self, "value", value)

    def to_dict(self) -> str:
        # Never into a log or an event payload: this is a credential until it
        # is used, and it is short enough that one leaked line is the whole
        # secret.
        return "<redacted>"
