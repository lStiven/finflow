"""Reading and resetting one person's standing with the model.

The parse worker is what changes a standing; these are the two ways anybody
else touches it — the screen asking whether to show the suspension notice,
and an operator reinstating somebody by hand.
"""

from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.application.ports import (
    ModelStandingRepository,
)
from personal_finance.contexts.ingestion.domain.model_standing import (
    ModelAccess,
    ModelStanding,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_REINSTATE_ATTEMPTS = 5


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ModelAccessView:
    access: ModelAccess
    #: Only while paused.
    paused_until: PosixTime | None
    #: Strikes given within the last 90 days.
    strikes: int


def view_of(standing: ModelStanding, *, now: PosixTime) -> ModelAccessView:
    access = standing.access(now)

    return ModelAccessView(
        access=access,
        paused_until=standing.paused_until if access is ModelAccess.PAUSED else None,
        strikes=len(standing.standing_strikes(now)),
    )


class GetModelAccessUseCase:
    def __init__(self, *, standings: ModelStandingRepository) -> None:
        self._standings = standings

    def execute(self, *, user_id: UserId) -> ModelAccessView:
        return view_of(self._standings.get(user_id), now=PosixTime.now())


class ReinstateContentionError(Exception):
    """The record kept changing while it was being reset; run it again."""


class ReinstateModelReadingUseCase:
    """Give somebody the model back, with a clean record.

    An operator's action, never a user's: it is what "comunícate con servicio
    al cliente" ends in.
    """

    def __init__(self, *, standings: ModelStandingRepository) -> None:
        self._standings = standings

    def execute(self, *, user_id: UserId) -> ModelAccessView:
        """Reset the record and return how it stood before."""
        now = PosixTime.now()

        for _ in range(_REINSTATE_ATTEMPTS):
            standing = self._standings.get(user_id)
            before = view_of(standing, now=now)

            if not standing.reinstate() or self._standings.save(standing):
                return before

        raise ReinstateContentionError(
            "This person's record kept changing while it was being reset",
        )
