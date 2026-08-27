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
    ForwardingConfirmer,
    InboundEmail,
    IngestMailboxReader,
)
from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    ForwardingConfirmation,
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
    # Gmail forwarding requests this pass finished. Counted apart from
    # `accepted` because nothing about them is a bank notification.
    confirmations: int = 0


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
        forwarding_confirmer: ForwardingConfirmer,
    ) -> None:
        self._reader = reader
        self._receive_use_case = receive_use_case
        self._forwarding_confirmer = forwarding_confirmer

    def execute(self) -> PollResult:
        emails = self._reader.fetch_new()
        accepted = 0
        duplicates = 0
        unknown_recipient = 0
        failed = 0
        confirmations = 0
        handled: list[InboundEmail] = []

        for email in emails:
            # Ahead of the bank-notification path on purpose: this message
            # carries no transaction, comes from a sender nobody approved, and
            # that filter would discard its body — the link with it.
            confirmation = ForwardingConfirmation.from_email(
                sender=email.sender,
                raw_content=email.raw_content,
            )

            if confirmation is not None:
                if self._confirm(confirmation, email):
                    confirmations += 1
                    handled.append(email)
                else:
                    failed += 1

                continue

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
            confirmations=confirmations,
        )

    def _confirm(
        self,
        confirmation: ForwardingConfirmation,
        email: InboundEmail,
    ) -> bool:
        """Follow one confirmation link, and never let it sink the batch."""
        try:
            accepted = self._forwarding_confirmer.confirm(confirmation)
        except Exception:
            # Unacknowledged, so the next poll finds the mail still new: the
            # link stays valid for days and a network blip should cost a
            # retry, not somebody's setup.
            _logger.exception(
                "failed to confirm a forwarding request; left unacknowledged",
                extra={"recipient": email.recipient.value},
            )

            return False

        if accepted:
            _logger.info(
                "forwarding confirmed",
                extra={"recipient": email.recipient.value},
            )
        else:
            # Acknowledged even so: an expired or already-used link answers
            # this way every time, and retrying it forever would be noise.
            _logger.warning(
                "forwarding confirmation was refused",
                extra={"recipient": email.recipient.value},
            )

        return True
