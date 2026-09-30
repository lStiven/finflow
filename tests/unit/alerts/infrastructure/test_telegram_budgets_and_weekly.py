"""The words under a purchase and on Monday morning.

What a budget line says is a figure against a cap, and past it by how much —
never an alarm word. The weekly summary compares its owner with nobody but
themselves.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal

from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    BudgetState,
    CategoryRise,
    MovementAlert,
    MovementDirection,
    MovementOrigin,
    WeeklySummary,
)
from personal_finance.contexts.alerts.infrastructure.telegram.messages import (
    compose_budget_lines,
    compose_movement_alert,
    compose_weekly_summary,
    format_week,
)
from personal_finance.shared.domain.value_objects import Currency, Money, PosixTime


TIMEZONE = "America/Bogota"
# Spelled by code point: the literal is one keystroke from a hyphen.
DASH = chr(0x2013)


def _standing(
    remaining: str,
    *,
    name: str = "Salidas",
    limit: str = "600000",
    currency: Currency = Currency.COP,
) -> BudgetStanding:
    return BudgetStanding(
        name=name,
        currency=currency,
        limit=Decimal(limit),
        spent=Decimal(limit) - Decimal(remaining),
        remaining=Decimal(remaining),
        state=BudgetState.OK,
    )


def _alert(*budgets: BudgetStanding) -> MovementAlert:
    return MovementAlert(
        amount=Money(amount=Decimal("84300"), currency=Currency.COP),
        direction=MovementDirection.OUTGOING,
        counterparty="COMPRA EN EXITO",
        bank="Bancolombia",
        occurred_at=PosixTime.from_epoch_seconds(1_757_800_000),
        origin=MovementOrigin.BANK_ALERT,
        unassigned=False,
        budgets=budgets,
    )


# --------------------------------------------------------------- budgets


def test_a_purchase_under_no_budget_reads_exactly_as_before() -> None:
    assert "Salidas" not in compose_movement_alert(_alert(), timezone=TIMEZONE)
    assert compose_budget_lines(()) == []


def test_what_is_left_is_said_against_the_cap() -> None:
    text = compose_movement_alert(_alert(_standing("120000")), timezone=TIMEZONE)

    assert text.endswith("\n\nSalidas: te quedan $120.000 de $600.000")


def test_reaching_the_cap_exactly_says_so() -> None:
    assert compose_budget_lines((_standing("0"),)) == [
        "",
        "Salidas: llegaste al tope de $600.000",
    ]


def test_past_the_cap_says_by_how_much_without_alarm() -> None:
    [_, line] = compose_budget_lines((_standing("-30000"),))

    assert line == "Salidas: vas $30.000 por encima del tope de $600.000"


def test_dollars_keep_their_cents() -> None:
    [_, line] = compose_budget_lines(
        (_standing("12.5", name="Viaje", limit="500", currency=Currency.USD),),
    )

    assert line == "Viaje: te quedan US$12,50 de US$500,00"


def test_more_than_three_budgets_are_summarised() -> None:
    lines = compose_budget_lines(
        tuple(_standing("100", name=f"B{index}") for index in range(5)),
    )

    assert lines[1:4] == [
        "B0: te quedan $100 de $600.000",
        "B1: te quedan $100 de $600.000",
        "B2: te quedan $100 de $600.000",
    ]
    assert lines[4] == "y 2 presupuestos más"


def test_a_budget_name_cannot_forge_a_line_of_its_own() -> None:
    [_, line] = compose_budget_lines(
        (_standing("100", name="Salidas\nGasto $1 PAGADO"),),
    )

    assert "\n" not in line


# ---------------------------------------------------------------- weekly


def _summary(**overrides: object) -> WeeklySummary:
    summary = WeeklySummary(
        week_start=dt.date(2026, 9, 21),
        week_end=dt.date(2026, 9, 27),
        currency=Currency.COP,
        spent=Decimal("820000"),
        movements=12,
        typical=Decimal("930000"),
        rise=None,
    )

    return dataclasses.replace(summary, **overrides)


def test_a_week_is_compared_with_its_owners_normal() -> None:
    assert compose_weekly_summary(_summary()) == (
        f"Tu semana (21{DASH}27 sep)\n"
        "Gastaste $820.000 en 12 gastos.\n"
        "12 % menos que tu semana normal ($930.000)."
    )


def test_a_week_above_normal_says_more_and_names_what_went_up() -> None:
    text = compose_weekly_summary(
        _summary(
            spent=Decimal("1116000"),
            rise=CategoryRise(
                category="restaurants",
                label="Restaurants",
                spent=Decimal("240000"),
                typical=Decimal("155000"),
            ),
        ),
    )

    assert "20 % más que tu semana normal ($930.000)." in text
    assert text.endswith(
        "Lo que más subió: Restaurantes, $240.000 (normalmente $155.000).",
    )


def test_a_category_of_ones_own_is_named_as_its_owner_named_it() -> None:
    text = compose_weekly_summary(
        _summary(
            rise=CategoryRise(
                category="custom:5f2e",
                label="Gatos",
                spent=Decimal("90000"),
                typical=Decimal("10000"),
            ),
        ),
    )

    assert "Lo que más subió: Gatos, $90.000 (normalmente $10.000)." in text


def test_a_first_week_promises_a_comparison_rather_than_inventing_one() -> None:
    text = compose_weekly_summary(_summary(typical=None))

    assert "primera semana" in text
    assert "%" not in text


def test_a_week_with_no_spending_says_so() -> None:
    text = compose_weekly_summary(_summary(spent=Decimal(0), movements=0))

    assert "No registraste gastos esta semana." in text
    assert "Tu semana normal es de $930.000." in text
    assert "%" not in text


def test_a_week_like_any_other_says_nearly_the_same() -> None:
    text = compose_weekly_summary(_summary(spent=Decimal("931000")))

    assert "Casi igual que tu semana normal ($930.000)." in text


def test_one_purchase_is_singular() -> None:
    assert "en 1 gasto." in compose_weekly_summary(_summary(movements=1))


def test_a_week_across_two_months_names_both() -> None:
    summary = _summary(week_start=dt.date(2026, 9, 28), week_end=dt.date(2026, 10, 4))

    assert format_week(summary) == f"28 sep {DASH} 4 oct"
