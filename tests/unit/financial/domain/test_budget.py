"""What a budget may say, what it watches, and where the bar turns.

Three halves, which is one more than this file used to have.

The **refusals** exist because each alternative is a budget that is wrong in a
way nobody can see from the screen: a ceiling of nothing that opens every month
already red, a warning point that leaves no amber band, a month spelled in a
way no spending is ever bucketed under.

The **scope** is new, and so is the rule it replaced. A budget used to *be* its
category, so two on the same category were one. Now they are two, on purpose —
«Salidas» and «Restaurantes» overlap because somebody meant them to.

And the **arithmetic**, which is the part two screens have to agree on. The
state is decided here rather than in the browser so the card on the dashboard
and the row on the budgets screen cannot say different things on the same day.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.budgets import (
    DEFAULT_WARN_PERCENT,
    MAX_SCOPE_CATEGORIES,
    Budget,
    BudgetId,
    BudgetScope,
    BudgetState,
    month_bounds,
    month_containing,
)
from personal_finance.contexts.financial.domain.value_objects import AccountId
from personal_finance.shared.domain.value_objects import Currency, Money, UserId


USER = UserId.new()


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def budget(
    *,
    name: str = "Restaurantes",
    limit: str = "600000",
    categories: frozenset[str] | None = frozenset({"restaurants"}),
    accounts: frozenset[AccountId] | None = None,
    icon: str = "",
    month: str | None = None,
    warn_at: int = DEFAULT_WARN_PERCENT,
) -> Budget:
    return Budget.declare(
        user_id=USER,
        name=name,
        limit=money(limit),
        scope=BudgetScope.of(categories=categories, accounts=accounts),
        icon=icon,
        month=month,
        warn_at=warn_at,
    )


class TestDeclaring:
    def test_each_budget_gets_its_own_identity(self) -> None:
        """The reversal this iteration is built on.

        Two budgets over the same category used to be one cap declared twice.
        They are two now, because a scope can overlap another on purpose.
        """
        one = budget(name="Restaurantes")
        other = budget(name="Salidas", categories=frozenset({"restaurants", "bars"}))

        assert one.id != other.id
        assert isinstance(one.id, BudgetId)

    def test_a_name_is_required(self) -> None:
        """New, and forced by the scope: «Salidas» is not derivable from
        restaurants, bars and delivery."""
        with pytest.raises(ValueError, match="needs a name"):
            budget(name="   ")

    def test_a_name_is_trimmed(self) -> None:
        assert budget(name="  Salidas  ").name == "Salidas"

    def test_a_name_has_a_ceiling(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            budget(name="x" * 41)

    def test_an_icon_is_optional(self) -> None:
        """Empty means «pick one for me», which is a question about drawings."""
        assert budget(icon="").icon == ""

    def test_an_icon_is_a_slug(self) -> None:
        assert budget(icon="shopping-bag").icon == "shopping-bag"

    @pytest.mark.parametrize("value", ["Shopping Bag", "9lives", "emoji🍔", "a_b"])
    def test_an_icon_that_is_not_a_slug_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="written like"):
            budget(icon=value)

    def test_a_ceiling_of_nothing_is_refused(self) -> None:
        """Every month would open already over, and a bar that is never once
        green is a bar nobody reads."""
        with pytest.raises(ValueError, match="cannot be for nothing"):
            budget(limit="0")

    @pytest.mark.parametrize("warn_at", [0, 100, -5, 140])
    def test_a_warning_point_outside_the_cap_is_refused(self, warn_at: int) -> None:
        with pytest.raises(ValueError, match="warns between"):
            budget(warn_at=warn_at)

    @pytest.mark.parametrize("month", ["2026-9", "sep-2026", "2026-13", "2026-09\n"])
    def test_a_month_nothing_is_bucketed_under_is_refused(self, month: str) -> None:
        """`2026-09\\n` is the one `match` would have let through: Python's `$`
        also matches before a trailing newline."""
        with pytest.raises(ValueError, match="written like 2026-09"):
            budget(month=month)

    def test_a_budget_governs_every_month_by_default(self) -> None:
        assert budget().recurring is True
        assert budget().month is None

    def test_a_month_of_its_own_still_governs_only_that_month(self) -> None:
        december = budget(month="2026-12")

        assert december.recurring is False
        assert december.governs("2026-12") is True
        assert december.governs("2026-11") is False

    def test_a_recurring_budget_governs_any_month(self) -> None:
        assert budget().governs("2026-01") is True
        assert budget().governs("2030-07") is True


class TestScope:
    def test_an_empty_scope_watches_everything(self) -> None:
        scope = BudgetScope.everything()

        assert scope.total is True
        assert scope.every_account is True

    def test_a_total_budget_counts_spending_no_merchant_owns(self) -> None:
        """`None` is unknown, not `uncategorized`. It is money that left, so a
        cap over everything has to count it."""
        assert BudgetScope.everything().watches_category(None) is True

    def test_a_scoped_budget_cannot_count_the_unknown_bucket(self) -> None:
        """Nobody has said which of its categories that spending belongs to."""
        scope = BudgetScope.of(categories=frozenset({"restaurants"}))

        assert scope.watches_category(None) is False

    def test_a_scoped_budget_counts_only_what_it_names(self) -> None:
        scope = BudgetScope.of(categories=frozenset({"restaurants", "bars"}))

        assert scope.watches_category("restaurants") is True
        assert scope.watches_category("bars") is True
        assert scope.watches_category("groceries") is False

    def test_the_two_axes_are_independent(self) -> None:
        """«Restaurantes, pero solo lo de la tarjeta» is one budget."""
        card = AccountId.new()
        scope = BudgetScope.of(
            categories=frozenset({"restaurants"}),
            accounts=frozenset({card}),
        )

        assert scope.total is False
        assert scope.every_account is False
        assert scope.accounts == frozenset({card})

    def test_a_category_that_cannot_be_a_key_is_refused(self) -> None:
        """`#` separates the segments of a sort key."""
        with pytest.raises(ValueError, match="cannot contain '#'"):
            BudgetScope.of(categories=frozenset({"food#drink"}))

    def test_a_category_with_no_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="with no name"):
            BudgetScope.of(categories=frozenset({"   "}))

    def test_categories_are_trimmed_and_deduplicated(self) -> None:
        scope = BudgetScope.of(categories=frozenset({"restaurants", " restaurants "}))

        assert scope.categories == frozenset({"restaurants"})

    def test_gathering_too_many_categories_is_refused(self) -> None:
        """A cap over twenty categories is a cap over everything, which an
        empty scope already says more cheaply."""
        many = frozenset(
            f"category-{index}" for index in range(MAX_SCOPE_CATEGORIES + 1)
        )

        with pytest.raises(ValueError, match="at most"):
            BudgetScope.of(categories=many)


class TestAmending:
    def test_amending_keeps_the_identity(self) -> None:
        """The whole point of the generated id: editing used to mean writing a
        second row under a new identity."""
        existing = budget(name="Restaurantes", limit="600000")
        before = existing.id

        existing.amend(
            name="Salidas",
            limit=money("900000"),
            scope=BudgetScope.of(categories=frozenset({"restaurants", "bars"})),
            icon="beer",
            month="2026-12",
            warn_at=70,
        )

        assert existing.id == before
        assert existing.name == "Salidas"
        assert existing.limit == money("900000")
        assert existing.scope.categories == frozenset({"restaurants", "bars"})
        assert existing.icon == "beer"
        assert existing.month == "2026-12"
        assert existing.warn_at == 70

    def test_amending_refuses_what_declaring_refuses(self) -> None:
        """One rule, not two: the validators are the same functions."""
        existing = budget()

        with pytest.raises(ValueError, match="cannot be for nothing"):
            existing.amend(
                name="Salidas",
                limit=money("0"),
                scope=BudgetScope.everything(),
                icon="",
                month=None,
                warn_at=80,
            )


class TestProgress:
    def test_under_the_warning_point_is_green(self) -> None:
        progress = budget(limit="600000").progress(Decimal("100000"))

        assert progress.state is BudgetState.OK
        assert progress.remaining == Decimal("500000")

    def test_at_the_warning_point_is_amber(self) -> None:
        """Eighty per cent of 600.000 is 480.000, and the bar turns *at* it."""
        progress = budget(limit="600000").progress(Decimal("480000"))

        assert progress.state is BudgetState.WARNING

    def test_at_the_ceiling_is_red(self) -> None:
        progress = budget(limit="600000").progress(Decimal("600000"))

        assert progress.state is BudgetState.OVER
        assert progress.remaining == Decimal(0)

    def test_over_the_ceiling_reports_by_how_much(self) -> None:
        """Never floored at zero: somebody who went over needs the number."""
        progress = budget(limit="600000").progress(Decimal("750000"))

        assert progress.state is BudgetState.OVER
        assert progress.remaining == Decimal("-150000")

    def test_a_warning_point_of_99_still_goes_red_past_the_ceiling(self) -> None:
        """Over is checked first, so the amber band never swallows red."""
        progress = budget(limit="1000", warn_at=99).progress(Decimal("1000"))

        assert progress.state is BudgetState.OVER

    def test_the_warning_amount_is_derived(self) -> None:
        """A stored copy would be the one that survives moving either number."""
        assert budget(limit="600000", warn_at=80).warning_at == Decimal("480000")

    def test_progress_carries_what_the_screen_draws(self) -> None:
        """Flat on purpose: a client reading the ceiling out of one object and
        the state out of another can render the two out of step."""
        cap = budget(name="Salidas", icon="beer", limit="600000")
        progress = cap.progress(Decimal("100000"))

        assert progress.budget_id == cap.id
        assert progress.name == "Salidas"
        assert progress.icon == "beer"
        assert progress.currency is Currency.COP
        assert progress.limit == Decimal("600000")
        assert progress.spent == Decimal("100000")
        assert progress.recurring is True


class TestMonths:
    def test_a_day_names_its_month(self) -> None:
        assert month_containing(dt.date(2026, 9, 18)) == "2026-09"

    def test_a_month_bounds_both_ends_inclusively(self) -> None:
        assert month_bounds("2026-09") == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))

    def test_december_rolls_the_year(self) -> None:
        assert month_bounds("2026-12") == (dt.date(2026, 12, 1), dt.date(2026, 12, 31))

    def test_february_needs_no_table(self) -> None:
        assert month_bounds("2024-02") == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))
        assert month_bounds("2026-02") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
