"""Polling the one mailbox this deployment owns.

Every user forwards their bank alerts to the same Gmail account, distinguished
by a `+alias`; this is the entry point that reads whatever landed there since
last time and feeds it into the same intake path the local testing webhook
uses. `ReceiveBankNotificationUseCase` already does the attribution (by
recipient) and the sender-approval check — this use case does not repeat
either, it only drives the read/ack loop around it.
"""

from __future__ import annotations

import dataclasses
import logging

from personal_finance.contexts.ingestion.application.commands import (
    ReceiveBankNotificationCommand,
)
from personal_finance.contexts.ingestion.application.handlers import (
    ReceiveBankNotificationUseCase,
    ReceiveOutcome,
)
from personal_finance.contexts.ingestion.application.ports import (
    InboundEmail,
    IngestMailboxReader,
)


_logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class PollResult:
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0
    # Recipient matched no registered inbox — a stale alias, or mail that
    # reached the ingest address some other way.
    unknown_recipient: int = 0
    # Recording it durably raised — left unacknowledged on purpose, so the
    # next poll finds it still new and tries again.
    failed: int = 0


class PollIngestMailboxUseCase:
    """Reads whatever is new in the ingest mailbox and hands each message to
    `ReceiveBankNotificationUseCase`.

    Every message that call handles without raising is acknowledged, in one
    batch at the end — the call itself is what is durable and idempotent, so
    re-fetching an already-handled message on a crash is safe but wasteful,
    and `ack` is what avoids the waste. A message that call *fails* on is
    deliberately left off that batch: it stays looking new, so a transient
    failure on one message costs a retry next poll rather than the rest of
    the batch behind it.
    """

    def __init__(
        self,
        *,
        reader: IngestMailboxReader,
        receive_use_case: ReceiveBankNotificationUseCase,
    ) -> None:
        self._reader = reader
        self._receive_use_case = receive_use_case

    def execute(self) -> PollResult:
        emails = self._reader.fetch_new()
        accepted = 0
        duplicates = 0
        unknown_recipient = 0
        failed = 0
        handled: list[InboundEmail] = []

        for email in emails:
            try:
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
            except Exception:
                # One bad message (a transient DynamoDB/SQS error, anything
                # unexpected) must not abandon the rest of the batch — nor
                # get acknowledged itself, since it was never durably
                # recorded.
                _logger.exception(
                    "failed to record an ingest message; left unacknowledged",
                    extra={"message_id": email.message_id.value},
                )
                failed += 1

                continue

            if result.outcome is ReceiveOutcome.ACCEPTED:
                accepted += 1
            elif result.outcome is ReceiveOutcome.DUPLICATE:
                duplicates += 1
            else:
                unknown_recipient += 1

            handled.append(email)

        self._reader.ack(handled)

        return PollResult(
            fetched=len(emails),
            accepted=accepted,
            duplicates=duplicates,
            unknown_recipient=unknown_recipient,
            failed=failed,
        )
