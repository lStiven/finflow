"""Console logging setup shared by the API and every worker's entrypoint.

`logging.basicConfig(level=...)` alone is not enough: its default formatter
only renders `%(message)s` and silently drops whatever a call site put in
`extra={...}` — and every `_logger.info(...)` in this codebase relies on
those fields actually showing up. This is the one place that fixes that, so
`extra` behaves the way every call site already assumes it does.
"""

from __future__ import annotations

import logging


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
    handler = logging.StreamHandler()
    handler.setFormatter(_ExtraFieldsFormatter("%(levelname)s:%(name)s:%(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
