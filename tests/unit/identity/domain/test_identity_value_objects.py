import pytest

from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
)


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Ada Lovelace  ", "Ada Lovelace"),
        ("Ada    Lovelace", "Ada Lovelace"),
        ("Ada\nLovelace", "Ada Lovelace"),
    ],
)
def test_person_name_collapses_surrounding_and_inner_whitespace(
    raw: str,
    expected: str,
) -> None:
    assert PersonName(raw).value == expected


@pytest.mark.parametrize("raw", ["", "   ", "\n\t"])
def test_a_name_that_is_only_whitespace_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        PersonName(raw)


def test_an_overlong_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        PersonName("a" * (PersonName.MAX_LENGTH + 1))


def test_person_name_serializes_as_the_plain_string() -> None:
    assert PersonName("Ada Lovelace").to_dict() == "Ada Lovelace"
