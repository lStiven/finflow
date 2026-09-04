"""The arithmetic that makes a debt more than what is unpaid.

Somebody owing 60 000 000 who pays 2 000 000 does not then owe 58 000 000, and
every test here exists because of that sentence. The month charges interest on
what was owed and the bank adds the insurance the loan carries; the payment
clears both before it reduces anything, and what is left is the only part that
moved the debt.

The figures are Colombian and deliberately so: rates quoted `% E.A.`, a
*seguro de vida deudores* charged on the outstanding balance, a *seguro de
incendio y terremoto* charged on what the property is insured for, and
*retención en la fuente* taken out of what a CDT earns.
"""

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.exceptions import (
    FinancingTermsError,
)
from personal_finance.contexts.financial.domain.financing import (
    AccountCategory,
    AmortizationStyle,
    ChargeBase,
    ChargeBasis,
    InterestRate,
    InvestmentTerms,
    LoanTerms,
    MovementDirection,
    RateBasis,
    RecurringCharge,
    StatementPeriod,
    accrue_period,
    add_months,
    first_cut_after,
    installment_for,
    on_day,
    postings,
    project_investment,
    project_loan,
    statement_periods,
    valuation_item,
)
from personal_finance.shared.domain.value_objects import Currency, Money


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


EFFECTIVE_ANNUAL = InterestRate(
    value=Decimal("0.1956"),
    basis=RateBasis.EFFECTIVE_ANNUAL,
)
LIFE_INSURANCE = RecurringCharge(
    name="Seguro de vida deudores",
    basis=ChargeBasis.OUTSTANDING_BALANCE,
    rate=Decimal("0.000345"),
)
FIRE_INSURANCE = RecurringCharge(
    name="Seguro de incendio y terremoto",
    basis=ChargeBasis.INSURED_VALUE,
    rate=Decimal("0.00029"),
    base=_cop("350000000"),
)


def _mortgage(**overrides: object) -> LoanTerms:
    defaults: dict[str, object] = {
        "rate": EFFECTIVE_ANNUAL,
        "disbursed_on": dt.date(2026, 1, 15),
        "term_months": 60,
        "statement_day": 15,
        "principal": _cop("60000000"),
    }
    defaults.update(overrides)

    return LoanTerms(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------- rates


def test_an_effective_annual_rate_converts_to_the_monthly_one_a_bank_quotes() -> None:
    # 19.56 % E.A. is 1.4999 % a month. The two are the same rate, and a
    # statement shows whichever the product was sold under.
    assert round(EFFECTIVE_ANNUAL.monthly, 6) == Decimal("0.014999")
    assert round(EFFECTIVE_ANNUAL.effective_annual, 6) == Decimal("0.195600")


def test_a_nominal_rate_is_not_the_same_rate_as_an_effective_one() -> None:
    # The mistake this enum exists to prevent: 19.56 % nominal anual is a
    # twelfth of itself each month, which compounds to more than 19.56 %.
    nominal = InterestRate(value=Decimal("0.1956"), basis=RateBasis.NOMINAL_ANNUAL)

    assert nominal.monthly == Decimal("0.1956") / 12
    assert nominal.effective_annual > EFFECTIVE_ANNUAL.effective_annual


def test_a_monthly_rate_is_used_exactly_as_given() -> None:
    monthly = InterestRate(value=Decimal("0.015"), basis=RateBasis.MONTHLY)

    assert monthly.monthly == Decimal("0.015")


def test_a_percentage_typed_as_a_percentage_is_refused() -> None:
    # `19.56` instead of `0.1956` is a debt growing twentyfold a month, and
    # every figure after it is nonsense.
    with pytest.raises(FinancingTermsError):
        InterestRate(value=Decimal("19.56"), basis=RateBasis.EFFECTIVE_ANNUAL)


def test_a_negative_rate_is_refused() -> None:
    with pytest.raises(FinancingTermsError):
        InterestRate(value=Decimal("-0.01"), basis=RateBasis.MONTHLY)


def test_a_part_month_prorates_thirty_over_three_hundred_and_sixty() -> None:
    half = EFFECTIVE_ANNUAL.factor(days=15)

    assert half == EFFECTIVE_ANNUAL.monthly * Decimal(15) / Decimal(30)
    assert EFFECTIVE_ANNUAL.factor(days=None) == EFFECTIVE_ANNUAL.monthly


# ----------------------------------------------------------------- charges


def test_the_life_insurance_falls_with_the_debt() -> None:
    base = ChargeBase(outstanding=_cop("60000000"))
    smaller = ChargeBase(outstanding=_cop("30000000"))

    assert LIFE_INSURANCE.due(base).amount == Decimal("20700.00")
    assert LIFE_INSURANCE.due(smaller).amount == Decimal("10350.00")


def test_the_fire_insurance_does_not_move_with_the_debt() -> None:
    # Charged on what the property is insured for, which is not a balance
    # this app holds and does not fall as the mortgage is paid.
    assert FIRE_INSURANCE.due(
        ChargeBase(outstanding=_cop("60000000")),
    ).amount == Decimal("101500.00")
    assert FIRE_INSURANCE.due(
        ChargeBase(outstanding=_cop("1000000")),
    ).amount == Decimal("101500.00")


def test_a_flat_fee_is_the_same_every_period() -> None:
    fee = RecurringCharge(
        name="Cuota de manejo",
        basis=ChargeBasis.FIXED,
        amount=_cop("18500"),
    )

    assert fee.due(ChargeBase(outstanding=_cop("900000"))).amount == Decimal("18500")


def test_withholding_is_charged_on_what_was_earned_not_on_the_balance() -> None:
    withholding = RecurringCharge(
        name="Retención en la fuente",
        basis=ChargeBasis.EARNINGS,
        rate=Decimal("0.04"),
    )
    base = ChargeBase(outstanding=_cop("10000000"), earned=_cop("90000"))

    assert withholding.due(base).amount == Decimal("3600.00")


def test_a_charge_quoted_as_a_rate_cannot_also_carry_an_amount() -> None:
    with pytest.raises(FinancingTermsError):
        RecurringCharge(
            name="Seguro",
            basis=ChargeBasis.OUTSTANDING_BALANCE,
            rate=Decimal("0.0003"),
            amount=_cop("1000"),
        )


def test_a_charge_on_an_insured_value_needs_one() -> None:
    with pytest.raises(FinancingTermsError):
        RecurringCharge(
            name="Seguro de incendio",
            basis=ChargeBasis.INSURED_VALUE,
            rate=Decimal("0.00029"),
        )


def test_two_charges_cannot_share_a_name() -> None:
    # Each is posted once per period under its own name, so the second would
    # be refused as a duplicate and never charged at all.
    duplicated = RecurringCharge(
        name="seguro",
        basis=ChargeBasis.OUTSTANDING_BALANCE,
        rate=Decimal("0.0003"),
    )
    other = RecurringCharge(
        name="SEGURO",
        basis=ChargeBasis.FIXED,
        amount=_cop("100"),
    )

    with pytest.raises(FinancingTermsError):
        _mortgage(charges=(duplicated, other))


def test_a_charge_on_the_original_principal_needs_the_amount_disbursed() -> None:
    charge = RecurringCharge(
        name="Estudio de crédito",
        basis=ChargeBasis.ORIGINAL_PRINCIPAL,
        rate=Decimal("0.0002"),
    )

    with pytest.raises(FinancingTermsError):
        _mortgage(principal=None, charges=(charge,))


# ---------------------------------------------------------------- calendar


def test_a_cut_on_the_thirty_first_falls_on_the_last_day_february_has() -> None:
    assert on_day(2026, 2, 31) == dt.date(2026, 2, 28)
    assert add_months(dt.date(2026, 1, 31), 1, on=31) == dt.date(2026, 2, 28)
    # And does not stay clamped: March has a 31st and the cut returns to it.
    assert add_months(dt.date(2026, 2, 28), 1, on=31) == dt.date(2026, 3, 31)


def test_the_next_cut_is_strictly_after_the_day_asked_about() -> None:
    # A period is half-open, so a movement on the cut day belongs to the one
    # that opens there and a cursor sitting on a cut must not re-accrue it.
    assert first_cut_after(dt.date(2026, 3, 15), 15) == dt.date(2026, 4, 15)
    assert first_cut_after(dt.date(2026, 3, 14), 15) == dt.date(2026, 3, 15)


def test_only_closed_periods_are_returned() -> None:
    periods = statement_periods(
        since=dt.date(2026, 1, 15),
        through=dt.date(2026, 4, 10),
        statement_day=15,
    )

    assert [period.ends_on for period in periods] == [
        dt.date(2026, 2, 15),
        dt.date(2026, 3, 15),
    ]


def test_a_period_that_does_not_start_on_a_cut_is_a_stub() -> None:
    periods = statement_periods(
        since=dt.date(2026, 1, 3),
        through=dt.date(2026, 3, 20),
        statement_day=15,
    )

    assert [period.partial for period in periods] == [True, False, False]
    assert periods[0].days == 12


# ------------------------------------------------------------------- loans


def test_the_french_instalment_clears_the_balance_over_the_term() -> None:
    installment = installment_for(
        outstanding=_cop("60000000"),
        rate=EFFECTIVE_ANNUAL,
        periods=60,
    )

    assert installment.amount == Decimal("1523555.29")


def test_an_interest_free_loan_is_the_balance_split_evenly() -> None:
    zero = InterestRate(value=Decimal(0), basis=RateBasis.MONTHLY)

    assert installment_for(
        outstanding=_cop("1200000"),
        rate=zero,
        periods=12,
    ).amount == Decimal("100000.00")


def test_paying_two_million_on_sixty_million_does_not_leave_fifty_eight() -> None:
    """The sentence the whole module exists for.

    60 000 000 at 19.56 % E.A. charges 899 922.87 of interest in a month and
    20 700 of *seguro de vida*. A 2 000 000 instalment covering both leaves
    1 079 377.13 against the debt — so the balance is 58 920 622.87, not
    58 000 000, and the 920 622.87 difference is what a plain ledger cannot
    see.
    """
    schedule = project_loan(
        terms=_mortgage(
            installment=_cop("2000000"),
            installment_covers_charges=True,
            charges=(LIFE_INSURANCE,),
        ),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=1,
    )
    first = schedule.payments[0]

    assert first.interest.amount == Decimal("899922.87")
    assert first.charges[0].amount.amount == Decimal("20700.00")
    assert first.principal.amount == Decimal("1079377.13")
    assert first.due.amount == Decimal("2000000.00")
    assert first.closing_balance.amount == Decimal("58920622.87")


def test_an_instalment_quoted_apart_from_the_insurance_is_paid_on_top_of_it() -> None:
    # The same loan read the other way: 2 000 000 of capital-and-interest
    # plus the insurance, which is 2 020 700 leaving the account and more of
    # the payment reaching the debt.
    schedule = project_loan(
        terms=_mortgage(
            installment=_cop("2000000"),
            installment_covers_charges=False,
            charges=(LIFE_INSURANCE,),
        ),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=1,
    )
    first = schedule.payments[0]

    assert first.principal.amount == Decimal("1100077.13")
    assert first.due.amount == Decimal("2020700.00")


def test_a_charge_the_bank_collects_elsewhere_is_owed_but_never_capitalized() -> None:
    apart = RecurringCharge(
        name="Seguro de vida deudores",
        basis=ChargeBasis.OUTSTANDING_BALANCE,
        rate=Decimal("0.000345"),
        charged_to_balance=False,
    )
    schedule = project_loan(
        terms=_mortgage(installment=_cop("2000000"), charges=(apart,)),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=1,
    )
    first = schedule.payments[0]

    assert first.due.amount == Decimal("2020700.00")
    # The capital portion is the instalment less the interest alone: the
    # insurance never passed through this balance.
    assert first.principal.amount == Decimal("1100077.13")


def test_an_instalment_that_does_not_cover_the_interest_grows_the_debt() -> None:
    schedule = project_loan(
        terms=_mortgage(installment=_cop("500000")),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=3,
    )

    assert schedule.negatively_amortizing is True
    assert schedule.payments[0].principal.amount == Decimal(0)
    assert schedule.payments[0].closing_balance == schedule.payments[0].opening_balance


def test_the_last_instalment_is_trimmed_so_the_debt_settles_exactly() -> None:
    schedule = project_loan(
        terms=_mortgage(term_months=3, disbursed_on=dt.date(2026, 1, 15)),
        outstanding=_cop("3000000"),
        as_of=dt.date(2026, 1, 15),
        periods=6,
    )

    assert schedule.payments[-1].closing_balance.amount == Decimal(0)
    assert schedule.settles_on == schedule.payments[-1].period.ends_on
    # Nothing is scheduled past the settlement: the loop stops on a zero
    # balance rather than charging a fourth month of a three-month loan.
    assert len(schedule.payments) == 3


def test_constant_principal_pays_the_same_capital_every_month() -> None:
    schedule = project_loan(
        terms=_mortgage(
            term_months=4,
            style=AmortizationStyle.CONSTANT_PRINCIPAL,
        ),
        outstanding=_cop("4000000"),
        as_of=dt.date(2026, 1, 15),
        periods=4,
    )

    assert [payment.principal.amount for payment in schedule.payments] == [
        Decimal("1000000.00"),
    ] * 4
    # And the instalment falls, because the interest is charged on less each
    # time.
    assert schedule.payments[0].due.amount > schedule.payments[-1].due.amount


def test_interest_only_never_touches_the_capital() -> None:
    schedule = project_loan(
        terms=_mortgage(style=AmortizationStyle.INTEREST_ONLY),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=3,
    )

    assert schedule.negatively_amortizing is False
    assert all(
        payment.closing_balance.amount == Decimal("60000000")
        for payment in schedule.payments
    )


def test_a_settled_loan_charges_nothing_at_all() -> None:
    # Not even a flat fee. A finished loan that keeps charging is a debt
    # growing with nobody watching it.
    fee = RecurringCharge(
        name="Cuota de manejo",
        basis=ChargeBasis.FIXED,
        amount=_cop("18500"),
    )
    accrued = accrue_period(
        period=StatementPeriod(
            starts_on=dt.date(2026, 1, 15),
            ends_on=dt.date(2026, 2, 15),
        ),
        opening=_cop("0"),
        rate=EFFECTIVE_ANNUAL,
        charges=(fee,),
    )

    assert accrued.interest.amount == Decimal(0)
    assert accrued.charges == ()


def test_a_stub_period_accrues_less_than_a_whole_month() -> None:
    whole = accrue_period(
        period=StatementPeriod(
            starts_on=dt.date(2026, 1, 15),
            ends_on=dt.date(2026, 2, 15),
        ),
        opening=_cop("60000000"),
        rate=EFFECTIVE_ANNUAL,
    )
    stub = accrue_period(
        period=StatementPeriod(
            starts_on=dt.date(2026, 2, 1),
            ends_on=dt.date(2026, 2, 15),
            partial=True,
        ),
        opening=_cop("60000000"),
        rate=EFFECTIVE_ANNUAL,
    )

    assert stub.interest.amount < whole.interest.amount
    assert stub.interest.amount == Decimal("419964.00")


# ---------------------------------------------------------------- postings


def test_interest_owed_grows_a_debt_and_interest_earned_grows_a_holding() -> None:
    accrued = accrue_period(
        period=StatementPeriod(
            starts_on=dt.date(2026, 1, 15),
            ends_on=dt.date(2026, 2, 15),
        ),
        opening=_cop("60000000"),
        rate=EFFECTIVE_ANNUAL,
        charges=(LIFE_INSURANCE,),
    )
    owed = postings(accrued, category=AccountCategory.LIABILITY)
    held = postings(accrued, category=AccountCategory.ASSET)

    assert owed[0].direction is MovementDirection.OUTGOING
    assert owed[0].label == "Intereses"
    assert held[0].direction is MovementDirection.INCOMING
    assert held[0].label == "Rendimientos"
    # A charge costs money either way, so it is outgoing on both.
    assert owed[1].direction is MovementDirection.OUTGOING
    assert held[1].direction is MovementDirection.OUTGOING


def test_a_charge_collected_elsewhere_never_becomes_a_row() -> None:
    apart = RecurringCharge(
        name="Seguro",
        basis=ChargeBasis.FIXED,
        amount=_cop("15000"),
        charged_to_balance=False,
    )
    accrued = accrue_period(
        period=StatementPeriod(
            starts_on=dt.date(2026, 1, 15),
            ends_on=dt.date(2026, 2, 15),
        ),
        opening=_cop("60000000"),
        rate=EFFECTIVE_ANNUAL,
        charges=(apart,),
    )

    assert [
        row.label for row in postings(accrued, category=AccountCategory.LIABILITY)
    ] == [
        "Intereses",
    ]


# ------------------------------------------------------------- investments


def test_a_cdt_earns_net_of_what_is_withheld() -> None:
    withholding = RecurringCharge(
        name="Retención en la fuente",
        basis=ChargeBasis.EARNINGS,
        rate=Decimal("0.04"),
    )
    projection = project_investment(
        terms=InvestmentTerms(
            opened_on=dt.date(2026, 1, 10),
            statement_day=10,
            rate=InterestRate(value=Decimal("0.105"), basis=RateBasis.EFFECTIVE_ANNUAL),
            charges=(withholding,),
        ),
        value=_cop("20000000"),
        as_of=dt.date(2026, 1, 10),
        periods=1,
    )
    first = projection.periods[0]

    assert first.earned.amount == Decimal("167103.11")
    assert first.charges[0].amount.amount == Decimal("6684.12")
    assert first.closing_balance.amount == Decimal("20160418.99")


def test_nothing_accrues_past_a_cdt_maturity() -> None:
    projection = project_investment(
        terms=InvestmentTerms(
            opened_on=dt.date(2026, 1, 10),
            statement_day=10,
            rate=InterestRate(value=Decimal("0.105"), basis=RateBasis.EFFECTIVE_ANNUAL),
            matures_on=dt.date(2026, 4, 10),
        ),
        value=_cop("20000000"),
        as_of=dt.date(2026, 1, 10),
        periods=12,
    )

    assert [period.period.ends_on for period in projection.periods] == [
        dt.date(2026, 2, 10),
        dt.date(2026, 3, 10),
        dt.date(2026, 4, 10),
    ]


def test_variable_income_projects_nothing_because_nothing_can_be_projected() -> None:
    projection = project_investment(
        terms=InvestmentTerms(opened_on=dt.date(2026, 1, 10), statement_day=10),
        value=_cop("20000000"),
        as_of=dt.date(2026, 1, 10),
        periods=12,
    )

    assert projection.periods == ()
    assert projection.value_at_end.amount == Decimal("20000000")


def test_an_investment_cannot_withhold_from_a_return_nobody_computes() -> None:
    with pytest.raises(FinancingTermsError):
        InvestmentTerms(
            opened_on=dt.date(2026, 1, 10),
            statement_day=10,
            charges=(
                RecurringCharge(
                    name="Retención",
                    basis=ChargeBasis.EARNINGS,
                    rate=Decimal("0.04"),
                ),
            ),
        )


def test_a_charge_the_bank_debits_elsewhere_is_not_inside_the_instalment() -> None:
    """The combination the form's own toggle invites, and got wrong.

    `installment_covers_charges` says the number on the statement already
    contains the insurance the *credit* carries. An insurance the bank debits
    from another account is a separate payment on a separate day and was never
    inside that number — subtracting it too would take it out of the capital
    portion every single month.
    """
    apart = RecurringCharge(
        name="Seguro de incendio",
        basis=ChargeBasis.INSURED_VALUE,
        rate=Decimal("0.00029"),
        base=_cop("350000000"),
        charged_to_balance=False,
    )
    schedule = project_loan(
        terms=_mortgage(
            installment=_cop("2000000"),
            installment_covers_charges=True,
            charges=(LIFE_INSURANCE, apart),
        ),
        outstanding=_cop("60000000"),
        as_of=dt.date(2026, 1, 15),
        periods=1,
    )
    first = schedule.payments[0]

    # The instalment covers the interest and the life insurance; the fire
    # insurance is paid on top of it, and none of it comes out of capital.
    assert first.principal.amount == Decimal("1079377.13")
    assert first.due.amount == Decimal("2101500.00")
    assert first.closing_balance.amount == Decimal("58920622.87")


def test_a_revaluation_is_keyed_by_the_move_it_makes() -> None:
    # Keying on the target alone made the third step of 11M -> 15M -> 11M
    # collide with the first, leaving the value stuck at 15 million while the
    # answer reported success.
    first = valuation_item(held=Decimal(0), stated=Decimal("11000000"))
    second = valuation_item(held=Decimal("11000000"), stated=Decimal("15000000"))
    third = valuation_item(held=Decimal("15000000"), stated=Decimal("11000000"))

    assert len({first, second, third}) == 3
    # And the same move twice is still the same move, which is what refuses a
    # double submit.
    assert third == valuation_item(held=Decimal("15000000"), stated=Decimal("11000000"))


def test_a_move_repeated_in_a_day_can_take_another_turn() -> None:
    # The step after the round trip: back up to 15M repeats the very first
    # move, so the pair alone is not enough to tell the two apart.
    first = valuation_item(held=Decimal("11000000"), stated=Decimal("15000000"))
    again = valuation_item(
        held=Decimal("11000000"),
        stated=Decimal("15000000"),
        turn=2,
    )

    assert first != again
    # The first turn is spelled the way it always was, so no row already
    # written changes its key.
    assert first == valuation_item(
        held=Decimal("11000000"),
        stated=Decimal("15000000"),
        turn=1,
    )

    with pytest.raises(ValueError, match="at least one turn"):
        valuation_item(held=Decimal(0), stated=Decimal("1"), turn=0)
