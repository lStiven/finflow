from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.ports import UserInboxRepository
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserInboxCommand:
    address: EmailAddress
    # Omitted when registering a brand-new user; supplied to re-register or to
    # attach a second inbound address to someone who already exists.
    user_id: UserId | None = None
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[EmailAddress] = dataclasses.field(
        default_factory=lambda: frozenset[EmailAddress](),
    )


class RegisterUserInboxUseCase:
    """Registers the inbound address a user forwards their bank email to,
    together with the senders that user trusts.
    """

    def __init__(self, *, inbox_repository: UserInboxRepository) -> None:
        self._inbox_repository = inbox_repository

    def execute(self, command: RegisterUserInboxCommand) -> UserInbox:
        existing = self._inbox_repository.find_by_address(command.address)
        # Re-registering an address keeps its owner: reassigning it silently
        # would hand one person's incoming email to another.
        user_id = command.user_id or (existing.user_id if existing else UserId.new())

        inbox = UserInbox(
            user_id=user_id,
            address=command.address,
            sender_policy=AuthorizedSenderPolicy(
                allowed_addresses=command.allowed_addresses,
                allowed_domains=command.allowed_domains,
            ),
        )
        self._inbox_repository.save(inbox)

        return inbox


class ListUserInboxesUseCase:
    """Lists the inbound addresses a user owns and the senders each trusts.

    Published for other contexts to call: identity exposes it to an
    authenticated user through its own adapter, so nobody has to reach into
    this context's repository to answer "which mailboxes do I have?".
    """

    def __init__(self, *, inbox_repository: UserInboxRepository) -> None:
        self._inbox_repository = inbox_repository

    def execute(self, user_id: UserId) -> Sequence[UserInbox]:
        inboxes = self._inbox_repository.find_by_user(user_id)

        # Sorted here rather than in the repository, so the order is part of
        # the use case's contract instead of a storage accident.
        return sorted(inboxes, key=lambda inbox: inbox.address.value)
