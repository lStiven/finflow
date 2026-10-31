"""A number an operator may change while the deployment keeps running.

Configured like a secret — a plain value, or `ssm:/finflow/<env>/<name>` —
but read differently. A secret is fetched once per process, and a Lambda
process can live for hours: an environment variable, or a parameter cached
for life, would make "change the limit" mean "redeploy", which is what this
exists to avoid. So the parameter is read again once its copy is older than
`max_age_seconds`, and `aws ssm put-parameter` is the whole change.

Plain parameters are enough — nothing here is secret — but a SecureString
still reads correctly.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import time

from personal_finance.shared.infrastructure.aws.session import get_ssm_client
from personal_finance.shared.infrastructure.config.secrets import SSM_PREFIX


_logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE_SECONDS = 300.0


class TunableValueError(Exception):
    """Raised when the configured value is not a whole number, or cannot be
    read the first time it is needed.

    Never replaced by a default: a typo in the parameter would otherwise turn
    a limit somebody set into one nobody chose.
    """


def _read_parameter(name: str) -> str:
    # Decrypted anyway, in case somebody stored it with `just secret-put`: a
    # SecureString read without it is ciphertext, and no limit at all.
    response = get_ssm_client().get_parameter(Name=name, WithDecryption=True)

    return response.get("Parameter", {}).get("Value", "")


class TunableNumber:
    """A non-negative whole number, from configuration or from SSM."""

    def __init__(
        self,
        raw: str,
        *,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        read_parameter: Callable[[str], str] = _read_parameter,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        raw = raw.strip()
        self._max_age_seconds = max_age_seconds
        self._read_parameter = read_parameter
        self._clock = clock
        # Exactly one of these two describes where the number comes from.
        self._fixed: int | None = None
        self._parameter = ""
        self._value: int | None = None
        self._read_at = 0.0

        if not raw.startswith(SSM_PREFIX):
            self._fixed = _whole_number(raw, source="the configured value")

            return

        self._parameter = raw[len(SSM_PREFIX) :].strip()

        if not self._parameter:
            raise TunableValueError(
                f"A {SSM_PREFIX} reference needs a parameter name after it",
            )

    def current(self) -> int:
        """The value now, reading the parameter again once its copy is stale.

        A failed re-read keeps the last good value and says so in the log: an
        SSM blip should not stop every email the model was about to read. Only
        the first read has nothing to fall back on, and that one raises.
        """
        if self._fixed is not None:
            return self._fixed

        now = self._clock()

        if self._value is not None and now - self._read_at < self._max_age_seconds:
            return self._value

        try:
            value = _whole_number(
                self._read_parameter(self._parameter),
                source=f"the parameter at {self._parameter!r}",
            )
        except Exception as error:
            if self._value is None:
                raise TunableValueError(
                    f"Could not read the parameter at {self._parameter!r}",
                ) from error

            _logger.exception(
                "could not refresh a tunable value; keeping the last one",
                extra={"parameter": self._parameter, "value": self._value},
            )
            # Not retried on every call while SSM is down: the stale value is
            # good for another window.
            self._read_at = now

            return self._value

        self._value = value
        self._read_at = now

        return value


def _whole_number(raw: str, *, source: str) -> int:
    text = raw.strip()

    # ASCII on purpose: `"²".isdigit()` is true and `int("²")` raises.
    if not (text.isascii() and text.isdigit()):
        # The value itself stays out of the message: it was read decrypted,
        # and a parameter pointed at the wrong path could be a secret.
        raise TunableValueError(f"Expected a whole number from {source}")

    return int(text)
