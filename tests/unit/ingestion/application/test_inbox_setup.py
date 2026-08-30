"""What the connect-your-bank screen is told, and why it is derived.

The steps a user cannot see finish are the whole reason this exists: Google
confirms a forwarding request by mailing an address only this deployment can
read, and the first alert lands in a worker.
"""

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.inbox_handlers import (
    MAX_UNAPPROVED_SENDERS,
    GetInboxSetupUseCase,
)
from personal_finance.contexts.ingestion.application.ports import NotificationSummary
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.setup import SetupStep
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER = UserId.from_string("22222222-2222-2222-2222-222222222222")
BASE_ADDRESS = EmailAddress("finflowingest@gmail.com")
ALIAS = EmailAddress("finflowingest+11111111111111111111111111111111@gmail.com")
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = EmailAddress(f"alertasynotificaciones@{BANK_DOMAIN}")
NOW = PosixTime.from_epoch_seconds(1_756_400_000)


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox, index_is_stale: bool = False) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}
        self.index_calls = 0
        # Stands in for the `by_user` GSI not having caught up yet, which is
        # what a brand-new account hits on its very first call.
        self._index_is_stale = index_is_stale

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        self.index_calls += 1

        if self._index_is_stale:
            return []

        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox

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

        self.inboxes[address] = dataclasses.replace(inbox, **milestone)

        return True


class InMemoryReader:
    def __init__(self, user_id: UserId, *summaries: NotificationSummary) -> None:
        self._user_id = user_id
        self._summaries = summaries
        self.calls = 0

    def list_by_user(self, user_id: UserId) -> Sequence[NotificationSummary]:
        self.calls += 1

        return self._summaries if user_id == self._user_id else ()


def _summary(
    sender: str,
    *,
    message_id: str,
    status: ProcessingStatus,
    received_at: PosixTime = NOW,
) -> NotificationSummary:
    return NotificationSummary(
        id=NotificationId.for_message(
            user_id=USER,
            message_id=EmailMessageId(message_id),
        ),
        message_id=EmailMessageId(message_id),
        sender=EmailAddress(sender),
        subject="Alertas y Notificaciones",
        status=status,
        deferred_reason=None,
        received_at=received_at,
    )


def _ignored(
    sender: str,
    *,
    message_id: str,
    received_at: PosixTime = NOW,
) -> NotificationSummary:
    return _summary(
        sender,
        message_id=message_id,
        status=ProcessingStatus.IGNORED,
        received_at=received_at,
    )


def _accepted(message_id: str, *, received_at: PosixTime = NOW) -> NotificationSummary:
    return _summary(
        BANK_SENDER.value,
        message_id=message_id,
        status=ProcessingStatus.PROCESSED,
        received_at=received_at,
    )


def _inbox(
    *,
    domains: frozenset[str] = frozenset(),
    forwarding_confirmed_at: PosixTime | None = None,
    first_accepted_at: PosixTime | None = None,
) -> UserInbox:
    return UserInbox(
        user_id=USER,
        address=ALIAS,
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
        forwarding_confirmed_at=forwarding_confirmed_at,
        first_accepted_at=first_accepted_at,
    )


def _use_case(
    inbox: UserInbox | None,
    *notifications: NotificationSummary,
    repository: InMemoryUserInboxRepository | None = None,
    reader: InMemoryReader | None = None,
) -> GetInboxSetupUseCase:
    if repository is None:
        repository = (
            InMemoryUserInboxRepository()
            if inbox is None
            else InMemoryUserInboxRepository(inbox)
        )

    return GetInboxSetupUseCase(
        inbox_repository=repository,
        notification_reader=(
            reader if reader is not None else InMemoryReader(USER, *notifications)
        ),
        base_address=BASE_ADDRESS,
    )


def test_a_new_account_is_pointed_at_approving_a_sender() -> None:
    view = _use_case(_inbox()).execute(USER)

    assert view is not None
    assert view.address == ALIAS
    assert view.setup.current is SetupStep.SENDERS_APPROVED
    assert view.setup.ready is False


def test_a_finished_setup_has_nothing_left_to_do() -> None:
    view = _use_case(
        _inbox(
            domains=frozenset({BANK_DOMAIN}),
            forwarding_confirmed_at=NOW,
            first_accepted_at=NOW,
        ),
    ).execute(USER)

    assert view is not None
    assert view.setup.ready is True
    assert view.setup.current is None


def test_mail_from_an_unapproved_sender_is_surfaced() -> None:
    """The failure a user cannot diagnose alone: everything is arriving and
    everything is being discarded, which from a screen looks like silence.
    """
    view = _use_case(
        _inbox(domains=frozenset({BANK_DOMAIN}), forwarding_confirmed_at=NOW),
        _ignored("alertas@otrobanco.com", message_id="m1"),
    ).execute(USER)

    assert view is not None
    assert view.unapproved_senders == (EmailAddress("alertas@otrobanco.com"),)


def test_a_sender_approved_since_is_no_longer_reported() -> None:
    """Its old rejections are still stored. Showing them would send the user
    round a loop they already finished.
    """
    view = _use_case(
        _inbox(domains=frozenset({BANK_DOMAIN})),
        _ignored(BANK_SENDER.value, message_id="m1"),
    ).execute(USER)

    assert view is not None
    assert view.unapproved_senders == ()


def test_the_same_sender_is_reported_once() -> None:
    view = _use_case(
        _inbox(domains=frozenset({BANK_DOMAIN})),
        _ignored("alertas@otrobanco.com", message_id="m1"),
        _ignored("alertas@otrobanco.com", message_id="m2"),
    ).execute(USER)

    assert view is not None
    assert view.unapproved_senders == (EmailAddress("alertas@otrobanco.com"),)


def test_the_list_of_rejections_is_capped() -> None:
    rejections = [
        _ignored(f"alertas{index}@otrobanco.com", message_id=f"m{index}")
        for index in range(MAX_UNAPPROVED_SENDERS + 3)
    ]
    view = _use_case(_inbox(domains=frozenset({BANK_DOMAIN})), *rejections).execute(
        USER,
    )

    assert view is not None
    assert len(view.unapproved_senders) == MAX_UNAPPROVED_SENDERS


def test_a_finished_setup_does_not_read_the_notification_table() -> None:
    """Polled while somebody watches the screen, and pointless once they are
    connected: the read walks a partition that only grows.
    """
    reader = InMemoryReader(USER, _ignored("alertas@otrobanco.com", message_id="m1"))
    use_case = _use_case(
        _inbox(
            domains=frozenset({BANK_DOMAIN}),
            forwarding_confirmed_at=NOW,
            first_accepted_at=NOW,
        ),
        reader=reader,
    )

    view = use_case.execute(USER)

    assert view is not None
    assert view.unapproved_senders == ()
    assert reader.calls == 0


def test_the_inbox_is_found_by_its_address_not_the_index() -> None:
    """The index is eventually consistent, and the first thing a new account
    does is open this screen: asked through it a moment after registration,
    it can answer that the user has no inbox at all.
    """
    repository = InMemoryUserInboxRepository(_inbox(), index_is_stale=True)

    view = _use_case(None, repository=repository).execute(USER)

    assert view is not None
    assert view.address == ALIAS
    assert repository.index_calls == 0


def test_an_inbox_whose_address_does_not_derive_is_still_found() -> None:
    """A record from before the current ingest mailbox. The index is the
    fallback precisely for it.
    """
    legacy = dataclasses.replace(
        _inbox(),
        address=EmailAddress("oldingest+11111111111111111111111111111111@gmail.com"),
    )
    repository = InMemoryUserInboxRepository(legacy)

    view = _use_case(None, repository=repository).execute(USER)

    assert view is not None
    assert view.address == legacy.address


def test_an_account_that_predates_the_milestone_is_backfilled() -> None:
    """Otherwise it reports "nothing has arrived yet" forever, and pays for a
    walk of its whole history on every poll of this screen.
    """
    repository = InMemoryUserInboxRepository(_inbox(domains=frozenset({BANK_DOMAIN})))
    earliest = PosixTime.from_epoch_seconds(1_756_000_000)
    use_case = _use_case(
        None,
        repository=repository,
        reader=InMemoryReader(
            USER,
            _accepted("m2"),
            _accepted("m1", received_at=earliest),
        ),
    )

    view = use_case.execute(USER)

    assert view is not None
    assert view.setup.ready is True
    # The earliest, not whichever came back first: the question is when this
    # account started receiving expenses.
    assert repository.inboxes[ALIAS].first_accepted_at == earliest


def test_an_account_that_only_ever_had_mail_rejected_is_not_backfilled() -> None:
    repository = InMemoryUserInboxRepository(_inbox(domains=frozenset({BANK_DOMAIN})))
    use_case = _use_case(
        None,
        repository=repository,
        reader=InMemoryReader(USER, _ignored("alertas@otrobanco.com", message_id="m1")),
    )

    view = use_case.execute(USER)

    assert view is not None
    assert view.setup.ready is False
    assert repository.inboxes[ALIAS].first_accepted_at is None


def test_the_most_recent_rejections_survive_the_cap() -> None:
    """Sorted by name instead, six banks reaching one account could push the
    one the user is waiting for off the end of the list that names it.
    """
    old = [
        _ignored(
            f"zzz-old-{index}@otrobanco.com",
            message_id=f"old-{index}",
            received_at=PosixTime.from_epoch_seconds(1_700_000_000 + index),
        )
        for index in range(MAX_UNAPPROVED_SENDERS)
    ]
    newest = _ignored(
        "alertas@elbancodelusuario.com",
        message_id="new-1",
        received_at=PosixTime.from_epoch_seconds(1_800_000_000),
    )
    view = _use_case(_inbox(domains=frozenset({BANK_DOMAIN})), *old, newest).execute(
        USER,
    )

    assert view is not None
    assert view.unapproved_senders[0] == EmailAddress("alertas@elbancodelusuario.com")
    assert len(view.unapproved_senders) == MAX_UNAPPROVED_SENDERS


def test_a_user_without_an_inbox_gets_nothing() -> None:
    assert _use_case(None).execute(USER) is None


def test_another_users_inbox_is_not_reachable() -> None:
    assert _use_case(_inbox()).execute(OTHER_USER) is None
