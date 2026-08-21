from __future__ import annotations

from collections.abc import Sequence

from personal_finance.contexts.identity.application.ports import InboxRegistration
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


class IngestionInboxRegistrar:
    """Adapts identity's `InboxRegistrar` port to ingestion's own use case.

    This is the one place identity is allowed to know ingestion exists: the
    application layer above depends only on the `InboxRegistrar` protocol, so
    swapping or removing this adapter never touches a use case or a test.
    Ingestion's `RegisterUserInboxUseCase` is its published integration
    surface — the same one its CLI uses — not an internal detail.
    """

    def __init__(self, *, use_case: RegisterUserInboxUseCase) -> None:
        self._use_case = use_case

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        for inbox in inboxes:
            self._use_case.execute(
                RegisterUserInboxCommand(
                    address=EmailAddress(inbox.address),
                    user_id=user_id,
                    allowed_domains=inbox.allowed_domains,
                    allowed_addresses=frozenset(
                        EmailAddress(value) for value in inbox.allowed_addresses
                    ),
                ),
            )
