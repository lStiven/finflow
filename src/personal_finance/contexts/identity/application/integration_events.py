"""What identity publishes to the rest of the system.

Same discipline as ingestion's: every field is listed by hand, so a change to
the `User` aggregate can never silently widen what other contexts receive.
"""

from __future__ import annotations

from personal_finance.contexts.identity.domain.events import UserRegistered
from personal_finance.shared.application.integration import IntegrationEvent
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import JsonValue


SOURCE = "finflow.identity"

USER_REGISTERED = "UserRegistered"
USER_REGISTERED_VERSION = 1


class IdentityIntegrationEventTranslator:
    """Announces that a user now exists, so other contexts can set up
    whatever they keep per user.
    """

    def translate(self, event: Event) -> IntegrationEvent | None:
        if not isinstance(event, UserRegistered):
            return None

        payload: dict[str, JsonValue] = {
            "user_id": str(event.user_id.value),
            "email": event.email.value,
        }

        # The password hash is not merely omitted here — `PasswordHash`
        # redacts itself on serialization too, so neither this contract nor a
        # stray log can carry it.
        return IntegrationEvent(
            source=SOURCE,
            detail_type=USER_REGISTERED,
            version=USER_REGISTERED_VERSION,
            event_id=event.event_id,
            payload=payload,
            occurred_at=event.occurred_at,
        )
