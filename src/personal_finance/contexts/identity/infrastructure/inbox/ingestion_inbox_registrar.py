from __future__ import annotations

from collections.abc import Sequence

from personal_finance.contexts.identity.application.ports import (
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    ListUserInboxesUseCase,
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


class IngestionInboxRegistrar:
    """Adapts identity's `InboxRegistrar` and `InboxReader` ports to
    ingestion's own use cases.

    This is the one place identity is allowed to know ingestion exists: the
    application layer above depends only on the protocols, so swapping or
    removing this adapter never touches a use case or a test. Ingestion's
    `RegisterUserInboxUseCase` and `ListUserInboxesUseCase` are its published
    integration surface — the same ones its CLI uses — not internal details.

    Translating at this boundary is the point: ingestion's `UserInbox` and its
    value objects stop here, and identity's own `RegisteredInbox` continues
    outwards.
    """

    def __init__(
        self,
        *,
        use_case: RegisterUserInboxUseCase,
        list_use_case: ListUserInboxesUseCase,
    ) -> None:
        self._use_case = use_case
        self._list_use_case = list_use_case

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

    def list_for_user(self, user_id: UserId) -> Sequence[RegisteredInbox]:
        return [
            RegisteredInbox(
                address=inbox.address.value,
                allowed_domains=frozenset(inbox.sender_policy.allowed_domains),
                allowed_addresses=frozenset(
                    address.value for address in inbox.sender_policy.allowed_addresses
                ),
            )
            for inbox in self._list_use_case.execute(user_id)
        ]
