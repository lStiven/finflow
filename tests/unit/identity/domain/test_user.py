from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
)
from personal_finance.shared.domain.value_objects import PosixTime


def _register() -> User:
    return User.register(
        email=Email("person@example.com"),
        password_hash=PasswordHash("$2b$12$abcdefg"),
        registered_at=PosixTime.now(),
    )


def test_register_assigns_a_fresh_id() -> None:
    first = _register()
    second = _register()

    assert first.id != second.id


def test_register_records_a_user_registered_event() -> None:
    user = _register()

    events = user.pull_events()

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, UserRegistered)
    assert event.user_id == user.id
    assert event.email == user.email


def test_an_account_can_be_registered_without_a_name() -> None:
    assert _register().name is None


def test_a_name_given_at_registration_is_kept() -> None:
    user = User.register(
        email=Email("person@example.com"),
        password_hash=PasswordHash("$2b$12$abcdefg"),
        registered_at=PosixTime.now(),
        name=PersonName("Ada Lovelace"),
    )

    assert user.name == PersonName("Ada Lovelace")


def test_renaming_changes_nothing_but_the_name() -> None:
    user = _register()
    email, password_hash, user_id = user.email, user.password_hash, user.id

    user.rename(PersonName("Ada Lovelace"))

    assert user.name == PersonName("Ada Lovelace")
    assert (user.id, user.email, user.password_hash) == (user_id, email, password_hash)
