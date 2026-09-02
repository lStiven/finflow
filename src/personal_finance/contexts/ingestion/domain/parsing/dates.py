from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

from personal_finance.shared.domain.value_objects import PosixTime


class DateParseError(ValueError):
    """Raised when a date and time cannot be read from a bank alert."""


# Colombian banks write local time with no zone marker anywhere in the body.
BOGOTA = ZoneInfo("America/Bogota")

# `a las` is optional because the same bank writes both: "el 20/08/2026 a las
# 12:00" on a purchase and "el 21/05/2026 16:30" on a card payment. Requiring
# it made the second one unreadable — which is a deferred alert, not a wrong
# one, but it is still a movement nobody sees.
DATE_TIME_PATTERN = r"\d{2}/\d{2}/\d{4}(?:\s+a\s+las)?\s+\d{1,2}:\d{2}"

_DATE_TIME = re.compile(
    r"^(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})"
    r"(?:\s+a\s+las)?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})$",
)


def parse_date_time(raw: str) -> PosixTime:
    """Read `20/08/2026 a las 12:00` — or `21/05/2026 16:30` — as an instant.

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


_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    # Both spellings are correct Spanish and neither is rare in print.
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Lulo bank writes the moment as two labelled fields — "Fecha 1 de septiembre
# de 2026 Hora 1:07 a.m." — with prose between them in some templates, so
# these are two patterns rather than one: a single pattern would have to
# guess what may sit in the middle.
#
# Both recognize only what this module can then read. That is the difference
# between an unknown wording and a broken alert: a template that matches and
# *then* fails marks the notification FAILED and the movement is lost, while a
# template that does not match at all falls through to the LLM. `1 de sep de
# 2026` and a 13 o'clock belong on the second path.
SPANISH_DATE_PATTERN = (
    rf"\d{{1,2}}\s+de\s+(?:{'|'.join(_SPANISH_MONTHS)})\s+de\s+\d{{4}}"
)
# `a.m.`/`p.m.` as the bank writes it, which is not consistently: the dots and
# the space between the letters both come and go.
CLOCK_12H_PATTERN = r"(?:0?[1-9]|1[0-2]):\d{2}\s*[ap]\.?\s*m\.?"

_SPANISH_DATE = re.compile(
    r"^(?P<day>\d{1,2})\s+de\s+(?P<month>[^\W\d_]+)\s+de\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
_CLOCK_12H = re.compile(
    r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<meridiem>[ap])\.?\s*m\.?$",
    re.IGNORECASE,
)


def parse_spanish_date_time(date: str, clock: str) -> PosixTime:
    """Read `1 de septiembre de 2026` plus `1:07 a.m.` as an instant.

    Same timezone rule as `parse_date_time`: the alert carries no zone, the
    wall clock is Bogotá's, and the result is UTC.
    """
    date_match = _SPANISH_DATE.match(date.strip())

    if date_match is None:
        raise DateParseError(f"Not a bank alert date: {date!r}")

    month = _SPANISH_MONTHS.get(date_match.group("month").lower())

    if month is None:
        raise DateParseError(f"Not a Spanish month: {date!r}")

    clock_match = _CLOCK_12H.match(clock.strip())

    if clock_match is None:
        raise DateParseError(f"Not a bank alert time: {clock!r}")

    # Outside the `try`: this raises its own, more precise error, and the
    # handler below would relabel it "impossible date".
    hour = _to_24_hour(
        hour=int(clock_match.group("hour")),
        meridiem=clock_match.group("meridiem").lower(),
    )

    try:
        local = datetime(
            year=int(date_match.group("year")),
            month=month,
            day=int(date_match.group("day")),
            hour=hour,
            minute=int(clock_match.group("minute")),
            tzinfo=BOGOTA,
        )
    except ValueError as error:
        raise DateParseError(f"Impossible date: {date!r} {clock!r}") from error

    return PosixTime.from_datetime(local)


def _to_24_hour(*, hour: int, meridiem: str) -> int:
    """Midnight and noon are the two the naive `+12` gets wrong.

    `12:05 a.m.` is five past midnight and `12:05 p.m.` is five past noon, so
    twelve is the hour that does not shift the way the other eleven do.
    """
    if not 1 <= hour <= 12:
        raise DateParseError(f"Not an hour on a 12-hour clock: {hour}")

    if meridiem == "a":
        return 0 if hour == 12 else hour

    return 12 if hour == 12 else hour + 12
