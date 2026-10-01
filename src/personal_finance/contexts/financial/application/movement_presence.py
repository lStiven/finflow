"""Which of some movements still exist — published for another context.

Alerts keeps a copy of every alert for the app's inbox, and an alert about a
movement its owner has since erased is an alert about nothing: opening it
leads to a screen that cannot load. Financial owns the ledger, so it is the
one asked, by id, at the moment the inbox is read — which stays right however
the erasure and the alert happen to be ordered, with nothing to publish and
nothing to keep in step.
"""

from __future__ import annotations

from collections.abc import Sequence

from personal_finance.contexts.financial.application.ports import ChargeLookup
from personal_finance.shared.domain.value_objects import UserId


class ExistingMovementsUseCase:
    def __init__(self, *, ledger: ChargeLookup) -> None:
        self._ledger = ledger

    def execute(
        self, *, user_id: UserId, movement_ids: Sequence[str]
    ) -> frozenset[str]:
        """The ids among these that name one of this user's movements.

        Somebody else's movement answers as missing, exactly as the ledger's
        own reads do: whether it exists is not this user's to learn.
        """
        if not movement_ids:
            return frozenset()

        return frozenset(
            self._ledger.find_many(
                user_id=user_id,
                movement_ids=list(dict.fromkeys(movement_ids)),
            ),
        )
