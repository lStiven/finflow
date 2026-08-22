from __future__ import annotations

from personal_finance.contexts.identity.application.ports import (
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    ListUserInboxesUseCase,
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
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

    def register(self, *, user_id: UserId, inbox: InboxRegistration) -> RegisteredInbox:
        saved = self._use_case.execute(
            RegisterUserInboxCommand(
                user_id=user_id,
                allowed_domains=inbox.allowed_domains,
                allowed_addresses=frozenset(
                    EmailAddress(value) for value in inbox.allowed_addresses
                ),
            ),
        )

        # Returns what was just written rather than making the caller read it
        # back: `find_by_user` goes through a GSI, which DynamoDB never
        # serves with strongly consistent reads, so a read immediately after
        # this write could otherwise still see the old sender list.
        return _to_registered(saved)

    def get_for_user(self, user_id: UserId) -> RegisteredInbox | None:
        inboxes = self._list_use_case.execute(user_id)

        if not inboxes:
            return None

        return _to_registered(inboxes[0])


def _to_registered(inbox: UserInbox) -> RegisteredInbox:
    return RegisteredInbox(
        address=inbox.address.value,
        allowed_domains=frozenset(inbox.sender_policy.allowed_domains),
        allowed_addresses=frozenset(
            address.value for address in inbox.sender_policy.allowed_addresses
        ),
    )
