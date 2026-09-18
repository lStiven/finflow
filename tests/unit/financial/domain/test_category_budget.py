"""What a cap may say, and where the bar turns.

Two halves. The refusals exist because each alternative is a cap that is wrong
in a way nobody can see from the screen: a ceiling of nothing that opens every
month already red, a warning point that leaves no amber band, a month spelled
in a way no spending is ever bucketed under.

And the arithmetic, which is the part two screens have to agree on. The state
is decided here rather than in the browser so that the card on the dashboard
and the row on the budgets screen cannot say different things about the same
category on the same day.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.budgets import (
    DEFAULT_WARN_PERCENT,
    EVERY_MONTH,
    BudgetId,
    BudgetState,
    CategoryBudget,
    month_bounds,
    month_containing,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


USER = UserId.new()


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def budget(
    *,
    category: str = "restaurants",
    limit: str = "600000",
    month: str | None = None,
    warn_at: int = DEFAULT_WARN_PERCENT,
) -> CategoryBudget:
    return CategoryBudget.declare(
        user_id=USER,
        category=category,
        limit=money(limit),
        month=month,
        warn_at=warn_at,
    )


class TestDeclaring:
    def test_a_cap_is_its_category_and_its_month(self) -> None:
        """No generated id: two caps on the same category for the same month
        are one cap declared twice."""
        cap = budget(category="groceries", month="2026-09")

        assert cap.id == BudgetId(category="groceries", month="2026-09")
        assert cap.category == "groceries"
        assert cap.month == "2026-09"
        assert cap.user_id == USER

    def test_a_cap_governs_every_month_by_default(self) -> None:
        cap = budget()

        assert cap.month is None
        assert cap.recurring is True

    def test_a_cap_for_one_month_is_not_recurring(self) -> None:
        cap = budget(month="2026-12")

        assert cap.recurring is False

    def test_the_month_key_stands_in_for_all_of_them_in_storage(self) -> None:
        """A sort key cannot be absent, so the nullable month needs one string
        that means «every»."""
        assert budget().id.month_key == EVERY_MONTH
        assert budget(month="2026-09").id.month_key == "2026-09"

    def test_a_users_own_category_is_an_ordinary_value_here(self) -> None:
        """The colon is Merchant's, and Financial reads no meaning into it."""
        cap = budget(category="custom:9f1e4b2c8a7d6e5f4a3b2c1d0e9f8a7b")

        assert cap.category == "custom:9f1e4b2c8a7d6e5f4a3b2c1d0e9f8a7b"

    def test_it_warns_at_eighty_per_cent_unless_told_otherwise(self) -> None:
        cap = budget(limit="600000")

        assert cap.warn_at == 80
        assert cap.warning_at == Decimal("480000")

    def test_declaring_again_replaces_rather_than_merges(self) -> None:
        """A cap and its warning point are one statement. Half an update would
        leave a warning standing against a ceiling it was never set against."""
        first = budget(limit="600000", warn_at=50)
        second = budget(limit="900000")

        assert first.warn_at == 50
        assert second.warn_at == DEFAULT_WARN_PERCENT
        assert second.limit.amount == Decimal("900000")

    def test_a_cap_announces_nothing(self) -> None:
        """A ceiling somebody sets on their own spending is a statement about
        their life. What crossing it would announce is a separate problem, and
        it is derived rather than recorded — see the module docstring."""
        assert budget().pull_events() == []

    def test_the_currency_is_the_caps_own(self) -> None:
        cap = CategoryBudget.declare(
            user_id=USER,
            category="travel",
            limit=money("400", Currency.USD),
        )

        assert cap.currency is Currency.USD


class TestRefusals:
    def test_a_cap_of_nothing_is_not_a_cap(self) -> None:
        """It opens every month already red, and a bar that is never once
        green is a bar nobody reads."""
        with pytest.raises(ValueError, match="for nothing"):
            budget(limit="0")

    def test_a_cap_needs_a_category(self) -> None:
        with pytest.raises(ValueError, match="needs a category"):
            budget(category="   ")

    def test_a_category_cannot_carry_the_key_separator(self) -> None:
        """`#` separates the segments of a sort key, so a category carrying
        one would be two categories that store as a single row."""
        with pytest.raises(ValueError, match="cannot contain"):
            budget(category="food#drink")

    def test_a_category_longer_than_the_key_allows_is_refused(self) -> None:
        with pytest.raises(ValueError, match="64 characters"):
            budget(category="x" * 65)

    def test_a_month_spelled_any_other_way_is_refused_not_coerced(self) -> None:
        """`2026-9` would store as a month no spending is ever bucketed under:
        a cap that governs nothing, which looks exactly like a cap nothing has
        been spent against."""
        for written in ("2026-9", "sep-2026", "2026/09", "2026-13", "2026-00", ""):
            with pytest.raises(ValueError, match="written like"):
                budget(month=written)

    def test_a_month_with_a_trailing_newline_is_refused(self) -> None:
        """Python's `$` matches before a trailing newline, so a `match` here
        would store a month key nothing else in the app spells that way — and
        the router's own validator, which runs first, refuses it."""
        with pytest.raises(ValueError, match="written like"):
            budget(month="2026-09\n")

    def test_warning_at_the_ceiling_leaves_no_amber_band(self) -> None:
        with pytest.raises(ValueError, match="99%"):
            budget(warn_at=100)

    def test_warning_at_nothing_is_the_same_as_not_warning(self) -> None:
        with pytest.raises(ValueError, match="1%"):
            budget(warn_at=0)

    def test_a_negative_warning_point_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1%"):
            budget(warn_at=-10)


class TestProgress:
    def test_under_the_warning_point_it_is_green(self) -> None:
        progress = budget(limit="600000").progress(Decimal("100000"))

        assert progress.state is BudgetState.OK
        assert progress.remaining == Decimal("500000")

    def test_at_the_warning_point_exactly_it_turns_amber(self) -> None:
        """The boundary belongs to the warning: somebody who has spent exactly
        eighty per cent has crossed eighty per cent."""
        progress = budget(limit="600000").progress(Decimal("480000"))

        assert progress.state is BudgetState.WARNING

    def test_one_peso_short_of_the_warning_point_is_still_green(self) -> None:
        progress = budget(limit="600000").progress(Decimal("479999"))

        assert progress.state is BudgetState.OK

    def test_at_the_ceiling_exactly_it_is_over(self) -> None:
        """Spending the whole cap is spending the whole cap — there is nothing
        left, and «queda $0» read as green is the wrong answer."""
        progress = budget(limit="600000").progress(Decimal("600000"))

        assert progress.state is BudgetState.OVER
        assert progress.remaining == Decimal(0)

    def test_going_over_reports_by_how_much_rather_than_flooring(self) -> None:
        progress = budget(limit="600000").progress(Decimal("740000"))

        assert progress.state is BudgetState.OVER
        assert progress.remaining == Decimal("-140000")

    def test_a_high_warning_point_still_goes_red_past_the_ceiling(self) -> None:
        """Over is checked before the warning, so a cap warning at 99 does not
        stay amber once it is passed."""
        progress = budget(limit="600000", warn_at=99).progress(Decimal("600001"))

        assert progress.state is BudgetState.OVER

    def test_nothing_spent_is_green_and_the_whole_cap_remains(self) -> None:
        progress = budget(limit="600000").progress(Decimal(0))

        assert progress.state is BudgetState.OK
        assert progress.spent == Decimal(0)
        assert progress.remaining == Decimal("600000")

    def test_progress_carries_the_caps_own_shape(self) -> None:
        """Flat on purpose: a client reading the ceiling out of one object and
        the state out of another can render the two out of step."""
        progress = budget(category="fuel", limit="300000", month="2026-09").progress(
            Decimal("90000"),
        )

        assert progress.category == "fuel"
        assert progress.currency is Currency.COP
        assert progress.limit == Decimal("300000")
        assert progress.warn_at == DEFAULT_WARN_PERCENT
        assert progress.month == "2026-09"
        assert progress.recurring is False

    def test_cents_survive_the_comparison(self) -> None:
        """Decimal the whole way: a cap compared through a binary float is a
        cap that is over by a hundredth of a cent."""
        progress = budget(limit="100.10").progress(Decimal("80.08"))

        assert progress.remaining == Decimal("20.02")
        assert progress.state is BudgetState.WARNING


class TestTheCalendar:
    """The month vocabulary both the cap and the spending it is compared
    against have to share. Two spellings of "which month" is how a ceiling
    ends up governing a month nothing is ever bucketed under."""

    def test_a_day_names_the_month_it_falls_in(self) -> None:
        assert month_containing(dt.date(2026, 9, 18)) == "2026-09"
        assert month_containing(dt.date(2026, 12, 31)) == "2026-12"

    def test_a_single_digit_month_is_written_with_its_zero(self) -> None:
        assert month_containing(dt.date(2026, 1, 1)) == "2026-01"

    def test_a_month_knows_its_own_first_and_last_day(self) -> None:
        assert month_bounds("2026-09") == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))

    def test_december_rolls_into_the_following_year(self) -> None:
        assert month_bounds("2026-12") == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))

    def test_february_needs_no_table_and_a_leap_year_no_special_case(self) -> None:
        assert month_bounds("2026-02")[1] == dt.date(2026, 2, 28)
        assert month_bounds("2028-02")[1] == dt.date(2028, 2, 29)

    def test_a_month_written_any_other_way_has_no_bounds(self) -> None:
        with pytest.raises(ValueError, match="written like"):
            month_bounds("2026-9")
