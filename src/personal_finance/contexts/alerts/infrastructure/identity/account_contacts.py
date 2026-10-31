"""Who to email, asked of Identity through its published use case.

Alerts knows people only by id. Identity owns their address and name, and
answers through `GetAccountContactUseCase` — the one surface it publishes for
this; nothing here reads its tables on its own.
"""

from __future__ import annotations

from personal_finance.contexts.alerts.application.messages import MailRecipient
from personal_finance.contexts.identity.application.contacts import (
    GetAccountContactUseCase,
)
from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    DynamoDBUserRepository,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_identity_settings,
)


class IdentityAccountContacts:
    """`AccountContacts` over Identity's published use case."""

    def __init__(self, *, use_case: GetAccountContactUseCase) -> None:
        self._use_case = use_case

    def find(self, user_id: UserId) -> MailRecipient | None:
        contact = self._use_case.execute(user_id=user_id)

        if contact is None:
            return None

        return MailRecipient(address=contact.email, name=contact.name)


def build_account_contacts() -> IdentityAccountContacts:
    return IdentityAccountContacts(
        use_case=GetAccountContactUseCase(
            directory=DynamoDBUserRepository(
                client=get_dynamodb_client(),
                table_name=get_identity_settings().users_table,
            ),
        ),
    )
