from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclass(slots=True)
class User(AggregateRoot[UserId]):
    email: Email
    password_hash: PasswordHash
    registered_at: PosixTime
    # Optional: an account is identified by its email, never by its name, so
    # signing up needs no name at all and one can be set later.
    name: PersonName | None = None

    @classmethod
    def register(
        cls,
        *,
        email: Email,
        password_hash: PasswordHash,
        registered_at: PosixTime,
        name: PersonName | None = None,
    ) -> Self:
        user = cls(
            id=UserId.new(),
            email=email,
            password_hash=password_hash,
            registered_at=registered_at,
            name=name,
        )
        user.record_event(
            UserRegistered(user_id=user.id, email=user.email),
        )

        return user

    def rename(self, name: PersonName) -> None:
        """Change what the user is called. Nothing else about the account
        moves with it: the email stays the account's identity, so a rename
        can never collide with another account or orphan its data.
        """
        self.name = name
