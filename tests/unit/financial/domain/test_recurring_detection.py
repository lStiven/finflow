"""The rhythms the detector has to recognise, and the ones it must refuse.

Every cadence in the plan's table gets its own case, built from dates a bank
would actually produce rather than from arithmetic — Netflix on the 15th has
gaps of 30, 31 and 31 days, and a detector measuring days alone calls the most
regular charge a person has irregular.

The refusals matter as much as the finds. A false negative costs a suggestion
nobody sees; a false positive tells somebody to declare a bill for three
visits to a restaurant, and then carries that into every forecast they read.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.bills import BillCadence
from personal_finance.contexts.financial.domain.recurring import (
    RecurringSeries,
    SeriesState,
    Sighting,
    detect_series,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    MovementDirection,
)
from personal_finance.shared.domain.value_objects import Currency, Money


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def seen(
    *days: str,
    amount: str = "44900",
    account_id: AccountId | None = None,
) -> list[Sighting]:
    return [
        Sighting(
            occurred_on=dt.date.fromisoformat(day),
            amount=money(amount),
            account_id=account_id,
        )
        for day in days
    ]


def detect(
    sightings: Sequence[Sighting],
    *,
    today: str,
    direction: MovementDirection = MovementDirection.OUTGOING,
) -> RecurringSeries | None:
    return detect_series(
        sightings,
        direction=direction,
        today=dt.date.fromisoformat(today),
    )


class TestCadence:
    def test_monthly_is_the_same_day_of_the_month_not_thirty_days(self) -> None:
        """The figure in the plan: gaps of 30, 31 and 31, all on the 15th."""
        series = detect(
            seen("2026-06-15", "2026-07-15", "2026-08-15", "2026-09-15"),
            today="2026-09-16",
        )

        assert series is not None
        assert series.cadence is BillCadence.MONTHLY
        assert series.missed == 0
        assert series.next_due_on == dt.date(2026, 10, 15)

    def test_monthly_anchored_on_the_end_of_the_month_survives_february(self) -> None:
        series = detect(
            seen("2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"),
            today="2026-05-01",
        )

        assert series is not None
        assert series.cadence is BillCadence.MONTHLY
        # The anchor's day comes back the moment the month is long enough,
        # which is `BillCadence`'s rule and not a second one.
        assert series.next_due_on == dt.date(2026, 5, 31)

    def test_weekly(self) -> None:
        series = detect(
            seen("2026-08-01", "2026-08-08", "2026-08-15", "2026-08-22"),
            today="2026-08-23",
        )

        assert series is not None
        assert series.cadence is BillCadence.WEEKLY

    def test_a_fortnightly_salary_is_not_a_weekly_one_that_misses(self) -> None:
        """The 15th and the 30th: gaps of 15 and 16 days, never 14.

        It fits weekly too, as a weekly series missing every other week. What
        tells them apart is which cadence has to invent the least.
        """
        series = detect(
            seen("2026-06-15", "2026-06-30", "2026-07-15", "2026-07-30", "2026-08-14"),
            today="2026-08-15",
            direction=MovementDirection.INCOMING,
        )

        assert series is not None
        assert series.cadence is BillCadence.BIWEEKLY
        assert series.missed == 0
        assert series.direction is MovementDirection.INCOMING

    def test_bimonthly(self) -> None:
        series = detect(
            seen("2026-03-10", "2026-05-11", "2026-07-09", "2026-09-10"),
            today="2026-09-11",
        )

        assert series is not None
        assert series.cadence is BillCadence.BIMONTHLY

    def test_quarterly(self) -> None:
        series = detect(
            seen("2025-12-05", "2026-03-06", "2026-06-05", "2026-09-04"),
            today="2026-09-10",
        )

        assert series is not None
        assert series.cadence is BillCadence.QUARTERLY

    def test_annual_tolerates_a_fortnight_of_drift(self) -> None:
        series = detect(
            seen("2024-03-05", "2025-03-14", "2026-03-20"),
            today="2026-04-01",
        )

        assert series is not None
        assert series.cadence is BillCadence.ANNUAL
        assert series.next_due_on == dt.date(2027, 3, 5)

    def test_three_annual_charges_span_two_years_and_are_still_one_series(
        self,
    ) -> None:
        """Which is why the history window is two years and not thirteen
        months: three sightings of something annual do not fit in one."""
        series = detect(
            seen("2024-03-05", "2025-03-06", "2026-03-04"),
            today="2026-03-10",
        )

        assert series is not None
        assert series.cadence is BillCadence.ANNUAL
        assert series.state is SeriesState.ACTIVE

    def test_a_missed_month_is_counted_not_refused(self) -> None:
        series = detect(
            seen("2026-05-04", "2026-06-04", "2026-08-04", "2026-09-04"),
            today="2026-09-05",
        )

        assert series is not None
        assert series.cadence is BillCadence.MONTHLY
        assert series.missed == 1


class TestRefusals:
    def test_two_sightings_are_never_a_series(self) -> None:
        assert detect(seen("2026-08-15", "2026-09-15"), today="2026-09-16") is None

    def test_three_unrelated_visits_are_not_a_subscription(self) -> None:
        assert (
            detect(seen("2026-07-02", "2026-07-19", "2026-09-03"), today="2026-09-10")
            is None
        )

    def test_one_stray_charge_costs_the_series_rather_than_bending_it(self) -> None:
        """Strict on purpose: the suggestion is cheap, the false one is not."""
        assert (
            detect(
                seen("2026-06-15", "2026-07-15", "2026-07-22", "2026-08-15"),
                today="2026-08-16",
            )
            is None
        )

    def test_a_gap_of_four_periods_is_two_stretches_not_one_rhythm(self) -> None:
        assert (
            detect(
                seen("2026-01-10", "2026-02-10", "2026-06-10"),
                today="2026-06-11",
            )
            is None
        )

    def test_mixing_currencies_is_refused_rather_than_averaged(self) -> None:
        sightings = [
            Sighting(occurred_on=dt.date(2026, 7, 5), amount=money("30000")),
            Sighting(
                occurred_on=dt.date(2026, 8, 5),
                amount=money("30", Currency.USD),
            ),
            Sighting(occurred_on=dt.date(2026, 9, 5), amount=money("30000")),
        ]

        with pytest.raises(ValueError, match="currencies"):
            detect(sightings, today="2026-09-06")

    def test_two_currencies_on_one_day_are_refused_rather_than_added(self) -> None:
        """The per-day collapse adds up what one day held, so the guard has to
        run before it — checked afterwards there would be one currency left
        and the sum would already have happened."""
        sightings = [
            Sighting(occurred_on=dt.date(2026, 7, 5), amount=money("30000")),
            Sighting(
                occurred_on=dt.date(2026, 7, 5),
                amount=money("30", Currency.USD),
            ),
            Sighting(occurred_on=dt.date(2026, 8, 5), amount=money("30000")),
            Sighting(occurred_on=dt.date(2026, 9, 5), amount=money("30000")),
        ]

        with pytest.raises(ValueError, match="currencies"):
            detect(sightings, today="2026-09-06")


class TestAmount:
    def test_a_fixed_charge_predicts_the_last_figure_not_the_median(self) -> None:
        """A price that went up once is the price now."""
        sightings = [
            *seen("2026-06-10", amount="44900"),
            *seen("2026-07-10", amount="44900"),
            *seen("2026-08-10", amount="46900"),
        ]

        series = detect(sightings, today="2026-08-11")

        assert series is not None
        assert series.variable is False
        assert series.amount.amount == Decimal("46900")

    def test_a_price_rise_is_the_new_price_not_a_reason_to_hedge(self) -> None:
        """16 900 three times and then 19 900 is a rise, not noise.

        Read as noise it would be called variable *and* predicted at the
        median — 16 900, the one figure certain to be wrong next month.
        """
        sightings = [
            *seen("2026-05-09", amount="16900"),
            *seen("2026-06-09", amount="16900"),
            *seen("2026-07-09", amount="16900"),
            *seen("2026-08-09", amount="19900"),
        ]

        series = detect(sightings, today="2026-08-10")

        assert series is not None
        assert series.variable is False
        assert series.amount.amount == Decimal("19900")

    def test_a_variable_charge_predicts_the_median_of_the_recent_ones(self) -> None:
        """The phone bill. Dropping it would lose the charges people feel."""
        sightings = [
            *seen("2026-05-20", amount="61000"),
            *seen("2026-06-20", amount="98000"),
            *seen("2026-07-20", amount="72000"),
            *seen("2026-08-20", amount="80000"),
        ]

        series = detect(sightings, today="2026-08-21")

        assert series is not None
        assert series.variable is True
        assert series.amount.amount == Decimal("80000")

    def test_two_charges_on_one_day_are_one_charge_for_that_day(self) -> None:
        sightings = [
            *seen("2026-06-10", amount="20000"),
            *seen("2026-07-10", amount="12000"),
            *seen("2026-07-10", amount="8000"),
            *seen("2026-08-10", amount="20000"),
        ]

        series = detect(sightings, today="2026-08-11")

        assert series is not None
        assert series.cadence is BillCadence.MONTHLY
        assert series.sightings == 3
        assert series.variable is False


class TestState:
    def test_a_charge_still_ahead_is_active(self) -> None:
        series = detect(
            seen("2026-06-04", "2026-07-04", "2026-08-04", "2026-09-04"),
            today="2026-09-20",
        )

        assert series is not None
        assert series.state is SeriesState.ACTIVE

    def test_past_its_day_and_its_grace_it_is_late(self) -> None:
        series = detect(
            seen("2026-05-04", "2026-06-04", "2026-07-04"),
            today="2026-08-20",
        )

        assert series is not None
        assert series.state is SeriesState.LATE

    def test_two_charges_missing_and_it_stops_predicting(self) -> None:
        """A cancelled subscription that keeps suggesting itself is worse than
        no detector at all."""
        series = detect(
            seen("2026-03-04", "2026-04-04", "2026-05-04"),
            today="2026-08-20",
        )

        assert series is not None
        assert series.state is SeriesState.DORMANT


class TestConfidence:
    def test_a_long_clean_series_beats_a_short_one_with_a_hole(self) -> None:
        clean = detect(
            seen(
                "2026-03-15",
                "2026-04-15",
                "2026-05-15",
                "2026-06-15",
                "2026-07-15",
                "2026-08-15",
            ),
            today="2026-08-16",
        )
        patchy = detect(
            seen("2026-05-15", "2026-06-15", "2026-08-15"),
            today="2026-08-16",
        )

        assert clean is not None
        assert patchy is not None
        assert clean.confidence == Decimal("1.00")
        assert patchy.confidence < clean.confidence

    def test_charges_landing_at_the_edge_of_the_tolerance_read_as_unsure(
        self,
    ) -> None:
        """Three charges, each two or three days off its day: a rhythm, but
        one worth showing near the bottom of the list."""
        series = detect(
            seen("2026-06-15", "2026-07-18", "2026-08-13"),
            today="2026-08-20",
        )

        assert series is not None
        assert Decimal(0) <= series.confidence <= Decimal("0.6")


class TestAccount:
    def test_the_account_it_is_charged_from_now_wins_over_the_old_one(self) -> None:
        old = AccountId.new()
        current = AccountId.new()
        sightings = [
            *seen("2026-06-10", account_id=old),
            *seen("2026-07-10", account_id=current),
            *seen("2026-08-10", account_id=current),
        ]

        series = detect(sightings, today="2026-08-11")

        assert series is not None
        assert series.account_id == current
