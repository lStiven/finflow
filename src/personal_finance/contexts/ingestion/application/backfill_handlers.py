"""Catching up on mail that arrived before a mailbox was ever connected.

The ordinary sync path only ever reads forward from the moment a mailbox
subscribed — that is the privacy invariant, not an accident: nothing here
scans a stranger's history by default. This is the one deliberate, opt-in
exception. A user who signs up on the 15th would otherwise have a first month
of history missing its first two weeks, because `SyncMailboxUseCase` only ever
sees what arrives after it connects. Backfilling is how they ask, once, to
also read whatever this calendar month already holds.

It goes through the exact same dedup-then-enqueue path as everything else —
`ReceiveBankNotificationUseCase` — so a message the ordinary sync already
picked up simply comes back as a duplicate, never twice, and calling this more
than once is always safe.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import logging

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
    ReceiveOutcome,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxConnectionStatus,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.ports import (
    MailboxConnectionRepository,
    MailboxReader,
    UserInboxRepository,
)
from personal_finance.contexts.ingestion.application.sync_handlers import (
    UnsupportedMailboxProviderError,
    approved_senders,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


_logger = logging.getLogger(__name__)


def start_of_current_month(*, now: PosixTime) -> PosixTime:
    """Midnight UTC on the first of `now`'s month.

    UTC because no per-user timezone is stored anywhere in the system; a
    calendar-month boundary computed against it is close enough for "give me
    this month's history" and never depends on where the request came from.
    """
    return PosixTime.from_datetime(
        now.to_datetime().replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ),
    )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BackfillResult:
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0


class BackfillMailboxUseCase:
    """Reads one connected mailbox over an explicit date range, independent of
    its incremental cursor.

    Never reads and never writes `connection.cursor`: a backfill must not be
    able to make the ongoing sync skip mail, and the ongoing sync must not be
    able to make a later backfill think it already covered a range it did not.
    """

    def __init__(
        self,
        *,
        readers: Mapping[MailboxProvider, MailboxReader],
        inbox_repository: UserInboxRepository,
        receive_use_case: ReceiveBankNotificationUseCase,
    ) -> None:
        self._readers = readers
        self._inbox_repository = inbox_repository
        self._receive_use_case = receive_use_case

    def execute(
        self,
        connection: MailboxConnection,
        *,
        since: PosixTime,
        until: PosixTime,
    ) -> BackfillResult:
        reader = self._readers.get(connection.provider)

        if reader is None:
            raise UnsupportedMailboxProviderError(
                f"No reader registered for {connection.provider.value}",
            )

        inbox = self._inbox_repository.find_by_address(connection.address)
        senders = approved_senders(inbox) if inbox else ()

        if not senders:
            # Same rule as the ongoing sync: never fetch without a filter.
            return BackfillResult()

        batch = reader.fetch_range(
            connection=connection,
            senders=senders,
            since=since,
            until=until,
        )
        accepted = 0
        duplicates = 0

        for email in batch.emails:
            result = self._receive_use_case.execute(
                ReceiveBankNotificationCommand(
                    recipient=email.recipient,
                    message_id=email.message_id,
                    sender=email.sender,
                    subject=email.subject,
                    raw_content=email.raw_content,
                    received_at=email.received_at,
                ),
            )

            if result.outcome is ReceiveOutcome.ACCEPTED:
                accepted += 1
            elif result.outcome is ReceiveOutcome.DUPLICATE:
                duplicates += 1

        return BackfillResult(
            fetched=len(batch.emails),
            accepted=accepted,
            duplicates=duplicates,
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class UserBackfillSummary:
    mailboxes: int = 0
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0
    needs_reauth: int = 0
    since: PosixTime = dataclasses.field(default_factory=PosixTime.now)


class BackfillUserMailboxesUseCase:
    """Backfills the current month across every active mailbox a user holds.

    Meant to be offered once, right after someone connects a mailbox — signing
    up on any day but the first should not mean losing that month's earlier
    history. Safe to call again regardless: everything already ingested comes
    back as a duplicate.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
        backfill_use_case: BackfillMailboxUseCase,
    ) -> None:
        self._connection_repository = connection_repository
        self._backfill_use_case = backfill_use_case

    def execute(self, user_id: UserId) -> UserBackfillSummary:
        connections = self._connection_repository.find_by_user(user_id)
        now = PosixTime.now()
        since = start_of_current_month(now=now)
        mailboxes = 0
        fetched = 0
        accepted = 0
        duplicates = 0
        needs_reauth = 0

        for connection in connections:
            if connection.status is MailboxConnectionStatus.NEEDS_REAUTH:
                needs_reauth += 1
                continue

            if not connection.is_active:
                continue

            mailboxes += 1
            result = self._backfill_use_case.execute(
                connection,
                since=since,
                until=now,
            )
            fetched += result.fetched
            accepted += result.accepted
            duplicates += result.duplicates

        _logger.info(
            "mailbox backfill completed",
            extra={
                "user_id": str(user_id.value),
                "mailboxes": mailboxes,
                "accepted": accepted,
            },
        )

        return UserBackfillSummary(
            mailboxes=mailboxes,
            fetched=fetched,
            accepted=accepted,
            duplicates=duplicates,
            needs_reauth=needs_reauth,
            since=since,
        )
