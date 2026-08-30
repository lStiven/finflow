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
