"""Console logging setup shared by the API and every worker's entrypoint.

`logging.basicConfig(level=...)` alone is not enough: its default formatter
only renders `%(message)s` and silently drops whatever a call site put in
`extra={...}` — and every `_logger.info(...)` in this codebase relies on
those fields actually showing up. This is the one place that fixes that, so
`extra` behaves the way every call site already assumes it does.

It is also where third-party loggers that would print a secret are turned
down. See `SILENCED_LOGGERS`.
"""

from __future__ import annotations

import logging


# Libraries that log a request URL, and would therefore log a credential.
#
# `httpx` writes `HTTP Request: POST <url> "HTTP/1.1 400"` at INFO for every
# call, and Telegram's API puts the bot token *in the path*:
# `api.telegram.org/bot<TOKEN>/sendMessage`. So the one line the alerts
# adapter takes such care never to write, httpx writes for it — on every
# alert, into CloudWatch, where it stays for the log group's retention and
# lets whoever reads it message every linked user as this deployment.
#
# `httpcore` is here for the same reason one level down: its DEBUG records
# carry the request target, which is the same path.
#
# Pinned rather than left to the root level, because the root level is the
# thing somebody turns up to DEBUG while chasing an unrelated bug.
SILENCED_LOGGERS = ("httpx", "httpcore")

# What a stock `LogRecord` already carries, so only a call site's own
# `extra` keys get appended.
_STANDARD_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message"}


class _ExtraFieldsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRS
        }

        if not extras:
            return rendered

        fields = " ".join(f"{key}={value!r}" for key, value in extras.items())

        return f"{rendered} | {fields}"


def configure_logging(*, level: int = logging.INFO) -> None:
    """Install the formatter above as the root logger's only handler.

    `force=True` is what makes this work on Lambda, and it is not optional
    there. The managed Python runtime attaches its own handler to the root
    logger before any of this package is imported, and `basicConfig` is
    documented to do nothing at all when the root logger already has one — no
    error, no warning. Without `force` both arguments below are discarded: the
    formatter never gets installed, and the level stays at the root logger's
    WARNING default, which silently drops every `_logger.info(...)` in the
    codebase. Every worker then looks idle in CloudWatch while doing its job,
    and only failures are visible.

    Off Lambda the root logger has no handlers yet, so `force` changes
    nothing. Uvicorn is unaffected either way: it configures its own named
    loggers, never the root one.

    The loop at the end is not about noise. Those loggers print request URLs,
    and one of the URLs this codebase builds carries a bot token — see
    `SILENCED_LOGGERS`.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(_ExtraFieldsFormatter("%(levelname)s:%(name)s:%(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)

    for name in SILENCED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
