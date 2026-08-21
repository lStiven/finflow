import pytest

from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Person@Example.com", "person@example.com"),
        ("  person@example.com  ", "person@example.com"),
    ],
)
def test_email_is_normalized(raw: str, expected: str) -> None:
    assert Email(raw).value == expected


@pytest.mark.parametrize("raw", ["", "   ", "not-an-email"])
def test_invalid_email_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="Invalid email address"):
        Email(raw)


def test_equal_emails_compare_equal_regardless_of_case() -> None:
    assert Email("Person@Example.com") == Email("person@example.com")


def test_empty_password_hash_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        PasswordHash("   ")


def test_password_hash_never_serializes_its_value() -> None:
    assert PasswordHash("$2b$12$abcdefg").to_dict() == "<redacted>"
