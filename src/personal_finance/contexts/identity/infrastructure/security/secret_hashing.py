"""Two ways to hash a one-time secret, for two different jobs.

They are not interchangeable, and picking the wrong one is a real mistake in
both directions:

* A **verification code** is six digits. If the table leaked, a fast hash of
  it is a million guesses — seconds of work — so it is hashed with bcrypt,
  salted and slow, exactly like a password. The record is found by address, so
  nothing needs the hash to be reproducible.
* A **reset or registration token** is 256 random bits, and its hash *is* the
  key the record is stored under. That forces a deterministic hash, which
  bcrypt is not. SHA-256 is right here for the same reason it is wrong above:
  there is nothing to brute-force in 256 bits of entropy, so slowness would
  buy nothing and only cost a lookup.
"""

from __future__ import annotations

import hashlib
import hmac

import bcrypt

from personal_finance.contexts.identity.domain.value_objects import SecretHash


class BcryptSecretHasher:
    """`SecretHasher` for low-entropy secrets read by a person."""

    def hash(self, secret: str) -> SecretHash:
        digest = bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt())

        return SecretHash(digest.decode("ascii"))

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        try:
            return bcrypt.checkpw(secret.encode("utf-8"), hashed.value.encode("utf-8"))
        except ValueError:
            # A stored value that is not a bcrypt hash is a failed check, not
            # a crash: the caller is a stranger typing digits at an endpoint.
            return False


class Sha256SecretHasher:
    """`SecretHasher` for high-entropy secrets that travel in a link.

    Unsalted on purpose — the hash is the lookup key — which is safe only
    because what goes in is `secrets.token_urlsafe(32)`. Never hand this a
    password or a six-digit code.
    """

    def hash(self, secret: str) -> SecretHash:
        return SecretHash(hashlib.sha256(secret.encode("utf-8")).hexdigest())

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        return hmac.compare_digest(self.hash(secret).value, hashed.value)
