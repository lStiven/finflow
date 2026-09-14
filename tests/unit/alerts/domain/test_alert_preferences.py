from decimal import Decimal

from personal_finance.contexts.alerts.domain.value_objects import (
    AlertPreference,
    AlertPreferences,
    AlertType,
)
from personal_finance.shared.domain.value_objects import Currency, Money


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _usd(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.USD)


# ----------------------------------------------------------------------
# One preference
# ----------------------------------------------------------------------


def test_a_disabled_preference_admits_nothing() -> None:
    preference = AlertPreference(
        alert_type=AlertType.MOVEMENT,
        enabled=False,
        minimum_amount=None,
    )

    assert not preference.admits(_cop("1000000"))


def test_without_a_floor_every_amount_is_admitted() -> None:
    preference = AlertPreference(alert_type=AlertType.MOVEMENT, enabled=True)

    assert preference.admits(_cop("1"))


def test_the_floor_is_inclusive() -> None:
    preference = AlertPreference(
        alert_type=AlertType.MOVEMENT,
        enabled=True,
        minimum_amount=_cop("20000"),
    )

    assert preference.admits(_cop("20000"))
    assert preference.admits(_cop("20001"))
    assert not preference.admits(_cop("19999"))


def test_a_floor_in_another_currency_does_not_silence_a_movement() -> None:
    """A filter that cannot be evaluated delivers.

    A message too many is noise; a message too few is a purchase nobody
    heard about.
    """
    preference = AlertPreference(
        alert_type=AlertType.MOVEMENT,
        enabled=True,
        minimum_amount=_cop("20000"),
    )

    assert preference.admits(_usd("1"))


# ----------------------------------------------------------------------
# The set of them
# ----------------------------------------------------------------------


def test_a_type_nobody_expressed_an_opinion_about_falls_back_to_its_default() -> None:
    """This is what lets a new alert type ship without a migration.

    Every channel stored before a member existed has no entry for it, and
    the member decides its own default in code rather than in the table.
    """
    preferences = AlertPreferences.none_expressed()

    fallback = preferences.for_type(AlertType.MOVEMENT)

    assert fallback.enabled is AlertType.MOVEMENT.enabled_by_default
    assert fallback.minimum_amount is None


def test_updating_a_type_replaces_it_rather_than_appending() -> None:
    preferences = AlertPreferences.none_expressed().with_updated(
        AlertPreference(alert_type=AlertType.MOVEMENT, enabled=False),
    )

    updated = preferences.with_updated(
        AlertPreference(
            alert_type=AlertType.MOVEMENT,
            enabled=True,
            minimum_amount=_cop("50000"),
        ),
    )

    assert len(updated.entries) == 1
    assert updated.for_type(AlertType.MOVEMENT).minimum_amount == _cop("50000")


def test_preferences_are_immutable() -> None:
    original = AlertPreferences.none_expressed()

    original.with_updated(
        AlertPreference(alert_type=AlertType.MOVEMENT, enabled=False),
    )

    assert original.entries == ()
