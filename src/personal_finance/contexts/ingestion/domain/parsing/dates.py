from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from personal_finance.shared.domain.value_objects import PosixTime


class DateParseError(ValueError):
    """Raised when a date and time cannot be read from a bank alert."""


# Colombian banks write local time with no zone marker anywhere in the body.
BOGOTA = ZoneInfo("America/Bogota")

DATE_TIME_PATTERN = r"\d{2}/\d{2}/\d{4}\s+a\s+las\s+\d{1,2}:\d{2}"

_DATE_TIME = re.compile(
    r"^(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})"
    r"\s+a\s+las\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})$",
)


def parse_date_time(raw: str) -> PosixTime:
    """Read `20/08/2026 a las 12:00` as an instant.

    The alert carries no timezone, so the wall clock is interpreted in Bogotá
    and converted to UTC. Reading it as UTC would shift every transaction five
    hours and move late-evening purchases into the next day.
    """
    match = _DATE_TIME.match(raw.strip())

    if match is None:
        raise DateParseError(f"Not a bank alert date: {raw!r}")

    try:
        local = datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            tzinfo=BOGOTA,
        )
    except ValueError as error:
        raise DateParseError(f"Impossible date: {raw!r}") from error

    return PosixTime.from_datetime(local)
