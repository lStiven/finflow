from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from personal_finance.contexts.identity.domain.events import (
    PasswordChanged,
    UserRegistered,
)
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
    # Which generation of this account's credentials is current. Travels in
    # every access token and is compared against this value on every
    # authenticated request, which is what makes changing a password end the
    # sessions opened with the old one — a stolen token that outlived the
    # reset it provoked would make the reset a formality.
    #
    # A counter rather than the moment of the change, because the comparison
    # has to be exact: a timestamp in seconds cannot tell a password set one
    # millisecond after registration from the registration itself, and that
    # is precisely the case where a token must stop working.
    credential_version: int = 1

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

    def change_password(self, password_hash: PasswordHash) -> None:
        """Replace the stored password and retire every token issued before.

        Retiring them is the point rather than a side effect: somebody resets
        a password precisely because the old one may be in the wrong hands,
        and a session opened with it has to stop working too.
        """
        self.password_hash = password_hash
        self.credential_version += 1
        self.record_event(PasswordChanged(user_id=self.id, email=self.email))
