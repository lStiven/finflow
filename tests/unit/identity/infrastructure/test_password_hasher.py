from personal_finance.contexts.identity.domain.value_objects import PasswordHash
from personal_finance.contexts.identity.infrastructure.security.password_hashing import (  # noqa: E501
    BcryptPasswordHasher,
)


def test_hash_never_contains_the_plaintext_password() -> None:
    hasher = BcryptPasswordHasher()

    hashed = hasher.hash("correct horse battery staple")

    assert "correct horse battery staple" not in hashed.value


def test_verify_accepts_the_correct_password() -> None:
    hasher = BcryptPasswordHasher()
    hashed = hasher.hash("correct horse battery staple")

    assert hasher.verify("correct horse battery staple", hashed) is True


def test_verify_rejects_the_wrong_password() -> None:
    hasher = BcryptPasswordHasher()
    hashed = hasher.hash("correct horse battery staple")

    assert hasher.verify("wrong password", hashed) is False


def test_verify_rejects_a_malformed_hash_instead_of_raising() -> None:
    hasher = BcryptPasswordHasher()

    assert hasher.verify("anything", PasswordHash("not-a-real-bcrypt-hash")) is False


def test_hashing_the_same_password_twice_yields_different_output() -> None:
    hasher = BcryptPasswordHasher()

    # bcrypt salts every hash, so two hashes of the same password never match
    # byte-for-byte even though both verify successfully.
    first = hasher.hash("correct horse battery staple")
    second = hasher.hash("correct horse battery staple")

    assert first != second
    assert hasher.verify("correct horse battery staple", first) is True
    assert hasher.verify("correct horse battery staple", second) is True
