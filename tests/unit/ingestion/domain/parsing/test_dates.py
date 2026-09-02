"""Reading the two date shapes Colombian banks write.

`dd/mm/yyyy hh:mm` on a 24-hour clock (Bancolombia) and `1 de septiembre de
2026` plus `1:07 a.m.` (Lulo bank). Neither carries a timezone, and both mean
Bogotá.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from personal_finance.contexts.ingestion.domain.parsing.dates import (
    DateParseError,
    parse_date_time,
    parse_spanish_date_time,
)
from personal_finance.shared.domain.value_objects import PosixTime


BOGOTA = ZoneInfo("America/Bogota")


def _moment(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
) -> PosixTime:
    return PosixTime.from_datetime(
        datetime(year, month, day, hour, minute, tzinfo=BOGOTA),
    )


def test_a_slashed_date_is_read_in_bogota() -> None:
    assert parse_date_time("20/08/2026 a las 12:00") == _moment(2026, 8, 20, 12, 0)


@pytest.mark.parametrize(
    ("date", "clock", "expected"),
    [
        ("1 de septiembre de 2026", "1:07 a.m.", (2026, 9, 1, 1, 7)),
        ("28 de mayo de 2025", "5:05 p.m.", (2025, 5, 28, 17, 5)),
        # Midnight and noon are the two a naive `+12` gets wrong.
        ("31 de julio de 2025", "12:05 p.m.", (2025, 7, 31, 12, 5)),
        ("31 de julio de 2025", "12:05 a.m.", (2025, 7, 31, 0, 5)),
        # The dots and the space both come and go in the bank's own text.
        ("1 de enero de 2026", "9:30 pm", (2026, 1, 1, 21, 30)),
        ("1 de enero de 2026", "9:30 p. m.", (2026, 1, 1, 21, 30)),
        # Both spellings of September are correct Spanish.
        ("1 de setiembre de 2026", "1:07 a.m.", (2026, 9, 1, 1, 7)),
        ("1 de Diciembre de 2026", "1:07 A.M.", (2026, 12, 1, 1, 7)),
    ],
)
def test_a_spanish_date_and_a_12_hour_clock(
    date: str,
    clock: str,
    expected: tuple[int, int, int, int, int],
) -> None:
    assert parse_spanish_date_time(date, clock) == _moment(*expected)


@pytest.mark.parametrize(
    ("date", "clock"),
    [
        ("1 de septiembre de 2026", "13:07 p.m."),
        ("1 de septiembre de 2026", "0:07 a.m."),
        ("1 de septiembre de 2026", "1:07"),
        ("1 de brumario de 2026", "1:07 a.m."),
        ("31 de febrero de 2026", "1:07 a.m."),
        ("01/09/2026", "1:07 a.m."),
    ],
)
def test_what_cannot_be_read_is_refused(date: str, clock: str) -> None:
    # Never a guess: an unreadable field fails the notification, which is
    # recoverable. A guessed one is a wrong movement nobody can spot.
    with pytest.raises(DateParseError):
        parse_spanish_date_time(date, clock)
