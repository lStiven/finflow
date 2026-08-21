from personal_finance.contexts.identity.application.integration_events import (
    SOURCE,
    IdentityIntegrationEventTranslator,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
PASSWORD_HASH = "$2b$12$notarealhashbutlooksliketone"


def _registered() -> UserRegistered:
    return UserRegistered(user_id=USER_ID, email=Email("person@example.com"))


def test_user_registered_becomes_a_public_event() -> None:
    event = IdentityIntegrationEventTranslator().translate(_registered())

    assert event is not None
    assert event.source == SOURCE
    assert event.detail_type == "UserRegistered"
    assert event.version == 1
    assert event.payload == {
        "user_id": str(USER_ID.value),
        "email": "person@example.com",
    }


def test_the_event_id_is_carried_through_for_deduplication() -> None:
    domain_event = _registered()

    event = IdentityIntegrationEventTranslator().translate(domain_event)

    assert event is not None
    assert event.event_id == domain_event.event_id


def test_the_password_hash_can_never_reach_the_bus() -> None:
    user = User.register(
        email=Email("person@example.com"),
        password_hash=PasswordHash(PASSWORD_HASH),
        registered_at=PosixTime.now(),
    )
    domain_event = user.pull_events()[0]

    event = IdentityIntegrationEventTranslator().translate(domain_event)

    assert event is not None
    assert PASSWORD_HASH not in str(event.payload)
    assert "password" not in str(event.payload)
