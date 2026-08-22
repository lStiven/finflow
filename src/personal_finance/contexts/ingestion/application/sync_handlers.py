from __future__ import annotations

from collections.abc import Mapping
import dataclasses

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
    ReceiveOutcome,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxEvent,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.ports import (
    MailboxConnectionRepository,
    MailboxReader,
    UserInboxRepository,
)
from personal_finance.contexts.ingestion.application.subscription_handlers import (
    KeepSubscriptionAliveUseCase,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.shared.domain.value_objects import PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class SyncMailboxResult:
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0


class UnsupportedMailboxProviderError(Exception):
    """Raised when no reader is registered for a connection's provider."""


class SyncMailboxUseCase:
    """Pulls new bank email from one user's mailbox and ingests it.

    Nothing here knows which provider is on the other side; the reader does.
    The notification path is the same one the webhook uses, so an email that
    arrives by pull and one that arrives by push are indistinguishable
    downstream.
    """

    def __init__(
        self,
        *,
        readers: Mapping[MailboxProvider, MailboxReader],
        inbox_repository: UserInboxRepository,
        connection_repository: MailboxConnectionRepository,
        receive_use_case: ReceiveBankNotificationUseCase,
    ) -> None:
        self._readers = readers
        self._inbox_repository = inbox_repository
        self._connection_repository = connection_repository
        self._receive_use_case = receive_use_case

    def execute(self, connection: MailboxConnection) -> SyncMailboxResult:
        reader = self._readers.get(connection.provider)

        if reader is None:
            raise UnsupportedMailboxProviderError(
                f"No reader registered for {connection.provider.value}",
            )

        inbox = self._inbox_repository.find_by_address(connection.address)
        senders = approved_senders(inbox) if inbox else ()

        if not senders:
            # Never fetch without a sender filter. The mailbox is someone's
            # private correspondence, and an unfiltered query would pull all of
            # it — not just the bank alerts we were authorized to read.
            return SyncMailboxResult()

        batch = reader.fetch_new(connection=connection, senders=senders)
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

        # Only now, with every message ingested, does the cursor move. A crash
        # mid-batch re-fetches on the next pass, which is safe precisely
        # because the conditional write makes ingestion idempotent.
        #
        # `last_synced_at` moves with it: a subscription that dies without
        # erroring leaves no trace except a mailbox that stopped being read,
        # and that is only visible if we write down when we last did.
        self._connection_repository.save(
            dataclasses.replace(
                connection,
                cursor=batch.cursor,
                last_synced_at=PosixTime.now(),
            ),
        )

        return SyncMailboxResult(
            fetched=len(batch.emails),
            accepted=accepted,
            duplicates=duplicates,
        )


def approved_senders(inbox: UserInbox) -> tuple[str, ...]:
    """Flattens a user's sender policy into what a `MailboxReader` expects:
    addresses and domains in one list, matched the same way regardless of
    which kind an entry is. Shared with `backfill_handlers`, which restricts
    a mailbox to exactly the same senders for its one-time catch-up.
    """
    policy = inbox.sender_policy

    return (
        *sorted(address.value for address in policy.allowed_addresses),
        *sorted(policy.allowed_domains),
    )


class HandleMailboxEventUseCase:
    """Entry point for a provider's push notification.

    The application does not poll: it waits for the provider to say that a
    mailbox changed, then reads only what that user authorized. An event for a
    mailbox we hold no connection for is dropped without a fetch — it is not
    ours to read.
    """

    def __init__(
        self,
        *,
        connection_repository: MailboxConnectionRepository,
        sync_use_case: SyncMailboxUseCase,
        keep_alive: KeepSubscriptionAliveUseCase | None = None,
    ) -> None:
        self._connection_repository = connection_repository
        self._sync_use_case = sync_use_case
        self._keep_alive = keep_alive

    def execute(self, event: MailboxEvent) -> SyncMailboxResult:
        connection = self._connection_repository.find(
            provider=event.provider,
            address=event.address,
        )

        # No connection: not ours to read. Revoked or awaiting
        # reauthorization: it stopped being ours to read, and a provider that
        # keeps notifying us does not change that.
        if connection is None or not connection.is_active:
            return SyncMailboxResult()

        result = self._sync_use_case.execute(connection)

        if self._keep_alive is not None:
            # Free renewal: the provider just proved it is still talking to
            # us, so a busy mailbox keeps its own subscription alive and the
            # scheduled sweep only ever has to cover the quiet ones.
            self._keep_alive.execute(
                self._connection_repository.find(
                    provider=event.provider,
                    address=event.address,
                )
                or connection,
            )

        return result
