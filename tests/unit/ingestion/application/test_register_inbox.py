import dataclasses

from personal_finance.contexts.ingestion.application.inbox_handlers import (
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
BASE_ADDRESS = EmailAddress("finflowingest@gmail.com")


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        # Like the real one: the milestones belong to another writer, and
        # this must not carry a stale copy of them back over the record.
        stored = self.inboxes.get(inbox.address)
        self.inboxes[inbox.address] = dataclasses.replace(
            inbox,
            forwarding_confirmed_at=(
                stored.forwarding_confirmed_at if stored else None
            ),
            first_accepted_at=stored.first_accepted_at if stored else None,
        )

    def mark_forwarding_confirmed(
        self,
        *,
        address: EmailAddress,
        confirmed_at: PosixTime,
    ) -> bool:
        return self._mark(address, forwarding_confirmed_at=confirmed_at)

    def mark_first_accepted(
        self,
        *,
        address: EmailAddress,
        accepted_at: PosixTime,
    ) -> bool:
        return self._mark(address, first_accepted_at=accepted_at)

    def _mark(self, address: EmailAddress, **milestone: PosixTime) -> bool:
        inbox = self.inboxes.get(address)

        if inbox is None:
            return False

        # First write wins, as `if_not_exists` does in DynamoDB.
        already_set = {
            field: value
            for field, value in milestone.items()
            if getattr(inbox, field) is not None
        }
        self.inboxes[address] = dataclasses.replace(
            inbox,
            **{k: v for k, v in milestone.items() if k not in already_set},
        )

        return True


def _use_case(repository: InMemoryUserInboxRepository) -> RegisterUserInboxUseCase:
    return RegisterUserInboxUseCase(
        inbox_repository=repository,
        base_address=BASE_ADDRESS,
    )


def test_the_address_is_derived_from_the_user_id() -> None:
    repository = InMemoryUserInboxRepository()

    inbox = _use_case(repository).execute(RegisterUserInboxCommand(user_id=USER_ID))

    assert inbox.address.value == f"finflowingest+{USER_ID.value.hex}@gmail.com"
    assert repository.inboxes[inbox.address].user_id == USER_ID


def test_the_approved_senders_are_stored() -> None:
    repository = InMemoryUserInboxRepository()

    inbox = _use_case(repository).execute(
        RegisterUserInboxCommand(
            user_id=USER_ID,
            allowed_domains=frozenset({"bank.com"}),
        ),
    )

    assert inbox.sender_policy.is_authorized(EmailAddress("alerts@bank.com"))


def test_no_approved_senders_by_default() -> None:
    repository = InMemoryUserInboxRepository()

    inbox = _use_case(repository).execute(RegisterUserInboxCommand(user_id=USER_ID))

    assert not inbox.sender_policy.is_authorized(EmailAddress("anyone@bank.com"))


def test_calling_it_again_replaces_the_sender_list_rather_than_extending_it() -> None:
    repository = InMemoryUserInboxRepository()
    use_case = _use_case(repository)
    use_case.execute(
        RegisterUserInboxCommand(
            user_id=USER_ID,
            allowed_domains=frozenset({"bank.com"}),
        ),
    )

    updated = use_case.execute(
        RegisterUserInboxCommand(
            user_id=USER_ID,
            allowed_domains=frozenset({"other-bank.com"}),
        ),
    )

    assert not updated.sender_policy.is_authorized(EmailAddress("alerts@bank.com"))
    assert updated.sender_policy.is_authorized(EmailAddress("alerts@other-bank.com"))
    # Same address both times: there is exactly one inbox per user, not a
    # growing collection of them.
    assert len(repository.inboxes) == 1
