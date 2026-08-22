from personal_finance.contexts.ingestion.application.inbox_handlers import (
    ListUserInboxesUseCase,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox


def _inbox(
    *,
    address: str,
    user_id: UserId = USER_ID,
    domains: frozenset[str] = frozenset({"bank.com"}),
) -> UserInbox:
    return UserInbox(
        user_id=user_id,
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


def test_a_user_with_no_inbox_gets_an_empty_list() -> None:
    use_case = ListUserInboxesUseCase(inbox_repository=InMemoryUserInboxRepository())

    assert use_case.execute(USER_ID) == []


def test_the_users_inbox_is_returned() -> None:
    repository = InMemoryUserInboxRepository(_inbox(address="mine@inbound.test"))
    use_case = ListUserInboxesUseCase(inbox_repository=repository)

    inboxes = use_case.execute(USER_ID)

    assert [inbox.address.value for inbox in inboxes] == ["mine@inbound.test"]


def test_another_users_inbox_is_never_returned() -> None:
    repository = InMemoryUserInboxRepository(
        _inbox(address="mine@inbound.test"),
        _inbox(address="theirs@inbound.test", user_id=OTHER_USER_ID),
    )
    use_case = ListUserInboxesUseCase(inbox_repository=repository)

    inboxes = use_case.execute(USER_ID)

    assert [inbox.address.value for inbox in inboxes] == ["mine@inbound.test"]


def test_the_trusted_senders_travel_with_the_inbox() -> None:
    repository = InMemoryUserInboxRepository(
        _inbox(address="a@inbound.test", domains=frozenset({"bancolombia.com.co"})),
    )
    use_case = ListUserInboxesUseCase(inbox_repository=repository)

    inbox = use_case.execute(USER_ID)[0]

    assert inbox.sender_policy.allowed_domains == frozenset({"bancolombia.com.co"})
