"""What the screen is told about somebody's standing, and how an operator
gives the model back."""

import copy
import datetime

import pytest

from personal_finance.contexts.ingestion.application.model_access import (
    GetModelAccessUseCase,
    ReinstateContentionError,
    ReinstateModelReadingUseCase,
    view_of,
)
from personal_finance.contexts.ingestion.domain.model_standing import (
    ModelAccess,
    ModelStanding,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_datetime(datetime.datetime(2026, 10, 31, 12, tzinfo=datetime.UTC))


def _ago(days: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() - datetime.timedelta(days=days))


class Standings:
    def __init__(
        self,
        standing: ModelStanding | None = None,
        *,
        conflicts: int = 0,
    ) -> None:
        self.stored = copy.deepcopy(standing)
        self.conflicts = conflicts
        self.saves = 0

    def get(self, user_id: UserId) -> ModelStanding:
        return copy.deepcopy(self.stored) or ModelStanding.clean(user_id)

    def save(self, standing: ModelStanding) -> bool:
        if self.conflicts:
            self.conflicts -= 1

            return False

        self.saves += 1
        self.stored = copy.deepcopy(standing)

        return True


def test_a_clean_record_is_open() -> None:
    view = GetModelAccessUseCase(standings=Standings()).execute(user_id=USER_ID)

    assert view.access is ModelAccess.OPEN
    assert view.paused_until is None
    assert view.strikes == 0


def test_a_pause_says_until_when_and_counts_only_standing_strikes() -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.strikes = (_ago(120), _ago(1))
    standing.paused_until = PosixTime.from_datetime(
        NOW.to_datetime() + datetime.timedelta(days=2),
    )

    view = view_of(standing, now=NOW)

    assert view.access is ModelAccess.PAUSED
    assert view.paused_until == standing.paused_until
    assert view.strikes == 1


def test_a_pause_that_ended_shows_no_date() -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.paused_until = _ago(1)

    view = view_of(standing, now=NOW)

    assert view.access is ModelAccess.OPEN
    assert view.paused_until is None


def test_reinstating_clears_the_record_and_reports_how_it_stood() -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.revoked = True
    standing.strikes = (_ago(10), _ago(5), _ago(1))
    standings = Standings(standing)

    before = ReinstateModelReadingUseCase(standings=standings).execute(user_id=USER_ID)

    assert before.access is ModelAccess.REVOKED
    assert before.strikes == 3
    assert standings.get(USER_ID).access(NOW) is ModelAccess.OPEN
    assert standings.get(USER_ID).strikes == ()


def test_reinstating_somebody_with_nothing_to_clear_writes_nothing() -> None:
    standings = Standings()

    ReinstateModelReadingUseCase(standings=standings).execute(user_id=USER_ID)

    assert standings.saves == 0


def test_a_record_that_keeps_changing_is_reported_not_overwritten() -> None:
    standing = ModelStanding.clean(USER_ID)
    standing.revoked = True

    with pytest.raises(ReinstateContentionError):
        ReinstateModelReadingUseCase(
            standings=Standings(standing, conflicts=100),
        ).execute(user_id=USER_ID)
