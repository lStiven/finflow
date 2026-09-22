"""How many attempts one door allows, and what "too many" means.

Brute force is the one attack this deployment cannot argue with on the merits:
a password, a six-digit code and a webhook secret are all *guessable* given
enough tries, and nothing about the guess itself looks wrong. What separates
an attack from a person who mistyped is **how many times**, so that is what is
counted.

**Fixed windows**, not a sliding log. A sliding window is more accurate and
needs every attempt's timestamp kept; a fixed window needs one integer that a
single atomic add can move, which is the difference between one DynamoDB write
per attempt and a read-modify-write that two Lambdas can race. The cost is the
boundary: somebody can spend a full budget at the end of one window and
another at the start of the next, so the real worst case is twice the number
written here. Every limit below is chosen with that doubling already in mind —
ten failed logins in a moment is still nothing like an attack, and a thousand
still is.

**What is counted is the refusal, not the request.** A login that succeeds
costs nothing and clears what came before it; only a wrong password counts.
That one decision is what makes a shared address — a house, an office, a phone
carrier putting a whole city behind one IP — safe to limit at all: the budget
is only ever spent by people getting it wrong.
"""

from __future__ import annotations

import dataclasses

from personal_finance.shared.domain.value_objects import PosixTime, ValueObject


# A window has to be long enough that spending its budget is a decision and
# short enough that being locked out is not a day. Everything here sits
# between a quarter of an hour and an hour.
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 24 * 60 * 60


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimit(ValueObject):
    """A budget of attempts, and how long it lasts.

    `attempts` is what the *whole* window allows, not a rate per second: five
    in fifteen minutes means the sixth is refused, whether it arrives one
    second or fourteen minutes after the first.
    """

    attempts: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("A rate limit has to allow at least one attempt")

        if not MIN_WINDOW_SECONDS <= self.window_seconds <= MAX_WINDOW_SECONDS:
            raise ValueError(
                f"A window has to be between {MIN_WINDOW_SECONDS} and "
                f"{MAX_WINDOW_SECONDS} seconds",
            )

    def window_of(self, now: PosixTime) -> int:
        """Which window this instant falls in.

        Part of the counter's key, which is what makes a window end without
        anything having to sweep it: the next window is a different key, and
        the old one expires on its own.
        """
        return now.as_epoch_seconds() // self.window_seconds

    def window_ends_at(self, now: PosixTime) -> PosixTime:
        return PosixTime.from_epoch_seconds(
            (self.window_of(now) + 1) * self.window_seconds,
        )

    def judge(self, *, attempt: int, now: PosixTime) -> Verdict:
        """Whether the attempt being made right now fits in the window.

        `attempt` **counts the one being judged**: the third attempt of five
        is `attempt=3`, and it is allowed. Spelling it this way rather than
        as "how many came before" is deliberate — the two callers of this
        arrive with different numbers in hand (one has just counted itself in,
        the other has not), and an off-by-one here is a budget of five that
        allows four, which nobody notices until somebody is locked out one
        attempt early.
        """
        remaining = max(self.attempts - attempt, 0)

        if attempt <= self.attempts:
            return Verdict(allowed=True, remaining=remaining, retry_after_seconds=0)

        return Verdict(
            allowed=False,
            remaining=0,
            # Never zero: a `Retry-After: 0` reads as "go ahead", which is the
            # opposite of what a 429 is saying.
            retry_after_seconds=max(
                1,
                self.window_ends_at(now).as_epoch_seconds() - now.as_epoch_seconds(),
            ),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Verdict(ValueObject):
    """What the limit says about the attempt being made right now."""

    allowed: bool
    remaining: int
    #: How long until the budget is back. Zero while it is still allowed.
    retry_after_seconds: int
