from collections.abc import Sequence

from personal_finance.contexts.identity.application.commands import AddInboxesCommand
from personal_finance.contexts.identity.application.inbox_handlers import (
    AddInboxesUseCase,
)
from personal_finance.contexts.identity.application.ports import InboxRegistration
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


class RecordingInboxRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[UserId, tuple[InboxRegistration, ...]]] = []

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        self.calls.append((user_id, tuple(inboxes)))


def test_inboxes_are_registered_for_the_authenticated_user_only() -> None:
    registrar = RecordingInboxRegistrar()
    use_case = AddInboxesUseCase(inbox_registrar=registrar)
    inboxes = (InboxRegistration(address="u-2@inbound.test"),)

    use_case.execute(user_id=USER_ID, command=AddInboxesCommand(inboxes=inboxes))

    assert registrar.calls == [(USER_ID, inboxes)]
