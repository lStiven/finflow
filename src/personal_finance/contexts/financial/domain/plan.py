"""What the owner says the month is supposed to look like.

The one number this whole context is building towards — «¿cuánto puedo gastar
hoy?» — cannot be computed from the ledger alone. The ledger knows what has
happened and the bills know what is coming, but neither knows what the owner
expects to earn or how much of it they mean to keep. Those two are statements
about the future, and in this app the future is always **declared, never
discovered**: the same rule that governs accounts, and for the same reason —
a guessed income is a guessed allowance, and an allowance that is wrong once
is a number nobody looks at again.

**Nothing here is money that moved.** A plan writes nothing to the ledger,
moves no balance and publishes no event. It is two figures and a currency.

One plan per person, and one currency in it. Two currencies would need a rate
to subtract one from the other, and this app has no business inventing one —
the same reason net worth, the month's spending and the bills' totals are all
reported per currency and never summed.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Self

from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


@dataclasses.dataclass(eq=False, slots=True)
class MonthlyPlan(AggregateRoot[UserId]):
    """What one person expects to earn in a month, and what they mean to keep.

    Its identity **is** the owner: nobody has two plans, so there is no id to
    generate, no listing to page and no way to ask for somebody else's by
    guessing a key. The row is at a fixed place in that person's partition.

    `savings_target` is a floor, not a transfer. Nothing moves it anywhere —
    it is subtracted from what is spendable so the month's allowance is what
    is left *after* the part that is not meant to be spent. Declaring one is
    optional and zero is the ordinary answer.
    """

    expected_income: Money
    #: Never larger than the income. Read `_within_income` for why that is
    #: refused rather than clamped.
    savings_target: Money
    updated_at: PosixTime = dataclasses.field(default_factory=PosixTime.now)

    @classmethod
    def declare(
        cls,
        *,
        user_id: UserId,
        expected_income: Money,
        savings_target: Money | None = None,
    ) -> Self:
        """State the month, or restate it.

        There is no separate «amend»: a plan has two fields and both are
        guesses that get corrected, so replacing it wholesale is the honest
        operation — and it means a client cannot half-update one into a state
        where the target outlives the income it was set against.

        No event. What somebody plans to earn is a statement about their life,
        not about money that moved, and nothing outside this context has any
        business hearing it.
        """
        target = savings_target or Money(
            amount=Decimal(0),
            currency=expected_income.currency,
        )

        return cls(
            id=user_id,
            expected_income=_earnable(expected_income),
            savings_target=_within_income(target, expected_income),
        )

    @property
    def user_id(self) -> UserId:
        """The owner, spelled out.

        `id` already is the owner, but every other aggregate here reads
        `user_id` and code that has to remember which one this class uses is
        code that will get it wrong.
        """
        return self.id

    @property
    def currency(self) -> Currency:
        return self.expected_income.currency

    @property
    def spendable(self) -> Decimal:
        """What the month has to live on: the income less what is being kept.

        The starting point of the allowance and nothing more — what has
        already been spent and what is still owed come off it in the
        application layer, because both are questions for a repository.
        """
        return self.expected_income.amount - self.savings_target.amount


def _earnable(income: Money) -> Money:
    """`Money` already refuses a negative. Zero is refused here.

    An income of nothing makes the allowance the negative of the month's
    spending, which is a true statement about nothing anybody asked. Somebody
    who earns nothing this month has no plan to declare, and the card is
    supposed to be absent rather than showing a number that only goes down.
    """
    if income.amount == 0:
        raise ValueError("A monthly plan needs an expected income")

    return income


def _within_income(target: Money, income: Money) -> Money:
    """A savings target in the income's own currency, and no bigger than it.

    Refused rather than clamped, both times. A target in another currency
    would need a rate to subtract, and silently treating 200 USD as 200 000
    COP is the kind of wrong that looks right. A target above the income makes
    the allowance negative before a single peso is spent — which is not a
    warning the screen can act on, it is a plan that says "spend nothing and
    you are still short", and the honest answer is to say so at the moment it
    is declared.
    """
    if target.currency is not income.currency:
        raise ValueError(
            f"The plan is in {income.currency.value}, so its savings target "
            f"cannot be in {target.currency.value}",
        )

    if target.amount > income.amount:
        raise ValueError("A savings target cannot be larger than the income")

    return target
