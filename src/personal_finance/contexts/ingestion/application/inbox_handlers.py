from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.ports import UserInboxRepository
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.forwarding import forwarding_address
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserInboxCommand:
    user_id: UserId
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[EmailAddress] = dataclasses.field(
        default_factory=lambda: frozenset[EmailAddress](),
    )


class RegisterUserInboxUseCase:
    """Assigns a user their forwarding address and the senders they trust.

    The address is never chosen by a caller: it is derived deterministically
    from `user_id` against this deployment's one ingest mailbox, so there is
    nothing to allocate and nothing that can collide. Calling this again for
    the same user replaces the sender list rather than adding to it — there is
    exactly one inbox per user, not a growing collection of them.
    """

    def __init__(
        self,
        *,
        inbox_repository: UserInboxRepository,
        base_address: EmailAddress,
    ) -> None:
        self._inbox_repository = inbox_repository
        self._base_address = base_address

    def execute(self, command: RegisterUserInboxCommand) -> UserInbox:
        inbox = UserInbox(
            user_id=command.user_id,
            address=forwarding_address(
                base=self._base_address,
                user_id=command.user_id,
            ),
            sender_policy=AuthorizedSenderPolicy(
                allowed_addresses=command.allowed_addresses,
                allowed_domains=command.allowed_domains,
            ),
        )
        self._inbox_repository.save(inbox)

        return inbox


class ListUserInboxesUseCase:
    """Reports the inbox a user owns and the senders it trusts.

    Published for other contexts to call: identity exposes it to an
    authenticated user through its own adapter, so nobody has to reach into
    this context's repository to answer "what's my forwarding address?".
    """

    def __init__(self, *, inbox_repository: UserInboxRepository) -> None:
        self._inbox_repository = inbox_repository

    def execute(self, user_id: UserId) -> Sequence[UserInbox]:
        return self._inbox_repository.find_by_user(user_id)
