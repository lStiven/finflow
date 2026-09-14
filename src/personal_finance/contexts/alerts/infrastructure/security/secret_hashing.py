"""How a link token becomes the key it is stored under.

SHA-256, unsalted, because the hash *is* the partition key and a key has to
hash the same way twice — which bcrypt, by design, does not. That is safe
here and would be wrong for anything a person types: what goes in is
`secrets.token_urlsafe(32)`, and there is nothing to brute-force in 256 bits
of entropy, so slowness would buy nothing and only cost a lookup.

Alerts keeps its own rather than importing identity's. A dozen lines is the
price of the two contexts being able to change independently.
"""

from __future__ import annotations

import hashlib

from personal_finance.contexts.alerts.domain.value_objects import SecretHash


class Sha256TokenHasher:
    """`TokenHasher` for the high-entropy token that travels in a link."""

    def hash(self, token: str) -> SecretHash:
        return SecretHash(hashlib.sha256(token.encode("utf-8")).hexdigest())
