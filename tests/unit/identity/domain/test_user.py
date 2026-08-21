from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
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
