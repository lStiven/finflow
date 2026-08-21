from __future__ import annotations

from dataclasses import dataclass
from typing import Self

from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclass(slots=True)
class User(AggregateRoot[UserId]):
    email: Email
    password_hash: PasswordHash
    registered_at: PosixTime

    @classmethod
    def register(
        cls,
        *,
        email: Email,
        password_hash: PasswordHash,
        registered_at: PosixTime,
    ) -> Self:
        user = cls(
            id=UserId.new(),
            email=email,
            password_hash=password_hash,
            registered_at=registered_at,
        )
        user.record_event(
            UserRegistered(user_id=user.id, email=user.email),
        )

        return user
