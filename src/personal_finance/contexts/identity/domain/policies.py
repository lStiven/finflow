from __future__ import annotations


_MIN_PASSWORD_LENGTH = 8


class WeakPasswordError(ValueError):
    """Raised when a candidate password does not meet the minimum policy."""


def validate_password_strength(password: str) -> None:
    """Rejects a plaintext password before it ever reaches the hasher.

    Deliberately minimal: length is the one rule that is both meaningful and
    impossible to get subtly wrong. Composition rules (must contain a digit,
    a symbol, ...) are known to push users toward predictable patterns without
    a corresponding security gain.
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long",
        )
