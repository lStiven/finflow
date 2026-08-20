import pytest

from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress


def test_policy_requires_at_least_one_allowed_address_or_domain() -> None:
    with pytest.raises(ValueError, match="at least one"):
        AuthorizedSenderPolicy()


def test_sender_authorized_by_exact_address() -> None:
    policy = AuthorizedSenderPolicy(
        allowed_addresses=frozenset({EmailAddress("alerts@bank.com")}),
    )

    assert policy.is_authorized(EmailAddress("alerts@bank.com"))


def test_sender_not_authorized_when_address_not_allowed() -> None:
    policy = AuthorizedSenderPolicy(
        allowed_addresses=frozenset({EmailAddress("alerts@bank.com")}),
    )

    assert not policy.is_authorized(EmailAddress("someone@gmail.com"))


def test_sender_authorized_by_domain() -> None:
    policy = AuthorizedSenderPolicy(allowed_domains=frozenset({"bank.com"}))

    assert policy.is_authorized(EmailAddress("no-reply@bank.com"))


def test_domain_matching_is_case_insensitive() -> None:
    policy = AuthorizedSenderPolicy(allowed_domains=frozenset({"BANK.COM"}))

    assert policy.is_authorized(EmailAddress("no-reply@bank.com"))


def test_sender_not_authorized_when_domain_not_allowed() -> None:
    policy = AuthorizedSenderPolicy(allowed_domains=frozenset({"bank.com"}))

    assert not policy.is_authorized(EmailAddress("someone@gmail.com"))


def test_email_address_exposes_domain() -> None:
    assert EmailAddress("no-reply@bank.com").domain == "bank.com"
