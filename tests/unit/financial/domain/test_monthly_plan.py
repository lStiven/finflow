"""What a declared month may say, and what it may not.

Three refusals carry the whole aggregate, and each one exists because the
alternative is a number that is wrong in a way nobody can see: an income of
nothing, a savings target in another currency, and a target bigger than the
income it was set against.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.plan import MonthlyPlan
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


USER = UserId.new()


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


class TestDeclaring:
    def test_a_plan_is_its_owner(self) -> None:
        """No id to generate, and no key anybody could guess their way into."""
        plan = MonthlyPlan.declare(user_id=USER, expected_income=money("5000000"))

        assert plan.id == USER
        assert plan.user_id == USER

    def test_saving_nothing_is_the_ordinary_answer(self) -> None:
        plan = MonthlyPlan.declare(user_id=USER, expected_income=money("5000000"))

        assert plan.savings_target.amount == Decimal(0)
        assert plan.savings_target.currency is Currency.COP
        assert plan.spendable == Decimal("5000000")

    def test_what_is_kept_comes_off_what_can_be_spent(self) -> None:
        plan = MonthlyPlan.declare(
            user_id=USER,
            expected_income=money("5000000"),
            savings_target=money("1200000"),
        )

        assert plan.spendable == Decimal("3800000")

    def test_declaring_again_replaces_rather_than_merges(self) -> None:
        """Two figures, both guesses. Restating the pair is what keeps a
        target from outliving the income it was set against."""
        first = MonthlyPlan.declare(
            user_id=USER,
            expected_income=money("5000000"),
            savings_target=money("1200000"),
        )
        second = MonthlyPlan.declare(user_id=USER, expected_income=money("3000000"))

        assert first.savings_target.amount == Decimal("1200000")
        assert second.savings_target.amount == Decimal(0)

    def test_a_plan_announces_nothing(self) -> None:
        """What somebody expects to earn is a statement about their life, not
        about money that moved."""
        plan = MonthlyPlan.declare(user_id=USER, expected_income=money("5000000"))

        assert plan.pull_events() == []


class TestRefusals:
    def test_an_income_of_nothing_is_not_a_plan(self) -> None:
        """It would make the allowance the negative of the month's spending —
        a true statement about nothing anybody asked."""
        with pytest.raises(ValueError, match="expected income"):
            MonthlyPlan.declare(user_id=USER, expected_income=money("0"))

    def test_a_target_in_another_currency_is_refused_not_converted(self) -> None:
        with pytest.raises(ValueError, match="COP"):
            MonthlyPlan.declare(
                user_id=USER,
                expected_income=money("5000000"),
                savings_target=money("300", Currency.USD),
            )

    def test_keeping_more_than_you_earn_is_refused_at_the_door(self) -> None:
        """Not a warning the screen can act on: it is a plan that says «spend
        nothing and you are still short»."""
        with pytest.raises(ValueError, match="larger than the income"):
            MonthlyPlan.declare(
                user_id=USER,
                expected_income=money("5000000"),
                savings_target=money("6000000"),
            )

    def test_keeping_exactly_what_you_earn_is_allowed(self) -> None:
        plan = MonthlyPlan.declare(
            user_id=USER,
            expected_income=money("5000000"),
            savings_target=money("5000000"),
        )

        assert plan.spendable == Decimal(0)
