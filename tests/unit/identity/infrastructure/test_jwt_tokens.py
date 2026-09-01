import jwt as pyjwt
import pytest

from personal_finance.contexts.identity.application.ports import AuthenticatedUser
from personal_finance.contexts.identity.domain.exceptions import InvalidAccessTokenError
from personal_finance.contexts.identity.domain.value_objects import Email, PersonName
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
EMAIL = Email("person@example.com")
USER = AuthenticatedUser(user_id=USER_ID, email=EMAIL, name=PersonName("Ada Lovelace"))


def _issuer(*, secret: str = "test-secret", ttl_minutes: int = 60) -> JWTTokenIssuer:
    return JWTTokenIssuer(secret=secret, algorithm="HS256", ttl_minutes=ttl_minutes)


def _claims(token: str, *, secret: str = "test-secret") -> dict[str, object]:
    return pyjwt.decode(token, secret, algorithms=["HS256"])


def test_a_freshly_issued_token_verifies_back_to_the_same_user() -> None:
    issuer = _issuer()

    token = issuer.issue(USER)

    assert issuer.verify(token.value) == USER


def test_the_token_carries_the_email_and_name_under_their_standard_claims() -> None:
    # `email` and `name` rather than private claim names, so any decoder a
    # client already has can read them.
    token = _issuer().issue(USER)

    claims = _claims(token.value)

    assert claims["sub"] == str(USER_ID.value)
    assert claims["email"] == EMAIL.value
    assert claims["name"] == "Ada Lovelace"


def test_an_account_with_no_name_gets_a_token_without_the_claim() -> None:
    nameless = AuthenticatedUser(user_id=USER_ID, email=EMAIL)

    token = _issuer().issue(nameless)

    assert "name" not in _claims(token.value)
    assert _issuer().verify(token.value).name is None


def test_a_token_signed_with_a_different_secret_is_rejected() -> None:
    token = _issuer(secret="secret-a").issue(USER)

    with pytest.raises(InvalidAccessTokenError):
        _issuer(secret="secret-b").verify(token.value)


def test_an_expired_token_is_rejected() -> None:
    issuer = _issuer(ttl_minutes=-1)

    token = issuer.issue(USER)

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(token.value)


def test_garbage_input_is_rejected_rather_than_raising_a_library_error() -> None:
    with pytest.raises(InvalidAccessTokenError):
        _issuer().verify("not-a-jwt-at-all")


def test_a_token_without_a_subject_claim_is_rejected() -> None:
    issuer = _issuer()
    tampered = pyjwt.encode({"exp": 9_999_999_999}, "test-secret", algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(tampered)


def test_a_token_without_an_email_claim_is_rejected() -> None:
    # The email is what an authenticated request reaches its own record by,
    # so a token that verifies without one must not be usable at all.
    issuer = _issuer()
    tampered = pyjwt.encode(
        {"sub": str(USER_ID.value), "exp": 9_999_999_999},
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(tampered)


def test_a_token_whose_email_claim_is_not_an_address_is_rejected() -> None:
    issuer = _issuer()
    tampered = pyjwt.encode(
        {"sub": str(USER_ID.value), "email": "not-an-address", "exp": 9_999_999_999},
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(tampered)


def test_an_unusable_name_claim_is_dropped_rather_than_failing_the_request() -> None:
    # The name is a display convenience: nothing depends on it, so a bad one
    # costs the caller their name in the token, not their session.
    issuer = _issuer()
    tampered = pyjwt.encode(
        {
            "sub": str(USER_ID.value),
            "email": EMAIL.value,
            "name": "   ",
            "cv": 1,
            "exp": 9_999_999_999,
        },
        "test-secret",
        algorithm="HS256",
    )

    assert issuer.verify(tampered).name is None


def test_a_token_carries_the_credential_version_it_was_cut_from() -> None:
    issuer = _issuer()

    token = issuer.issue(
        AuthenticatedUser(
            user_id=USER_ID, email=EMAIL, credential_version=1_700_000_000
        ),
    )

    assert issuer.verify(token.value).credential_version == 1_700_000_000


def test_a_token_without_a_credential_version_is_refused() -> None:
    """Every token issued before the claim existed names an account whose
    credentials have since moved on. Defaulting the claim would be the one
    case where a token nothing can check still authenticates.
    """
    issuer = _issuer()
    old = pyjwt.encode(
        {
            "sub": str(USER_ID.value),
            "email": EMAIL.value,
            "exp": 9_999_999_999,
        },
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(old)


def test_a_boolean_credential_version_is_refused() -> None:
    # `True` is an `int` in Python, so a claim of `true` would otherwise
    # decode to version 1 and match an account registered on that second.
    issuer = _issuer()
    tampered = pyjwt.encode(
        {
            "sub": str(USER_ID.value),
            "email": EMAIL.value,
            "cv": True,
            "exp": 9_999_999_999,
        },
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(InvalidAccessTokenError):
        issuer.verify(tampered)
