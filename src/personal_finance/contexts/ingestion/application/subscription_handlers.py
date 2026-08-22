"""Keeping the provider talking to us.

Every push provider expires its subscription, and none of them warn you: the
notifications simply stop. Nothing errors, nothing retries, and the mailbox
looks healthy right up until someone notices their spending stopped updating
a week ago.

The strategy here is deliberately redundant. Renewal is driven from three
independent places, so no single one failing is enough to go silent:

* a scheduled sweep, which is the floor;
* every notification we handle, which keeps an active mailbox alive for free;
* the user opening the application, which is the safety net that works even
  when nothing scheduled runs at all.

All three call the same use case, and it is safe to call at any time — asking
early is free, and asking twice is a renewal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import enum
import logging
from typing import Protocol

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxEventDelivery,
    MailboxProvider,
    MailboxTemporarilyUnavailableError,
)
from personal_finance.contexts.ingestion.application.ports import (
    MailboxConnectionRepository,
    MailboxSubscriber,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_logger = logging.getLogger(__name__)

# Renew once half the subscription's life has gone, rather than at the last
# moment. Gmail's seven days become a daily renewal with six days of retries
# still in hand, so one bad afternoon cannot cost the subscription.
RENEWAL_FRACTION = 0.5

# The floor for that window. A provider handing out a very short subscription
# must still be renewed with real time to spare.
MINIMUM_RENEWAL_WINDOW_SECONDS = 3_600


class RenewalOutcome(enum.Enum):
    RENEWED = "renewed"
    # Still comfortably subscribed; asking again would be waste.
    NOT_DUE = "not_due"
    # The grant is gone. The user has to authorize again, and until they do
    # there is nothing to retry.
    NEEDS_REAUTH = "needs_reauth"
    # The provider had a bad moment. Left alone; the next pass tries again.
    DEFERRED = "deferred"
    # Nothing subscribes for this provider — it is polled, not pushed.
    NOT_APPLICABLE = "not_applicable"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RenewalResult:
    outcome: RenewalOutcome
    connection: MailboxConnection


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SweepResult:
    renewed: int = 0
    not_due: int = 0
    needs_reauth: int = 0
    deferred: int = 0


def renewal_window_seconds(*, lifetime_seconds: int) -> int:
    return max(
        MINIMUM_RENEWAL_WINDOW_SECONDS,
        int(lifetime_seconds * RENEWAL_FRACTION),
    )


class KeepSubscriptionAliveUseCase:
    """Renews one mailbox's subscription if it is time, and records what the
    provider said.

    Cheap to call speculatively: a connection that is comfortably subscribed
    costs one comparison and no network.
    """

    def __init__(
        self,
        *,
        subscribers: Mapping[MailboxProvider, MailboxSubscriber],
        connection_repository: MailboxConnectionRepository,
    ) -> None:
        self._subscribers = subscribers
        self._connection_repository = connection_repository

    def execute(
        self,
        connection: MailboxConnection,
        *,
        force: bool = False,
    ) -> RenewalResult:
        subscriber = self._subscribers.get(connection.provider)

        if subscriber is None or subscriber.delivery is MailboxEventDelivery.POLL:
            return RenewalResult(
                outcome=RenewalOutcome.NOT_APPLICABLE,
                connection=connection,
            )

        if not connection.is_active:
            # Renewing a mailbox the user turned off would quietly reopen it.
            return RenewalResult(
                outcome=RenewalOutcome.NOT_APPLICABLE,
                connection=connection,
            )

        window = renewal_window_seconds(
            lifetime_seconds=subscriber.lifetime_seconds,
        )

        if not force and not connection.subscription_expires_within(
            now=PosixTime.now(),
            seconds=window,
        ):
            return RenewalResult(outcome=RenewalOutcome.NOT_DUE, connection=connection)

        return self._renew(connection, subscriber=subscriber)

    def _renew(
        self,
        connection: MailboxConnection,
        *,
        subscriber: MailboxSubscriber,
    ) -> RenewalResult:
        try:
            subscription = subscriber.subscribe(connection)
        except MailboxAccessRevokedError:
            _logger.warning(
                "mailbox needs reauthorization",
                extra={
                    "provider": connection.provider.value,
                    "user_id": str(connection.user_id.value),
                },
            )
            flagged = dataclasses.replace(
                connection,
                status=MailboxConnectionStatus.NEEDS_REAUTH,
            )
            self._connection_repository.save(flagged)

            return RenewalResult(
                outcome=RenewalOutcome.NEEDS_REAUTH,
                connection=flagged,
            )
        except MailboxTemporarilyUnavailableError:
            # Left exactly as it was. Renewing early is what buys the room to
            # simply try again later.
            _logger.info(
                "subscription renewal deferred",
                extra={"provider": connection.provider.value},
            )

            return RenewalResult(
                outcome=RenewalOutcome.DEFERRED,
                connection=connection,
            )

        renewed = dataclasses.replace(
            connection,
            subscription_expires_at=subscription.expires_at,
            # A provider's starting position is only adopted when we have
            # none. Overwriting a cursor we already hold would skip whatever
            # arrived while we were not subscribed.
            cursor=connection.cursor or subscription.cursor,
        )
        self._connection_repository.save(renewed)

        return RenewalResult(outcome=RenewalOutcome.RENEWED, connection=renewed)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UserRefreshSummary:
    mailboxes: int = 0
    fetched: int = 0
    accepted: int = 0
    needs_reauth: int = 0


class RefreshUserMailboxesUseCase:
    """Catches one user's mailboxes up, and renews them on the way past.

    Meant to be called when that user opens the application. It is the safety
    net that does not depend on anything scheduled running at all: the moment
    someone looks at their finances is the moment the data most needs to be
    current, and a cursor-based read costs nothing when nothing changed.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
        sync_use_case: SyncMailboxRunner,
        keep_alive: KeepSubscriptionAliveUseCase,
    ) -> None:
        self._connection_repository = connection_repository
        self._sync_use_case = sync_use_case
        self._keep_alive = keep_alive

    def execute(self, user_id: UserId) -> UserRefreshSummary:
        connections = self._connection_repository.find_by_user(user_id)
        mailboxes = 0
        fetched = 0
        accepted = 0
        needs_reauth = 0

        for connection in connections:
            if connection.status is MailboxConnectionStatus.NEEDS_REAUTH:
                needs_reauth += 1
                continue

            if not connection.is_active:
                continue

            mailboxes += 1
            # Renew first: if the grant is gone, this is what discovers it,
            # and there is no point reading a mailbox we have lost access to.
            renewal = self._keep_alive.execute(connection)

            if renewal.outcome is RenewalOutcome.NEEDS_REAUTH:
                needs_reauth += 1
                continue

            result = self._sync_use_case.execute(renewal.connection)
            fetched += result.fetched
            accepted += result.accepted

        return UserRefreshSummary(
            mailboxes=mailboxes,
            fetched=fetched,
            accepted=accepted,
            needs_reauth=needs_reauth,
        )


class SyncMailboxRunner(Protocol):
    """Just the part of `SyncMailboxUseCase` this needs.

    Declared here rather than imported, because `sync_handlers` already
    imports this module and the dependency must not point both ways.
    """

    def execute(self, connection: MailboxConnection) -> SyncOutcome: ...


class SyncOutcome(Protocol):
    @property
    def fetched(self) -> int: ...

    @property
    def accepted(self) -> int: ...


class SweepSubscriptionsUseCase:
    """Walks every active mailbox and renews the ones that are due.

    This is the floor, not the whole strategy: it runs on a schedule, and its
    job is to catch the mailboxes too quiet to have renewed themselves.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
        keep_alive: KeepSubscriptionAliveUseCase,
    ) -> None:
        self._connection_repository = connection_repository
        self._keep_alive = keep_alive

    def execute(self) -> SweepResult:
        connections: Sequence[MailboxConnection] = (
            self._connection_repository.list_active()
        )
        counts: dict[RenewalOutcome, int] = {}

        for connection in connections:
            result = self._keep_alive.execute(connection)
            counts[result.outcome] = counts.get(result.outcome, 0) + 1

        return SweepResult(
            renewed=counts.get(RenewalOutcome.RENEWED, 0),
            not_due=counts.get(RenewalOutcome.NOT_DUE, 0),
            needs_reauth=counts.get(RenewalOutcome.NEEDS_REAUTH, 0),
            deferred=counts.get(RenewalOutcome.DEFERRED, 0),
        )
