from __future__ import annotations

import bcrypt

from personal_finance.contexts.identity.domain.value_objects import PasswordHash


class BcryptPasswordHasher:
    """`PasswordHasher` backed by bcrypt.

    bcrypt embeds its own salt in the output, so nothing else in this context
    ever has to think about salting.
    """

    def hash(self, password: str) -> PasswordHash:
        digest = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

        return PasswordHash(digest.decode("ascii"))

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        try:
            return bcrypt.checkpw(
                password.encode("utf-8"),
                hashed.value.encode("utf-8"),
            )
        except ValueError:
            # A malformed or foreign-format hash (e.g. a decoy from a
            # different hasher) is a failed login, not a crash.
            return False
