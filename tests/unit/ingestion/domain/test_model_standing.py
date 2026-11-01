"""The abuse rule: ten emails that are not from a bank, arriving within an
hour, are a strike — a day's pause, then three, then the end.
"""

import datetime
import uuid

from personal_finance.contexts.ingestion.domain.events import ModelReadingSuspended
from personal_finance.contexts.ingestion.domain.model_standing import (
    ModelAccess,
    ModelStanding,
    OffenseOutcome,
)
from personal_finance.contexts.ingestion.domain.value_objects import NotificationId
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
START = PosixTime.from_datetime(
    datetime.datetime(2026, 10, 31, 9, 0, tzinfo=datetime.UTC),
)


def _at(*, days: float = 0, minutes: float = 0) -> PosixTime:
    return PosixTime.from_datetime(
        START.to_datetime() + datetime.timedelta(days=days, minutes=minutes),
    )


def _offend(
    standing: ModelStanding,
    *,
    received_at: PosixTime,
    now: PosixTime | None = None,
    notification_id: NotificationId | None = None,
) -> bool:
    outcome = standing.record_offense(
        notification_id=notification_id or NotificationId(value=uuid.uuid4()),
        received_at=received_at,
        now=now or received_at,
    )

    return outcome is OffenseOutcome.STRUCK


def _burst(standing: ModelStanding, *, days: float = 0, count: int = 10) -> bool:
    """`count` offenses a minute apart; whether the last one struck."""
    struck = False

    for minute in range(count):
        struck = _offend(standing, received_at=_at(days=days, minutes=minute))

    return struck


def _suspensions(standing: ModelStanding) -> list[ModelReadingSuspended]:
    return [
        event
        for event in standing.pull_events()
        if isinstance(event, ModelReadingSuspended)
    ]


def test_a_clean_record_leaves_the_model_open() -> None:
    assert ModelStanding.clean(USER_ID).access(START) is ModelAccess.OPEN


def test_nine_in_an_hour_is_not_a_strike() -> None:
    standing = ModelStanding.clean(USER_ID)

    assert _burst(standing, count=9) is False
    assert standing.access(_at(minutes=9)) is ModelAccess.OPEN
    assert _suspensions(standing) == []


def test_the_tenth_in_an_hour_pauses_the_model_for_a_day() -> None:
    standing = ModelStanding.clean(USER_ID)

    assert _burst(standing) is True

    [event] = _suspensions(standing)
    assert event.strike == 1
    assert event.offenses == 10
    assert event.paused_until == _at(days=1, minutes=9)
    assert standing.access(_at(minutes=10)) is ModelAccess.PAUSED
    assert standing.access(_at(days=1, minutes=9)) is ModelAccess.OPEN


def test_ten_spread_over_more_than_an_hour_are_not_a_strike() -> None:
    standing = ModelStanding.clean(USER_ID)

    for index in range(10):
        # Seven minutes apart: the first and the tenth are 63 minutes apart.
        _offend(standing, received_at=_at(minutes=7 * index))

    assert standing.access(_at(minutes=70)) is ModelAccess.OPEN


def test_arrival_decides_the_burst_not_when_it_was_read() -> None:
    """A backlog drained at once is not a burst if the mail did not arrive as
    one — and a burst read slowly, out of order, still is one."""
    spread = ModelStanding.clean(USER_ID)
    for index in range(10):
        _offend(spread, received_at=_at(minutes=10 * index), now=_at(minutes=200))
    assert spread.access(_at(minutes=200)) is ModelAccess.OPEN

    burst = ModelStanding.clean(USER_ID)
    for index in reversed(range(10)):
        _offend(burst, received_at=_at(minutes=index), now=_at(minutes=90 - index))
    assert burst.access(_at(minutes=91)) is ModelAccess.PAUSED


def test_the_same_email_read_twice_is_one_offense() -> None:
    standing = ModelStanding.clean(USER_ID)
    repeated = NotificationId(value=uuid.uuid4())

    for minute in range(10):
        _offend(standing, received_at=_at(minutes=minute), notification_id=repeated)

    assert standing.access(_at(minutes=10)) is ModelAccess.OPEN


def test_the_second_strike_pauses_for_three_days_and_the_third_ends_it() -> None:
    standing = ModelStanding.clean(USER_ID)

    _burst(standing)
    _burst(standing, days=2)
    second = _suspensions(standing)[-1]
    _burst(standing, days=6)
    third = _suspensions(standing)[-1]

    assert second.strike == 2
    assert second.paused_until == _at(days=5, minutes=9)
    assert third.strike == 3
    assert third.paused_until is None
    assert standing.access(_at(days=400)) is ModelAccess.REVOKED


def test_nothing_counts_while_the_model_is_paused() -> None:
    standing = ModelStanding.clean(USER_ID)
    _burst(standing)

    # Read during the pause: emails that were already in flight.
    assert _burst(standing, days=0.5) is False
    assert standing.offenses == {}

    # After it ends, a fresh burst is the second strike, not a continuation.
    assert _burst(standing, days=2) is True
    assert _suspensions(standing)[-1].strike == 2


def test_a_strike_stops_counting_ninety_days_after_it_was_given() -> None:
    standing = ModelStanding.clean(USER_ID)
    _burst(standing)

    _burst(standing, days=91)

    assert _suspensions(standing)[-1].strike == 1
    assert len(standing.strikes) == 1


def test_offenses_older_than_two_days_are_forgotten() -> None:
    standing = ModelStanding.clean(USER_ID)
    _burst(standing, count=5)

    _offend(standing, received_at=_at(days=3))

    assert len(standing.offenses) == 1


def test_reinstating_starts_over() -> None:
    standing = ModelStanding.clean(USER_ID)
    for days in (0, 2, 6):
        _burst(standing, days=days)

    assert standing.reinstate() is True
    assert standing.access(_at(days=7)) is ModelAccess.OPEN
    assert standing.strikes == ()

    _burst(standing, days=8)
    assert _suspensions(standing)[-1].strike == 1


def test_reinstating_a_clean_record_changes_nothing() -> None:
    assert ModelStanding.clean(USER_ID).reinstate() is False


def test_what_changed_nothing_says_so() -> None:
    standing = ModelStanding.clean(USER_ID)
    repeated = NotificationId(value=uuid.uuid4())

    first = standing.record_offense(
        notification_id=repeated, received_at=START, now=START
    )
    again = standing.record_offense(
        notification_id=repeated, received_at=START, now=START
    )

    assert first is OffenseOutcome.COUNTED
    assert again is OffenseOutcome.IGNORED


def test_only_strikes_within_ninety_days_stand() -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.strikes = (_at(days=-120), _at(days=-10))

    assert standing.standing_strikes(START) == (_at(days=-10),)
