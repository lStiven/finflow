"""Where every one-time secret in this context comes from.

`secrets`, never `random`: the module that seeds from the operating system,
not the one that produces a reproducible sequence. A reset token drawn from a
predictable generator is not a secret at all.
"""

from __future__ import annotations

import secrets

from personal_finance.contexts.identity.domain.value_objects import VerificationCode


# 32 bytes before encoding — 256 bits, the same order as the keys guarding
# everything else here. It is the only thing standing behind a reset link.
_TOKEN_BYTES = 32


class SecretsSecretGenerator:
    """`SecretGenerator` over the standard library's CSPRNG."""

    def verification_code(self) -> str:
        # `randbelow` over the whole range rather than digit by digit, so
        # every code including `000000` is equally likely.
        length = VerificationCode.LENGTH

        return f"{secrets.randbelow(10**length):0{length}d}"

    def opaque_token(self) -> str:
        return secrets.token_urlsafe(_TOKEN_BYTES)
