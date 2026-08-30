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
import enum
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
    UserInboxRepository,
)
from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    ForwardingConfirmation,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)


class ConfirmationOutcome(enum.Enum):
    """What became of one forwarding confirmation link."""

    CONFIRMED = "confirmed"
    # Google answered, and said no. Nothing to retry.
    REFUSED = "refused"
    # Never got an answer. Worth another poll.
    FAILED = "failed"
    # Addressed to an alias no user owns. Nothing was fetched.
    UNCLAIMED = "unclaimed"


class ConfirmForwardingUseCase:
    """Finishes a Gmail forwarding request aimed at one user's alias.

    The order matters and is the point of this being a use case rather than a
    call to the confirmer: the alias is resolved to a registered inbox
    **before** the link is followed. An alias nobody owns is not ours to set
    up — confirming it would route a stranger's mail into the one mailbox
    this deployment reads, on the say-so of an email. It is the same rule the
    intake path already applies to bank alerts, which records nothing for a
    recipient it cannot attribute.

    Confirming also leaves a mark on the inbox, which is what lets the user's
    own screen say the step is done. Without it the fact lived only in a log
    line nobody but the operator can read.
    """

    def __init__(
        self,
        *,
        inbox_repository: UserInboxRepository,
        confirmer: ForwardingConfirmer,
    ) -> None:
        self._inbox_repository = inbox_repository
        self._confirmer = confirmer

    def execute(
        self,
        *,
        recipient: EmailAddress,
        confirmation: ForwardingConfirmation,
        confirmed_at: PosixTime,
    ) -> ConfirmationOutcome:
        """Follow one link, and never let it sink the batch that called it.

        Four answers, not two. A link Google turned down is finished and must
        be acknowledged; one that could not be reached is not finished and
        must not be; one for an unknown alias was never ours to follow.
        Reporting any of those as success is what would make the counter an
        operator reads say a setup completed when it did not.
        """
        inbox = self._inbox_repository.find_by_address(recipient)

        if inbox is None:
            _logger.warning(
                "ignoring a forwarding request for an unregistered alias",
                extra={"recipient": recipient.value},
            )

            return ConfirmationOutcome.UNCLAIMED

        try:
            accepted = self._confirmer.confirm(confirmation)
        except Exception:
            # The caller leaves the mail unacknowledged, so the next poll
            # finds it still new: the link stays valid for days and a network
            # blip should cost a retry, not somebody's setup.
            _logger.exception(
                "failed to confirm a forwarding request",
                extra={"recipient": recipient.value},
            )

            return ConfirmationOutcome.FAILED

        if not accepted:
            # Acknowledged even so: an expired or already-used link answers
            # this way every time, and retrying it forever would be noise.
            _logger.warning(
                "forwarding confirmation was refused",
                extra={"recipient": recipient.value},
            )

            return ConfirmationOutcome.REFUSED

        # After the fetch, not before: the step this records is "Google
        # accepted", and a mark written on the way there would be a checkmark
        # for something that had not happened yet.
        #
        # Guarded, and still CONFIRMED if it fails. The link has been spent by
        # now — Google refuses it a second time and never sends the mail
        # again — so retrying the message could only lose the confirmation as
        # well as the mark. What is lost is a checkmark, not the forwarding
        # rule itself: it is set up, and the first alert that arrives closes
        # the step that actually decides `ready`.
        try:
            self._inbox_repository.mark_forwarding_confirmed(
                address=inbox.address,
                confirmed_at=confirmed_at,
            )
        except Exception:
            _logger.exception(
                "forwarding was confirmed but the inbox could not be marked",
                extra={"recipient": recipient.value},
            )

        _logger.info(
            "forwarding confirmed",
            extra={"recipient": recipient.value},
        )

        return ConfirmationOutcome.CONFIRMED


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
    # Gmail forwarding requests Google accepted this pass. Counted apart from
    # `accepted` because nothing about them is a bank notification, and apart
    # from `refused_confirmations` because only this one means a user's
    # forwarding is actually set up.
    confirmations: int = 0
    # Links Google turned down — expired, already used. Acknowledged rather
    # than retried, but counted on their own: reporting them as confirmations
    # would tell an operator that somebody's setup finished when it did not.
    refused_confirmations: int = 0
    # Forwarding requests aimed at an alias no user owns: never followed,
    # acknowledged so they do not come round again. A steady trickle here is
    # somebody trying to route their mail through this deployment.
    unclaimed_confirmations: int = 0


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
        confirm_use_case: ConfirmForwardingUseCase,
    ) -> None:
        self._reader = reader
        self._receive_use_case = receive_use_case
        self._confirm_use_case = confirm_use_case

    def execute(self) -> PollResult:
        emails = self._reader.fetch_new()
        accepted = 0
        duplicates = 0
        unknown_recipient = 0
        failed = 0
        confirmations = 0
        refused_confirmations = 0
        unclaimed_confirmations = 0
        handled: list[InboundEmail] = []

        for email in emails:
            try:
                # Ahead of the bank-notification path on purpose: this message
                # carries no transaction, comes from a sender nobody approved,
                # and that filter would discard its body — the link with it.
                # Inside the guard because it reads untrusted mail: raising
                # here would abandon the whole batch, and the messages already
                # handled would never be acknowledged.
                confirmation = ForwardingConfirmation.from_email(
                    sender=email.sender,
                    raw_content=email.raw_content,
                )
            except Exception:
                _logger.exception(
                    "failed to read an ingest message; left unacknowledged",
                    extra={"message_id": email.message_id.value},
                )
                failed += 1

                continue

            if confirmation is not None:
                try:
                    outcome = self._confirm_use_case.execute(
                        recipient=email.recipient,
                        confirmation=confirmation,
                        # When the request landed, not when this poll got
                        # round to it: the poll interval is an implementation
                        # detail and has no business shifting the time a user
                        # is shown.
                        confirmed_at=email.received_at,
                    )
                except Exception:
                    # Guarded like the bank path beside it. That use case
                    # swallows a failure of its own network call, but it also
                    # reads storage, and one transient DynamoDB error must not
                    # abandon a batch whose other messages are already
                    # recorded and still waiting to be acknowledged.
                    _logger.exception(
                        "failed to handle a forwarding request; left unacknowledged",
                        extra={"message_id": email.message_id.value},
                    )
                    failed += 1

                    continue

                if outcome is ConfirmationOutcome.CONFIRMED:
                    confirmations += 1
                    handled.append(email)
                elif outcome is ConfirmationOutcome.REFUSED:
                    refused_confirmations += 1
                    handled.append(email)
                elif outcome is ConfirmationOutcome.UNCLAIMED:
                    unclaimed_confirmations += 1
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
            refused_confirmations=refused_confirmations,
            unclaimed_confirmations=unclaimed_confirmations,
        )
