"""Where the unguessable half of a deep link comes from.

`secrets`, never `random`: the module that seeds from the operating system,
not the one that produces a reproducible sequence. A link token drawn from a
predictable generator is not a secret at all, and this one is the only thing
standing between a stranger and somebody's purchases.
"""

from __future__ import annotations

import secrets


# 32 bytes before encoding — 256 bits, which `token_urlsafe` renders as 43
# characters from the same alphabet Telegram allows in a deep link's payload.
# That is not a coincidence worth losing: it is why the token needs no
# escaping to travel in `?start=`.
_TOKEN_BYTES = 32


class SecretsTokenGenerator:
    """`TokenGenerator` over the standard library's CSPRNG."""

    def link_token(self) -> str:
        return secrets.token_urlsafe(_TOKEN_BYTES)
