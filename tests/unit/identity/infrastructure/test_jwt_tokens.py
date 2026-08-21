import jwt as pyjwt
import pytest

from personal_finance.contexts.identity.domain.exceptions import InvalidAccessTokenError
from personal_finance.contexts.identity.infrastructure.security.jwt_tokens import (
    JWTTokenIssuer,
)
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


def _issuer(*, secret: str = "test-secret", ttl_minutes: int = 60) -> JWTTokenIssuer:
    return JWTTokenIssuer(secret=secret, algorithm="HS256", ttl_minutes=ttl_minutes)


def test_a_freshly_issued_token_verifies_back_to_the_same_user() -> None:
    issuer = _issuer()

    token = issuer.issue(USER_ID)

    assert issuer.verify(token.value) == USER_ID


def test_a_token_signed_with_a_different_secret_is_rejected() -> None:
    token = _issuer(secret="secret-a").issue(USER_ID)

    with pytest.raises(InvalidAccessTokenError):
        _issuer(secret="secret-b").verify(token.value)


def test_an_expired_token_is_rejected() -> None:
    issuer = _issuer(ttl_minutes=-1)

    token = issuer.issue(USER_ID)

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
