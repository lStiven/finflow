from __future__ import annotations

from personal_finance.contexts.identity.application.commands import AddInboxesCommand
from personal_finance.contexts.identity.application.ports import InboxRegistrar
from personal_finance.shared.domain.value_objects import UserId


class AddInboxesUseCase:
    """Lets an already-authenticated user attach more inboxes, or update the
    senders trusted for one they already registered.

    The user id always comes from the verified access token, never from the
    request body: a caller must not be able to edit someone else's inboxes by
    naming their id.
    """

    def __init__(self, *, inbox_registrar: InboxRegistrar) -> None:
        self._inbox_registrar = inbox_registrar

    def execute(self, *, user_id: UserId, command: AddInboxesCommand) -> None:
        self._inbox_registrar.register(user_id=user_id, inboxes=command.inboxes)
