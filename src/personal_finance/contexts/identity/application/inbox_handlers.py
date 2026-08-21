from __future__ import annotations

from collections.abc import Sequence

from personal_finance.contexts.identity.application.commands import AddInboxesCommand
from personal_finance.contexts.identity.application.ports import (
    InboxReader,
    InboxRegistrar,
    RegisteredInbox,
)
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


class ListInboxesUseCase:
    """Reports the inboxes a user owns, with the senders trusted for each.

    Scoped to the authenticated user by construction: there is no parameter
    for whose inboxes to read other than the verified token's subject, so no
    request shape can ask for someone else's.
    """

    def __init__(self, *, inbox_reader: InboxReader) -> None:
        self._inbox_reader = inbox_reader

    def execute(self, *, user_id: UserId) -> Sequence[RegisteredInbox]:
        return self._inbox_reader.list_for_user(user_id)
