"""Plan B: what happens when no deterministic template recognises an alert.

The deterministic path is covered in `test_parse_notification.py`. These pin
down the handover to the model, and above all that a model which is merely
unreachable never turns into a permanent outcome for a real email.
"""

from collections.abc import Sequence
import copy
import datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.application.parsing_handlers import (
    ParseNotificationUseCase,
    ParseOutcome,
    StandingContentionError,
)
from personal_finance.contexts.ingestion.application.ports import FallbackReading
from personal_finance.contexts.ingestion.domain.entities import BankNotification
from personal_finance.contexts.ingestion.domain.events import ModelReadingSuspended
from personal_finance.contexts.ingestion.domain.model_standing import ModelStanding
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    IdempotencyKey,
    NotificationDeferredReason,
    NotificationId,
    ProcessingStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.llm.errors import (
    LLMTemporarilyUnavailableError,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
BANK_SENDER = "alertasynotificaciones@an.notificacionesbancolombia.com"
UNKNOWN_BANK = "alertas@otrobanco.com.co"
# A wording no template covers.
UNRECOGNISED = "Tu compra en LA TIENDA DE LA ESQUINA por $12.500 fue aprobada."
RECOGNISED = (
    "Bancolombia: Compraste COP29.259,00 en TIENDAS ARA con tu T.Cred *7653, "
    "el 20/08/2026 a las 12:00."
)


class InMemoryRepository:
    """Copies on the way in and out, the way real storage does.

    Handing back the same object the use case is mutating would make an
    unsaved change look persisted — which is exactly the distinction the
    outage test exists to check.
    """

    def __init__(self, *notifications: BankNotification) -> None:
        self.stored = {
            notification.idempotency_key: copy.deepcopy(notification)
            for notification in notifications
        }
        self.saves = 0

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        return self.stored.setdefault(
            notification.idempotency_key,
            copy.deepcopy(notification),
        )

    def get(self, idempotency_key: IdempotencyKey) -> BankNotification | None:
        stored = self.stored.get(idempotency_key)

        return copy.deepcopy(stored) if stored is not None else None

    def save(self, notification: BankNotification) -> None:
        self.saves += 1
        self.stored[notification.idempotency_key] = copy.deepcopy(notification)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


class StubExtractor:
    """Stands in for the model, with the port's exact contract."""

    def __init__(
        self,
        *,
        transaction: ExtractedTransaction | None = None,
        error: Exception | None = None,
        not_bank_mail: bool = False,
    ) -> None:
        self.transaction = transaction
        self.error = error
        self.not_bank_mail = not_bank_mail
        self.calls = 0

    def extract(
        self,
        *,
        sender: EmailAddress,
        subject: str,
        body: str,
        received_at: PosixTime,
    ) -> FallbackReading:
        del sender, subject, body, received_at
        self.calls += 1

        if self.error is not None:
            raise self.error

        return FallbackReading(
            transaction=self.transaction,
            not_bank_mail=self.not_bank_mail,
        )


class StubStandings:
    """One standing per person, copied in and out like storage, with a
    version check and a way to lose it a given number of times."""

    def __init__(self, *standings: ModelStanding, conflicts: int = 0) -> None:
        self.stored = {
            standing.user_id: copy.deepcopy(standing) for standing in standings
        }
        self.conflicts = conflicts
        self.saves = 0

    def get(self, user_id: UserId) -> ModelStanding:
        stored = self.stored.get(user_id)

        return copy.deepcopy(stored) if stored else ModelStanding.clean(user_id)

    def save(self, standing: ModelStanding) -> bool:
        if self.conflicts:
            self.conflicts -= 1

            return False

        self.saves += 1
        standing.version += 1
        self.stored[standing.user_id] = copy.deepcopy(standing)

        return True


class StubQuota:
    """Stands in for the day's model reads: grants or refuses, and records."""

    def __init__(self, *, grants: bool = True) -> None:
        self.grants = grants
        self.reservations: list[tuple[UserId, NotificationId, datetime.date]] = []
        self.releases: list[tuple[UserId, NotificationId, datetime.date]] = []

    def reserve(
        self,
        *,
        user_id: UserId,
        notification_id: NotificationId,
        day: datetime.date,
    ) -> bool:
        self.reservations.append((user_id, notification_id, day))

        return self.grants

    def release(
        self,
        *,
        user_id: UserId,
        notification_id: NotificationId,
        day: datetime.date,
    ) -> None:
        self.releases.append((user_id, notification_id, day))


def _transaction() -> ExtractedTransaction:
    return ExtractedTransaction(
        kind=TransactionKind.CARD_PURCHASE,
        direction=TransactionDirection.OUTGOING,
        amount=Money(amount=Decimal("12500"), currency=Currency.COP),
        occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
        counterparty="LA TIENDA DE LA ESQUINA",
        bank="otro banco",
    )


def _notification(
    *,
    sender: str = UNKNOWN_BANK,
    raw_content: str = UNRECOGNISED,
    message_id: str = "message-1",
) -> BankNotification:
    notification = BankNotification.receive(
        user_id=USER_ID,
        message_id=EmailMessageId(message_id),
        sender=EmailAddress(sender),
        subject="Notificación",
        raw_content=raw_content,
        received_at=PosixTime.now(),
    )
    notification.mark_as_queued()
    notification.pull_events()

    return notification


def _message(notification: BankNotification) -> ParseNotificationMessage:
    return ParseNotificationMessage(
        notification_id=notification.id,
        user_id=notification.user_id,
        idempotency_key=notification.idempotency_key,
        message_id=notification.message_id,
        received_at=notification.received_at,
    )


def _make(
    notification: BankNotification,
    extractor: StubExtractor | None,
    *,
    quota: StubQuota | None = None,
    standings: StubStandings | None = None,
) -> tuple[ParseNotificationUseCase, InMemoryRepository, RecordingEventPublisher]:
    repository = InMemoryRepository(notification)
    publisher = RecordingEventPublisher()
    use_case = ParseNotificationUseCase(
        repository=repository,
        registry=ParserRegistry(),
        event_publisher=publisher,
        model_quota=quota or StubQuota(),
        standings=standings if standings is not None else StubStandings(),
        fallback_extractor=extractor,
    )

    return use_case, repository, publisher


def test_without_a_model_an_unrecognised_alert_is_kept_for_later() -> None:
    notification = _notification()
    use_case, repository, _ = _make(notification, None)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PENDING_FALLBACK
    assert stored.deferred_reason is NotificationDeferredReason.NO_FALLBACK_CONFIGURED


def test_the_model_reads_an_alert_no_template_matched() -> None:
    notification = _notification()
    extractor = StubExtractor(transaction=_transaction())
    use_case, repository, publisher = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK
    assert result.status is ProcessingStatus.PROCESSED
    # Downstream cannot tell it apart from a template extraction, which is the
    # point: the merchant and financial contexts see one kind of event.
    assert [type(event).__name__ for event in publisher.published] == [
        "TransactionExtracted",
    ]
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PROCESSED


def test_a_template_that_matches_never_reaches_the_model() -> None:
    notification = _notification(sender=BANK_SENDER, raw_content=RECOGNISED)
    extractor = StubExtractor(transaction=_transaction())
    use_case, _, _ = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.EXTRACTED
    # Deterministic first is not a preference, it is the order the pipeline
    # depends on: cheap, repeatable, auditable.
    assert extractor.calls == 0


def test_a_known_bank_with_an_unknown_wording_still_reaches_the_model() -> None:
    # The sender has templates, but this particular alert matches none of them.
    notification = _notification(sender=BANK_SENDER, raw_content=UNRECOGNISED)
    extractor = StubExtractor(transaction=_transaction())
    use_case, _, _ = _make(notification, extractor)

    result = use_case.execute(_message(notification))

    assert extractor.calls == 1
    assert result.outcome is ParseOutcome.EXTRACTED_BY_FALLBACK


def test_a_model_that_declines_the_email_keeps_it_rather_than_failing_it() -> None:
    notification = _notification()
    use_case, repository, _ = _make(notification, StubExtractor(transaction=None))

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PENDING_FALLBACK
    assert stored.deferred_reason is NotificationDeferredReason.FALLBACK_FOUND_NOTHING


def test_a_model_outage_leaves_the_email_exactly_where_it_was() -> None:
    notification = _notification()
    extractor = StubExtractor(error=LLMTemporarilyUnavailableError("overloaded"))
    use_case, repository, publisher = _make(notification, extractor)

    with pytest.raises(LLMTemporarilyUnavailableError):
        use_case.execute(_message(notification))

    # Nothing was written, so the stored notification is still QUEUED and the
    # redelivered SQS message tries again. Recording a permanent outcome for a
    # temporary outage would quietly drop a real transaction.
    assert repository.saves == 0
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.QUEUED
    assert publisher.published == []


def test_past_the_days_limit_the_model_is_not_called_and_the_email_is_kept() -> None:
    notification = _notification()
    extractor = StubExtractor(transaction=_transaction())
    use_case, repository, _ = _make(
        notification,
        extractor,
        quota=StubQuota(grants=False),
    )

    result = use_case.execute(_message(notification))

    assert extractor.calls == 0
    assert result.outcome is ParseOutcome.DEFERRED
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.PENDING_FALLBACK
    assert stored.deferred_reason is NotificationDeferredReason.DAILY_LIMIT_REACHED
    # A cost limit, not a verdict on the email: the body stays readable.
    assert stored.raw_content == UNRECOGNISED


def test_a_read_is_reserved_for_this_email_on_todays_bogota_date() -> None:
    notification = _notification()
    quota = StubQuota()
    use_case, _, _ = _make(
        notification,
        StubExtractor(transaction=_transaction()),
        quota=quota,
    )

    use_case.execute(_message(notification))

    [(user_id, notification_id, day)] = quota.reservations
    assert user_id == USER_ID
    assert notification_id == notification.id
    bogota = datetime.timezone(datetime.timedelta(hours=-5))
    assert day == datetime.datetime.now(bogota).date()


@pytest.mark.parametrize(
    ("sender", "raw_content", "extractor"),
    [
        # A template read it: templates cost nothing.
        (BANK_SENDER, RECOGNISED, StubExtractor(transaction=_transaction())),
        # There is no model to spend.
        (UNKNOWN_BANK, UNRECOGNISED, None),
    ],
    ids=["template", "no-model"],
)
def test_nothing_but_a_model_call_spends_a_read(
    sender: str,
    raw_content: str,
    extractor: StubExtractor | None,
) -> None:
    notification = _notification(sender=sender, raw_content=raw_content)
    quota = StubQuota()
    use_case, _, _ = _make(notification, extractor, quota=quota)

    use_case.execute(_message(notification))

    assert quota.reservations == []


def test_a_model_that_is_down_hands_its_read_back() -> None:
    notification = _notification()
    quota = StubQuota()
    use_case, _, _ = _make(
        notification,
        StubExtractor(error=LLMTemporarilyUnavailableError("overloaded")),
        quota=quota,
    )

    with pytest.raises(LLMTemporarilyUnavailableError):
        use_case.execute(_message(notification))

    # An outage must not spend the day's reads on emails nobody read: the
    # redelivered message reserves again.
    assert quota.releases == quota.reservations


def test_a_read_the_model_delivered_is_never_handed_back() -> None:
    notification = _notification()
    quota = StubQuota()
    use_case, _, _ = _make(notification, StubExtractor(transaction=None), quota=quota)

    use_case.execute(_message(notification))

    # Declining is an answer, and it was paid for.
    assert quota.releases == []


# ------------------------------------------------------------------ strikes


def _paused_standing(*, revoked: bool = False) -> ModelStanding:
    standing = ModelStanding.clean(USER_ID)
    standing.revoked = revoked
    standing.paused_until = (
        None if revoked else PosixTime.from_epoch_seconds(4_102_444_800)  # 2100
    )

    return standing


@pytest.mark.parametrize("revoked", [False, True])
def test_a_suspended_person_is_never_read_and_the_email_is_dropped(
    revoked: bool,
) -> None:
    notification = _notification()
    extractor = StubExtractor(transaction=_transaction())
    quota = StubQuota()
    use_case, repository, _ = _make(
        notification,
        extractor,
        quota=quota,
        standings=StubStandings(_paused_standing(revoked=revoked)),
    )

    result = use_case.execute(_message(notification))

    assert result.outcome is ParseOutcome.DEFERRED
    assert extractor.calls == 0
    # A suspended person spends none of the day's reads either.
    assert quota.reservations == []
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.deferred_reason is NotificationDeferredReason.MODEL_SUSPENDED
    assert stored.raw_content == ""


def test_a_template_still_reads_for_a_suspended_person() -> None:
    notification = _notification(sender=BANK_SENDER, raw_content=RECOGNISED)
    use_case, _, _ = _make(
        notification,
        StubExtractor(),
        standings=StubStandings(_paused_standing(revoked=True)),
    )

    assert use_case.execute(_message(notification)).outcome is ParseOutcome.EXTRACTED


def test_an_email_that_is_not_from_a_bank_counts_against_its_owner() -> None:
    notification = _notification()
    standings = StubStandings()
    use_case, repository, _ = _make(
        notification,
        StubExtractor(not_bank_mail=True),
        standings=standings,
    )

    use_case.execute(_message(notification))

    assert set(standings.get(USER_ID).offenses) == {notification.id}
    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.deferred_reason is NotificationDeferredReason.FALLBACK_FOUND_NOTHING


def test_a_bank_email_the_model_declines_counts_for_nothing() -> None:
    notification = _notification()
    standings = StubStandings()
    use_case, _, _ = _make(notification, StubExtractor(), standings=standings)

    use_case.execute(_message(notification))

    assert standings.saves == 0


def test_the_tenth_in_an_hour_publishes_the_suspension() -> None:
    standings = StubStandings()
    published: list[Event] = []

    for index in range(10):
        notification = _notification(message_id=f"message-{index}")
        use_case, _, publisher = _make(
            notification,
            StubExtractor(not_bank_mail=True),
            standings=standings,
        )
        use_case.execute(_message(notification))
        published.extend(publisher.published)

    [suspension] = [
        event for event in published if isinstance(event, ModelReadingSuspended)
    ]
    assert suspension.strike == 1
    assert suspension.user_id == USER_ID


def test_a_record_another_worker_changed_is_read_again() -> None:
    notification = _notification()
    standings = StubStandings(conflicts=2)
    use_case, _, _ = _make(
        notification,
        StubExtractor(not_bank_mail=True),
        standings=standings,
    )

    use_case.execute(_message(notification))

    assert set(standings.get(USER_ID).offenses) == {notification.id}


def test_a_record_that_never_settles_sends_the_email_back_to_the_queue() -> None:
    notification = _notification()
    use_case, repository, _ = _make(
        notification,
        StubExtractor(not_bank_mail=True),
        standings=StubStandings(conflicts=100),
    )

    with pytest.raises(StandingContentionError):
        use_case.execute(_message(notification))

    stored = repository.get(notification.idempotency_key)
    assert stored is not None
    assert stored.status is ProcessingStatus.QUEUED


def test_an_offense_already_counted_writes_nothing_again() -> None:
    notification = _notification()
    standings = StubStandings()
    extractor = StubExtractor(not_bank_mail=True)
    use_case, _, _ = _make(notification, extractor, standings=standings)
    use_case.execute(_message(notification))

    # The same email, read again after a failed save further down.
    retry, _, _ = _make(notification, extractor, standings=standings)
    retry.execute(_message(notification))

    assert standings.saves == 1
