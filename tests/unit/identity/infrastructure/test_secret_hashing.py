from personal_finance.contexts.identity.domain.value_objects import SecretHash
from personal_finance.contexts.identity.infrastructure.security.secret_generator import (  # noqa: E501
    SecretsSecretGenerator,
)
from personal_finance.contexts.identity.infrastructure.security.secret_hashing import (
    BcryptSecretHasher,
    Sha256SecretHasher,
)


CODE = "123456"


def test_a_code_hash_is_salted_so_two_of_the_same_code_differ() -> None:
    """Which is exactly why it cannot be a lookup key, and why the reset
    token — which is one — uses the other hasher.
    """
    hasher = BcryptSecretHasher()

    assert hasher.hash(CODE) != hasher.hash(CODE)


def test_a_code_verifies_against_its_own_hash_and_nothing_else() -> None:
    hasher = BcryptSecretHasher()
    hashed = hasher.hash(CODE)

    assert hasher.verify(CODE, hashed)
    assert not hasher.verify("654321", hashed)


def test_a_stored_value_that_is_not_a_hash_is_a_failed_check() -> None:
    # The caller is a stranger typing digits at an unauthenticated endpoint;
    # a corrupt record must not turn that into a 500.
    assert not BcryptSecretHasher().verify(CODE, SecretHash("not-a-bcrypt-hash"))


def test_a_token_hash_is_deterministic_because_it_is_the_lookup_key() -> None:
    hasher = Sha256SecretHasher()

    assert hasher.hash("a-token") == hasher.hash("a-token")


def test_a_token_hash_does_not_contain_the_token() -> None:
    assert "a-token" not in Sha256SecretHasher().hash("a-token").value


def test_a_token_verifies_against_its_own_hash_and_nothing_else() -> None:
    hasher = Sha256SecretHasher()
    hashed = hasher.hash("a-token")

    assert hasher.verify("a-token", hashed)
    assert not hasher.verify("another-token", hashed)


def test_a_generated_code_is_six_ascii_digits() -> None:
    generator = SecretsSecretGenerator()

    for _ in range(50):
        code = generator.verification_code()
        assert len(code) == 6
        assert code.isascii()
        assert code.isdigit()


def test_generated_tokens_do_not_repeat() -> None:
    generator = SecretsSecretGenerator()

    tokens = {generator.opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    # Long enough that guessing one is not a strategy: this is the whole
    # secret behind a reset link.
    assert all(len(token) >= 40 for token in tokens)
