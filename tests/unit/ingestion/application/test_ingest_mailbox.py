from collections.abc import Sequence

import pytest

from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
)
from personal_finance.contexts.ingestion.application.ingest_handlers import (
    PollIngestMailboxUseCase,
)
from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.ports import InboundEmail
from personal_finance.contexts.ingestion.domain.entities import (
    BankNotification,
    UserInbox,
)
from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    ForwardingConfirmation,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
ALIAS = f"finflowingest+{USER_ID.value.hex}@gmail.com"
BANK_DOMAIN = "an.notificacionesbancolombia.com"
BANK_SENDER = f"alertasynotificaciones@{BANK_DOMAIN}"


def _email(*, message_id: str, sender: str = BANK_SENDER) -> InboundEmail:
    return InboundEmail(
        recipient=EmailAddress(ALIAS),
        sender=EmailAddress(sender),
        message_id=EmailMessageId(message_id),
        subject="Alertas y Notificaciones",
        raw_content="Bancolombia: Compraste COP29.259,00 en TIENDAS ARA",
        received_at=PosixTime.now(),
    )


class FakeReader:
    def __init__(self, *emails: InboundEmail) -> None:
        self._emails = list(emails)
        self.acked: list[InboundEmail] = []
        self.ack_calls = 0

    def fetch_new(self) -> Sequence[InboundEmail]:
        return tuple(self._emails)

    def ack(self, emails: Sequence[InboundEmail]) -> None:
        self.ack_calls += 1
        self.acked.extend(emails)


class InMemoryUserInboxRepository:
    def __init__(self, *inboxes: UserInbox) -> None:
        self.inboxes = {inbox.address: inbox for inbox in inboxes}

    def find_by_address(self, address: EmailAddress) -> UserInbox | None:
        return self.inboxes.get(address)

    def find_by_user(self, user_id: UserId) -> list[UserInbox]:
        return [inbox for inbox in self.inboxes.values() if inbox.user_id == user_id]

    def save(self, inbox: UserInbox) -> None:
        self.inboxes[inbox.address] = inbox


class InMemoryBankNotificationRepository:
    def __init__(self) -> None:
        self.saved: dict[IdempotencyKey, BankNotification] = {}

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        existing = self.saved.get(notification.idempotency_key)

        if existing is not None:
            return existing

        self.saved[notification.idempotency_key] = notification

        return None

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        return self.saved.get(idempotency_key)

    def save(self, notification: BankNotification) -> None:
        self.saved[notification.idempotency_key] = notification


class NullQueuePublisher:
    def __init__(self) -> None:
        self.enqueued: list[ParseNotificationMessage] = []

    def enqueue(self, message: ParseNotificationMessage) -> None:
        self.enqueued.append(message)


class FlakyQueuePublisher(NullQueuePublisher):
    """Raises once, for the one notification whose message id matches."""

    def __init__(self, *, fails_message_id: str) -> None:
        super().__init__()
        self._fails_message_id = fails_message_id

    def enqueue(self, message: ParseNotificationMessage) -> None:
        if message.message_id.value == self._fails_message_id:
            raise RuntimeError("queue is down")

        super().enqueue(message)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


def _inbox(*, domains: frozenset[str] = frozenset({BANK_DOMAIN})) -> UserInbox:
    return UserInbox(
        user_id=USER_ID,
        address=EmailAddress(ALIAS),
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
    )


class FakeConfirmer:
    """Records what it was asked to confirm, and answers however told to."""

    def __init__(self, *, accepts: bool = True, raises: bool = False) -> None:
        self.confirmed: list[str] = []
        self._accepts = accepts
        self._raises = raises

    def confirm(self, confirmation: ForwardingConfirmation) -> bool:
        if self._raises:
            raise RuntimeError("network is down")

        self.confirmed.append(confirmation.url)

        return self._accepts


def _make(
    reader: FakeReader,
    *,
    inbox: UserInbox | None = None,
    queue: NullQueuePublisher | None = None,
    confirmer: FakeConfirmer | None = None,
) -> tuple[PollIngestMailboxUseCase, NullQueuePublisher]:
    queue = queue if queue is not None else NullQueuePublisher()
    inboxes = InMemoryUserInboxRepository(inbox if inbox is not None else _inbox())

    use_case = PollIngestMailboxUseCase(
        forwarding_confirmer=confirmer if confirmer is not None else FakeConfirmer(),
        reader=reader,
        receive_use_case=ReceiveBankNotificationUseCase(
            repository=InMemoryBankNotificationRepository(),
            inbox_repository=inboxes,
            queue_publisher=queue,
            event_publisher=NullEventPublisher(),
        ),
    )

    return use_case, queue


def test_fetched_email_is_ingested_and_acknowledged() -> None:
    reader = FakeReader(_email(message_id="m1"), _email(message_id="m2"))
    use_case, queue = _make(reader)

    result = use_case.execute()

    assert (result.fetched, result.accepted) == (2, 2)
    assert len(queue.enqueued) == 2
    assert len(reader.acked) == 2


def test_a_message_from_an_unapproved_sender_is_recorded_but_not_queued() -> None:
    reader = FakeReader(_email(message_id="m1", sender="someone@else.com"))
    use_case, queue = _make(reader)

    result = use_case.execute()

    assert result.accepted == 1  # still recorded, just ignored downstream
    assert queue.enqueued == []
    assert len(reader.acked) == 1


def test_a_message_for_an_unregistered_alias_is_acknowledged_and_not_recorded() -> None:
    stranger = InboundEmail(
        recipient=EmailAddress("finflowingest+doesnotexist@gmail.com"),
        sender=EmailAddress(BANK_SENDER),
        message_id=EmailMessageId("m1"),
        subject="x",
        raw_content="x",
        received_at=PosixTime.now(),
    )
    reader = FakeReader(stranger)
    use_case, queue = _make(reader)

    result = use_case.execute()

    assert result.unknown_recipient == 1
    assert queue.enqueued == []
    assert len(reader.acked) == 1


def test_a_redelivered_message_is_counted_as_duplicate_and_still_acknowledged() -> None:
    same = _email(message_id="m1")
    reader = FakeReader(same, same)
    use_case, queue = _make(reader)

    result = use_case.execute()

    assert (result.accepted, result.duplicates) == (1, 1)
    assert len(queue.enqueued) == 1
    assert len(reader.acked) == 2


def test_nothing_new_leaves_nothing_to_acknowledge() -> None:
    use_case, queue = _make(FakeReader())

    result = use_case.execute()

    assert result.fetched == 0
    assert queue.enqueued == []


def test_acknowledging_happens_once_per_poll_not_once_per_message() -> None:
    reader = FakeReader(_email(message_id="m1"), _email(message_id="m2"))
    use_case, _ = _make(reader)

    use_case.execute()

    assert reader.ack_calls == 1
    assert len(reader.acked) == 2


def test_a_message_that_fails_to_record_is_left_unacknowledged_for_retry() -> None:
    reader = FakeReader(_email(message_id="m1"), _email(message_id="m2"))
    queue = FlakyQueuePublisher(fails_message_id="m1")
    use_case, _ = _make(reader, queue=queue)

    result = use_case.execute()

    assert result.failed == 1
    assert result.accepted == 1
    # Only the message that was actually recorded gets acknowledged — the
    # failed one must stay looking new so the next poll retries it.
    assert [email.message_id.value for email in reader.acked] == ["m2"]


def test_one_failing_message_does_not_abandon_the_rest_of_the_batch() -> None:
    reader = FakeReader(
        _email(message_id="m1"),
        _email(message_id="m2"),
        _email(message_id="m3"),
    )
    queue = FlakyQueuePublisher(fails_message_id="m2")
    use_case, _ = _make(reader, queue=queue)

    result = use_case.execute()

    assert result.accepted == 2
    assert result.failed == 1
    assert len(queue.enqueued) == 2


_CONFIRMATION_URL = "https://mail-settings.google.com/mail/vf-%5BANGjdJ-abc%5D-def"


def _confirmation_email(message_id: str = "google-1") -> InboundEmail:
    return InboundEmail(
        recipient=EmailAddress(ALIAS),
        sender=EmailAddress("forwarding-noreply@google.com"),
        message_id=EmailMessageId(message_id),
        subject="Confirmación de reenvío",
        raw_content=(
            "haz clic en el siguiente vínculo para confirmar la solicitud:\n"
            f"{_CONFIRMATION_URL}\n"
        ),
        received_at=PosixTime.now(),
    )


def test_a_forwarding_request_is_confirmed_and_never_reaches_the_parser() -> None:
    """The whole point: nobody has to fish this link out of the mailbox.

    It must not take the bank-notification path either — that path would file
    it under an unapproved sender and discard its body, link included.
    """
    reader = FakeReader(_confirmation_email())
    confirmer = FakeConfirmer()
    use_case, queue = _make(reader, confirmer=confirmer)

    result = use_case.execute()

    assert confirmer.confirmed == [_CONFIRMATION_URL]
    assert result.confirmations == 1
    # Not a notification by any counter, and nothing queued for parsing.
    assert result.accepted == 0
    assert queue.enqueued == []
    assert len(reader.acked) == 1


def test_a_confirmation_that_could_not_be_reached_is_left_for_the_next_poll() -> None:
    """The link stays valid for days; a network blip should cost a retry, not
    somebody's setup.
    """
    reader = FakeReader(_confirmation_email())
    use_case, _ = _make(reader, confirmer=FakeConfirmer(raises=True))

    result = use_case.execute()

    assert result.failed == 1
    assert result.confirmations == 0
    # Unacknowledged, so the next poll still finds it new.
    assert reader.acked == []


def test_a_link_google_refuses_is_not_retried_forever() -> None:
    """An expired or already-used link answers this way every time.

    Acknowledged, so it is not retried — but never counted as a confirmation:
    `confirmations` is what tells an operator a user's forwarding is set up,
    and this one is not.
    """
    reader = FakeReader(_confirmation_email())
    use_case, _ = _make(reader, confirmer=FakeConfirmer(accepts=False))

    result = use_case.execute()

    assert result.confirmations == 0
    assert result.refused_confirmations == 1
    assert len(reader.acked) == 1


def test_bank_mail_in_the_same_batch_is_unaffected() -> None:
    reader = FakeReader(_confirmation_email(), _email(message_id="bank-1"))
    confirmer = FakeConfirmer()
    use_case, queue = _make(reader, confirmer=confirmer)

    result = use_case.execute()

    assert result.confirmations == 1
    assert result.accepted == 1
    assert len(queue.enqueued) == 1
    assert len(reader.acked) == 2


def test_mail_forging_googles_address_still_cannot_reach_the_ledger() -> None:
    """Anyone can write that `From`. It buys a fetch of a pinned Google URL and
    nothing else — in particular it does not become a transaction.
    """
    forged = InboundEmail(
        recipient=EmailAddress(ALIAS),
        sender=EmailAddress("forwarding-noreply@google.com"),
        message_id=EmailMessageId("forged-1"),
        subject="Confirmación de reenvío",
        raw_content="Bancolombia: Compraste COP1.000.000,00 en ATACANTE",
        received_at=PosixTime.now(),
    )
    reader = FakeReader(forged)
    confirmer = FakeConfirmer()
    use_case, queue = _make(reader, confirmer=confirmer)

    result = use_case.execute()

    # No confirmation link in it, so it falls through to the ordinary path —
    # where Google is not an approved sender, so it is filed and ignored.
    assert confirmer.confirmed == []
    assert result.confirmations == 0
    assert queue.enqueued == []


def test_unreadable_mail_does_not_abandon_the_rest_of_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading a message is parsing untrusted input, so it can raise.

    When it does, the bank mail behind it must still be recorded and
    acknowledged. Left outside the guard, one such message would abort the
    pass before `ack` ran, so nothing would ever be acknowledged and the same
    batch would come back forever.
    """
    poison = InboundEmail(
        recipient=EmailAddress(ALIAS),
        sender=EmailAddress(BANK_SENDER),
        message_id=EmailMessageId("poison-1"),
        subject="Alertas y Notificaciones",
        raw_content="whatever it is about this one that cannot be read",
        received_at=PosixTime.now(),
    )
    reader = FakeReader(poison, _email(message_id="bank-1"))
    real = ForwardingConfirmation.from_email

    def explode(*, sender: EmailAddress, raw_content: str) -> object:
        if raw_content == poison.raw_content:
            raise ValueError("unreadable")

        return real(sender=sender, raw_content=raw_content)

    monkeypatch.setattr(ForwardingConfirmation, "from_email", explode)

    use_case, queue = _make(reader)

    result = use_case.execute()

    assert result.failed == 1
    assert result.accepted == 1
    assert len(queue.enqueued) == 1
    # The poison message alone stays unacknowledged.
    assert [email.message_id.value for email in reader.acked] == ["bank-1"]
