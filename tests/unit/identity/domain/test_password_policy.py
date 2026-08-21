import pytest

from personal_finance.contexts.identity.domain.policies import (
    WeakPasswordError,
    validate_password_strength,
)


def test_password_at_the_minimum_length_is_accepted() -> None:
    validate_password_strength("12345678")


def test_short_password_is_rejected() -> None:
    with pytest.raises(WeakPasswordError, match="at least 8 characters"):
        validate_password_strength("short")
