from __future__ import annotations

from personal_finance.contexts.identity.application.commands import (
    UpdateApprovedSendersCommand,
)
from personal_finance.contexts.identity.application.ports import (
    InboxReader,
    InboxRegistrar,
    RegisteredInbox,
)
from personal_finance.shared.domain.value_objects import UserId


class UpdateApprovedSendersUseCase:
    """Lets an already-authenticated user set which senders may reach their
    forwarding address.

    Replaces the sender list rather than adding to it — there is exactly one
    inbox per user, not a growing collection. The user id always comes from
    the verified access token, never from the request body: a caller must not
    be able to edit someone else's approved senders by naming their id.
    """

    def __init__(self, *, inbox_registrar: InboxRegistrar) -> None:
        self._inbox_registrar = inbox_registrar

    def execute(
        self,
        *,
        user_id: UserId,
        command: UpdateApprovedSendersCommand,
    ) -> RegisteredInbox:
        return self._inbox_registrar.register(user_id=user_id, inbox=command.inbox)


class GetInboxUseCase:
    """Reports the authenticated user's forwarding address and approved
    senders.

    Scoped to the authenticated user by construction: there is no parameter
    for whose inbox to read other than the verified token's subject, so no
    request shape can ask for someone else's.
    """

    def __init__(self, *, inbox_reader: InboxReader) -> None:
        self._inbox_reader = inbox_reader

    def execute(self, *, user_id: UserId) -> RegisteredInbox | None:
        return self._inbox_reader.get_for_user(user_id)
