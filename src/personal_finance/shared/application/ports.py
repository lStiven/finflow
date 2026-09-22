from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime


class EventPublisher(Protocol):
    """Port for publishing the domain events an aggregate root accumulated."""

    def publish(self, events: Sequence[Event]) -> None: ...


class AttemptCounter(Protocol):
    """Where the attempts against one door are tallied.

    Shared and durable on purpose. An in-process counter is no counter at all
    here: the API runs as a Lambda behind a Function URL, so a hundred guesses
    can arrive at a hundred cold instances, each of which would see its own
    first attempt. The same argument the ingest pipeline makes about
    deduplication — never in memory — applies with more force to the one
    number that decides whether a password can be brute-forced.

    Reading and recording are separate because the interesting doors only
    count the *failures*: a login has to know the budget before it tries, and
    spend it only if the attempt turns out to be wrong.
    """

    def spent(self, bucket: str) -> int:
        """How much of this bucket's budget is gone.

        Zero for a bucket nobody has touched, which is the ordinary answer.
        """
        ...

    def record(self, *, bucket: str, expires_at: PosixTime) -> int:
        """Count one attempt and answer the new total.

        Atomic: two requests arriving together must not both read three and
        both write four. `expires_at` is set when the bucket is created and
        never moved, so a window ends when it was always going to end rather
        than being pushed forward by the attempt that filled it.
        """
        ...

    def clear(self, bucket: str) -> None:
        """Forget this bucket's attempts.

        What a correct password does to the failures before it. Without it,
        somebody who mistypes four times and then gets in would still be four
        down for the rest of the window — the surest way to lock out exactly
        the person the limit exists to protect.
        """
        ...
