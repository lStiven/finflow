import pytest

from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    CorruptUserItemError,
    to_entity,
    to_item,
)
from personal_finance.shared.domain.value_objects import PosixTime


REGISTERED_AT_EPOCH = 1_700_000_000


def _user() -> User:
    return User.register(
        email=Email("person@example.com"),
        password_hash=PasswordHash("$2b$12$abcdefg"),
        registered_at=PosixTime.from_epoch_seconds(REGISTERED_AT_EPOCH),
    )


def test_item_round_trip_preserves_the_aggregate() -> None:
    user = _user()

    restored = to_entity(to_item(user))

    assert restored.id == user.id
    assert restored.email == user.email
    assert restored.password_hash == user.password_hash
    assert restored.registered_at == user.registered_at


def test_restored_aggregate_has_no_pending_events() -> None:
    user = _user()

    restored = to_entity(to_item(user))

    assert user.pull_events() != []
    assert restored.pull_events() == []


def test_item_is_partitioned_by_email() -> None:
    user = _user()

    assert to_item(user)[PARTITION_KEY] == {"S": "person@example.com"}


def test_corrupt_item_is_rejected_with_a_named_error() -> None:
    item = to_item(_user())
    del item["password_hash"]

    with pytest.raises(CorruptUserItemError, match="password_hash"):
        to_entity(item)
