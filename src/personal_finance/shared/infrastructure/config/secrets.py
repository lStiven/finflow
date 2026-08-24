"""Reading a secret out of SSM Parameter Store instead of a file.

The convention is one prefix. Anywhere a secret is configured, a value of
`ssm:/finflow/production/jwt-secret` means "the SecureString at that path",
and anything else is the secret itself. That keeps local development on plain
values in `.env` — moto has no KMS key and no parameters worth having — while
a deployed environment holds nothing but references.

Explicit rather than a global switch, so one deployment can move its secrets
across one at a time, and so `grep ssm:` answers which values are real.

Standard parameters are free up to ten thousand, which is why this is
Parameter Store and not Secrets Manager: nothing here rotates automatically
today, and paying per secret per month for a private deployment buys nothing.
"""

from __future__ import annotations

import functools
import logging

from pydantic import SecretStr


_logger = logging.getLogger(__name__)

SSM_PREFIX = "ssm:"


class SecretResolutionError(Exception):
    """Raised when a configured secret reference cannot be read.

    Never swallowed into an empty string: a missing token-signing secret has
    to stop the process, not quietly downgrade it to one an attacker can
    guess.
    """


def resolve(value: SecretStr) -> SecretStr:
    """The secret itself, following an `ssm:` reference when it is one."""
    raw = value.get_secret_value()

    if not raw.startswith(SSM_PREFIX):
        return value

    name = raw[len(SSM_PREFIX) :].strip()

    if not name:
        raise SecretResolutionError(
            f"A {SSM_PREFIX} reference needs a parameter name after it",
        )

    return SecretStr(_read_parameter(name))


@functools.lru_cache(maxsize=32)
def _read_parameter(name: str) -> str:
    """Fetch and cache one parameter for the life of the process.

    Cached because settings are read on every request that touches them, and
    a network call per read would turn a signing key into a latency budget.
    A rotated secret therefore needs a restart, which is the trade this
    deployment can afford.
    """
    # Imported here rather than at module scope: `session` reads settings, and
    # settings is what imports this module.
    from personal_finance.shared.infrastructure.aws.session import get_ssm_client

    try:
        response = get_ssm_client().get_parameter(Name=name, WithDecryption=True)
    except Exception as error:
        # The name is safe to log; the value never is.
        raise SecretResolutionError(
            f"Could not read the secret at {name!r} from Parameter Store",
        ) from error

    secret = response.get("Parameter", {}).get("Value", "")

    if not secret:
        raise SecretResolutionError(f"The parameter at {name!r} is empty")

    _logger.info("secret resolved from Parameter Store", extra={"parameter": name})

    return secret


def reset_secrets_cache() -> None:
    """Drop what has been fetched. Only useful for tests."""
    _read_parameter.cache_clear()
