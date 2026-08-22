from personal_finance.contexts.identity.application.commands import (
    UpdateApprovedSendersCommand,
)
from personal_finance.contexts.identity.application.inbox_handlers import (
    UpdateApprovedSendersUseCase,
)
from personal_finance.contexts.identity.application.ports import (
    InboxRegistration,
    RegisteredInbox,
)
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")


class RecordingInboxRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[UserId, InboxRegistration]] = []

    def register(
        self,
        *,
        user_id: UserId,
        inbox: InboxRegistration,
    ) -> RegisteredInbox:
        self.calls.append((user_id, inbox))

        return RegisteredInbox(
            address=f"inbox+{user_id.value}@test",
            allowed_domains=inbox.allowed_domains,
            allowed_addresses=inbox.allowed_addresses,
        )


def test_approved_senders_are_set_for_the_authenticated_user_only() -> None:
    registrar = RecordingInboxRegistrar()
    use_case = UpdateApprovedSendersUseCase(inbox_registrar=registrar)
    inbox = InboxRegistration(allowed_domains=frozenset({"bank.com"}))

    result = use_case.execute(
        user_id=USER_ID,
        command=UpdateApprovedSendersCommand(inbox=inbox),
    )

    assert registrar.calls == [(USER_ID, inbox)]
    assert result.allowed_domains == frozenset({"bank.com"})
