from personal_finance.contexts.ingestion.domain.forwarding import (
    forwarding_address,
    gmail_filter_terms,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-2222-3333-4444-555555555555")


def test_the_alias_plugs_the_user_id_into_the_base_address() -> None:
    address = forwarding_address(
        base=EmailAddress("finflowingest@gmail.com"),
        user_id=USER_ID,
    )

    assert address.value == ("finflowingest+11111111222233334444555555555555@gmail.com")


def test_it_is_deterministic() -> None:
    base = EmailAddress("finflowingest@gmail.com")

    first = forwarding_address(base=base, user_id=USER_ID)
    second = forwarding_address(base=base, user_id=USER_ID)

    assert first == second


def test_different_users_get_different_addresses() -> None:
    base = EmailAddress("finflowingest@gmail.com")
    other = UserId.from_string("99999999-8888-7777-6666-555555555555")

    assert forwarding_address(base=base, user_id=USER_ID) != forwarding_address(
        base=base,
        user_id=other,
    )


def test_it_respects_the_base_address_domain() -> None:
    address = forwarding_address(
        base=EmailAddress("someoneelse@outlook.com"),
        user_id=USER_ID,
    )

    assert address.domain == "outlook.com"
    assert address.value.startswith("someoneelse+")


def test_the_filter_terms_put_each_domain_behind_an_at_sign() -> None:
    policy = AuthorizedSenderPolicy(
        allowed_domains=frozenset({"lulobank.com", "bancolombia.com.co"}),
        allowed_addresses=frozenset({EmailAddress("alertas@otrobanco.com")}),
    )

    assert gmail_filter_terms(policy) == (
        "@bancolombia.com.co",
        "@lulobank.com",
        "alertas@otrobanco.com",
    )


def test_nobody_approved_is_an_empty_filter() -> None:
    assert gmail_filter_terms(AuthorizedSenderPolicy()) == ()
