from collections.abc import Sequence

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxEventDelivery,
    MailboxProvider,
    MailboxTemporarilyUnavailableError,
    Subscription,
)
from personal_finance.contexts.ingestion.application.subscription_handlers import (
    MINIMUM_RENEWAL_WINDOW_SECONDS,
    KeepSubscriptionAliveUseCase,
    RenewalOutcome,
    SweepSubscriptionsUseCase,
    renewal_window_seconds,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
ADDRESS = "ana@gmail.test"
SEVEN_DAYS = 7 * 24 * 3_600


class InMemoryConnectionRepository:
    def __init__(self, *connections: MailboxConnection) -> None:
        self.connections = {(c.provider, c.address): c for c in connections}
        self.saves: list[MailboxConnection] = []

    def save(self, connection: MailboxConnection) -> None:
        self.saves.append(connection)
        self.connections[(connection.provider, connection.address)] = connection

    def list_active(self) -> Sequence[MailboxConnection]:
        return tuple(c for c in self.connections.values() if c.is_active)

    def find(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
    ) -> MailboxConnection | None:
        return self.connections.get((provider, address))

    def find_by_user(self, user_id: UserId) -> Sequence[MailboxConnection]:
        return tuple(c for c in self.connections.values() if c.user_id == user_id)


class FakeSubscriber:
    provider = MailboxProvider.SIMULATED
    delivery = MailboxEventDelivery.PUSH

    def __init__(
        self,
        *,
        lifetime_seconds: int = SEVEN_DAYS,
        raises: Exception | None = None,
        cursor: str | None = "provider-start",
    ) -> None:
        self.calls = 0
        self.lifetime_seconds = lifetime_seconds
        self._raises = raises
        self._cursor = cursor

    def subscribe(self, connection: MailboxConnection) -> Subscription:
        del connection
        self.calls += 1

        if self._raises is not None:
            raise self._raises

        return Subscription(
            expires_at=PosixTime.from_epoch_seconds(
                PosixTime.now().as_epoch_seconds() + self.lifetime_seconds,
            ),
            cursor=self._cursor,
        )

    def unsubscribe(self, connection: MailboxConnection) -> None:
        del connection


class PollingSubscriber(FakeSubscriber):
    delivery = MailboxEventDelivery.POLL


def _connection(
    *,
    expires_in_seconds: int | None = None,
    cursor: str | None = None,
    status: MailboxConnectionStatus = MailboxConnectionStatus.ACTIVE,
    address: str = ADDRESS,
) -> MailboxConnection:
    expires_at = (
        PosixTime.from_epoch_seconds(
            PosixTime.now().as_epoch_seconds() + expires_in_seconds,
        )
        if expires_in_seconds is not None
        else None
    )

    return MailboxConnection(
        user_id=USER_ID,
        address=EmailAddress(address),
        provider=MailboxProvider.SIMULATED,
        cursor=cursor,
        status=status,
        subscription_expires_at=expires_at,
    )


def _use_case(
    subscriber: FakeSubscriber,
    repository: InMemoryConnectionRepository,
) -> KeepSubscriptionAliveUseCase:
    return KeepSubscriptionAliveUseCase(
        subscribers={MailboxProvider.SIMULATED: subscriber},
        connection_repository=repository,
    )


def test_the_renewal_window_is_half_the_lifetime() -> None:
    # Renewing at the halfway point leaves the whole second half as retries,
    # so one bad afternoon cannot cost the subscription.
    assert renewal_window_seconds(lifetime_seconds=SEVEN_DAYS) == SEVEN_DAYS // 2


def test_a_very_short_lifetime_still_gets_a_real_window() -> None:
    assert renewal_window_seconds(lifetime_seconds=60) == MINIMUM_RENEWAL_WINDOW_SECONDS


def test_a_connection_that_never_subscribed_is_renewed_immediately() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(_connection())

    assert result.outcome is RenewalOutcome.RENEWED
    assert subscriber.calls == 1
    assert result.connection.subscription_expires_at is not None


def test_a_freshly_subscribed_connection_is_left_alone() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(
        _connection(expires_in_seconds=SEVEN_DAYS),
    )

    assert result.outcome is RenewalOutcome.NOT_DUE
    assert subscriber.calls == 0


def test_a_connection_past_halfway_is_renewed() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(
        # Two days left of a seven-day subscription.
        _connection(expires_in_seconds=2 * 24 * 3_600),
    )

    assert result.outcome is RenewalOutcome.RENEWED
    assert subscriber.calls == 1


def test_forcing_renews_even_when_it_is_not_due() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(
        _connection(expires_in_seconds=SEVEN_DAYS),
        force=True,
    )

    assert result.outcome is RenewalOutcome.RENEWED


def test_a_new_connection_adopts_the_providers_starting_position() -> None:
    repository = InMemoryConnectionRepository()

    result = _use_case(FakeSubscriber(), repository).execute(_connection())

    assert result.connection.cursor == "provider-start"


def test_an_existing_position_is_never_overwritten_by_a_renewal() -> None:
    repository = InMemoryConnectionRepository()

    result = _use_case(FakeSubscriber(), repository).execute(
        _connection(cursor="ours-42"),
    )

    # Taking the provider's current position would skip everything that
    # arrived while we were not subscribed.
    assert result.connection.cursor == "ours-42"


def test_a_revoked_grant_flags_the_connection_and_stops_retrying() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber(raises=MailboxAccessRevokedError("gone"))

    result = _use_case(subscriber, repository).execute(_connection())

    assert result.outcome is RenewalOutcome.NEEDS_REAUTH
    assert result.connection.status is MailboxConnectionStatus.NEEDS_REAUTH
    assert repository.saves[-1].status is MailboxConnectionStatus.NEEDS_REAUTH


def test_a_transient_failure_changes_nothing() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber(raises=MailboxTemporarilyUnavailableError("503"))
    original = _connection(expires_in_seconds=60)

    result = _use_case(subscriber, repository).execute(original)

    # Renewing early is exactly what buys the room to simply try again.
    assert result.outcome is RenewalOutcome.DEFERRED
    assert repository.saves == []
    assert result.connection.status is MailboxConnectionStatus.ACTIVE


def test_a_mailbox_awaiting_reauthorization_is_not_renewed() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(
        _connection(status=MailboxConnectionStatus.NEEDS_REAUTH),
    )

    assert result.outcome is RenewalOutcome.NOT_APPLICABLE
    assert subscriber.calls == 0


def test_a_disconnected_mailbox_is_never_quietly_reopened() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = FakeSubscriber()

    result = _use_case(subscriber, repository).execute(
        _connection(status=MailboxConnectionStatus.REVOKED),
    )

    assert result.outcome is RenewalOutcome.NOT_APPLICABLE
    assert subscriber.calls == 0


def test_a_polling_provider_has_nothing_to_renew() -> None:
    repository = InMemoryConnectionRepository()
    subscriber = PollingSubscriber()

    result = KeepSubscriptionAliveUseCase(
        subscribers={MailboxProvider.SIMULATED: subscriber},
        connection_repository=repository,
    ).execute(_connection())

    assert result.outcome is RenewalOutcome.NOT_APPLICABLE
    assert subscriber.calls == 0


def test_the_sweep_renews_only_what_is_due() -> None:
    repository = InMemoryConnectionRepository(
        _connection(address="due@gmail.test", expires_in_seconds=60),
        _connection(address="fine@gmail.test", expires_in_seconds=SEVEN_DAYS),
        _connection(address="new@gmail.test"),
    )
    subscriber = FakeSubscriber()
    sweep = SweepSubscriptionsUseCase(
        connection_repository=repository,
        keep_alive=_use_case(subscriber, repository),
    )

    result = sweep.execute()

    assert (result.renewed, result.not_due) == (2, 1)
    assert subscriber.calls == 2


def test_the_sweep_reports_mailboxes_that_need_the_user() -> None:
    repository = InMemoryConnectionRepository(_connection())
    subscriber = FakeSubscriber(raises=MailboxAccessRevokedError("gone"))
    sweep = SweepSubscriptionsUseCase(
        connection_repository=repository,
        keep_alive=_use_case(subscriber, repository),
    )

    result = sweep.execute()

    assert result.needs_reauth == 1


def test_the_sweep_skips_mailboxes_the_user_turned_off() -> None:
    repository = InMemoryConnectionRepository(
        _connection(status=MailboxConnectionStatus.REVOKED),
    )
    subscriber = FakeSubscriber()
    sweep = SweepSubscriptionsUseCase(
        connection_repository=repository,
        keep_alive=_use_case(subscriber, repository),
    )

    result = sweep.execute()

    assert (result.renewed, result.not_due) == (0, 0)
    assert subscriber.calls == 0
