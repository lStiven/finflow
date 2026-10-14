from __future__ import annotations

from collections.abc import Sequence
import dataclasses

from personal_finance.contexts.ingestion.application.ports import (
    NotificationHistoryReader,
    NotificationSummary,
    UserInboxRepository,
)
from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.forwarding import (
    forwarding_address,
    gmail_filter_terms,
)
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.setup import InboxSetup
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    ProcessingStatus,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisterUserInboxCommand:
    user_id: UserId
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[EmailAddress] = dataclasses.field(
        default_factory=lambda: frozenset[EmailAddress](),
    )


class RegisterUserInboxUseCase:
    """Assigns a user their forwarding address and the senders they trust.

    The address is never chosen by a caller: it is derived deterministically
    from `user_id` against this deployment's one ingest mailbox, so there is
    nothing to allocate and nothing that can collide. Calling this again for
    the same user replaces the sender list rather than adding to it — there is
    exactly one inbox per user, not a growing collection of them.

    What it does not touch is how far that user got connecting their bank:
    the milestones on the record are written by the ingest worker, and
    approving a new sender is not a reason to forget that a forwarding rule
    was confirmed last week.
    """

    def __init__(
        self,
        *,
        inbox_repository: UserInboxRepository,
        base_address: EmailAddress,
    ) -> None:
        self._inbox_repository = inbox_repository
        self._base_address = base_address

    def execute(self, command: RegisterUserInboxCommand) -> UserInbox:
        inbox = UserInbox(
            user_id=command.user_id,
            address=forwarding_address(
                base=self._base_address,
                user_id=command.user_id,
            ),
            sender_policy=AuthorizedSenderPolicy(
                allowed_addresses=command.allowed_addresses,
                allowed_domains=command.allowed_domains,
            ),
        )
        self._inbox_repository.save(inbox)

        return inbox


class ListUserInboxesUseCase:
    """Reports the inbox a user owns and the senders it trusts.

    Published for other contexts to call: identity exposes it to an
    authenticated user through its own adapter, so nobody has to reach into
    this context's repository to answer "what's my forwarding address?".
    """

    def __init__(self, *, inbox_repository: UserInboxRepository) -> None:
        self._inbox_repository = inbox_repository

    def execute(self, user_id: UserId) -> Sequence[UserInbox]:
        return self._inbox_repository.find_by_user(user_id)


# Enough to name the bank that is being turned away without turning the
# screen into a log. Anybody who needs the whole list has the notifications
# endpoint.
MAX_UNAPPROVED_SENDERS = 5


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboxSetupView:
    """How far one user got, and what is standing in the way."""

    address: EmailAddress
    setup: InboxSetup
    # What the user's Gmail filter should match: the approved senders, in
    # Gmail's own terms. Derived here so no client rebuilds the list it is
    # meant to mirror.
    filter_terms: tuple[str, ...] = ()
    # Senders whose mail arrived and was thrown away for not being approved.
    # The single most likely reason a setup looks finished and produces
    # nothing, and the one failure a user cannot diagnose from the outside:
    # "nothing is arriving" and "everything is arriving and being discarded"
    # look identical from a screen that only shows movements.
    unapproved_senders: tuple[EmailAddress, ...] = ()


class GetInboxSetupUseCase:
    """Answers "how far along is this user, and what is next?".

    Derived on every call, never stored. A step counter written by a client
    is wrong the moment the same person opens another browser, and it cannot
    see the half of this that happens in a mailbox — so the progress is
    recomputed from the inbox record, which costs one read because the two
    milestones live on it.

    Published for other contexts to call, like the two use cases above it.
    """

    def __init__(
        self,
        *,
        inbox_repository: UserInboxRepository,
        notification_reader: NotificationHistoryReader,
        base_address: EmailAddress,
    ) -> None:
        self._inbox_repository = inbox_repository
        self._notification_reader = notification_reader
        self._base_address = base_address

    def execute(self, user_id: UserId) -> InboxSetupView | None:
        """None when the user has no inbox at all — which registration always
        creates, so it means the record is gone rather than pending.
        """
        inbox = self._find(user_id)

        if inbox is None:
            return None

        setup = InboxSetup.of(inbox)

        if setup.ready:
            # Nothing left to diagnose, and the walk below grows with
            # everything the account ever received. A rejection that happens
            # after this point belongs to the notification list.
            return InboxSetupView(
                address=inbox.address,
                setup=setup,
                filter_terms=gmail_filter_terms(inbox.sender_policy),
            )

        return self._diagnose(inbox, setup)

    def _find(self, user_id: UserId) -> UserInbox | None:
        """The address first, the index second.

        The alias is a pure function of the user id, so it can be looked up
        on the table itself — which matters because the first thing a new
        account does is open this screen, and the `by_user` index is
        eventually consistent: asked through it a moment after registration,
        it can answer that the user has no inbox at all. The index stays as
        the fallback for a record that predates the current ingest mailbox,
        whose address would no longer derive to the same thing.
        """
        derived = self._inbox_repository.find_by_address(
            forwarding_address(base=self._base_address, user_id=user_id),
        )

        if derived is not None and derived.user_id == user_id:
            return derived

        inboxes = self._inbox_repository.find_by_user(user_id)

        return inboxes[0] if inboxes else None

    def _diagnose(self, inbox: UserInbox, setup: InboxSetup) -> InboxSetupView:
        """One walk of what arrived, used for both things it can answer."""
        notifications = tuple(self._notification_reader.list_by_user(inbox.user_id))
        accepted_at = _first_accepted_at(notifications)

        if inbox.first_accepted_at is None and accepted_at is not None:
            # A backfill, and the reason this read is allowed to write. Every
            # account that predates the milestone would otherwise report
            # "not receiving anything yet" forever *and* pay for this walk on
            # every poll — the two things the flag exists to avoid. Written
            # once: the next call reads it off the item and never gets here.
            self._inbox_repository.mark_first_accepted(
                address=inbox.address,
                accepted_at=accepted_at,
            )
            inbox = dataclasses.replace(inbox, first_accepted_at=accepted_at)
            setup = InboxSetup.of(inbox)

        return InboxSetupView(
            address=inbox.address,
            setup=setup,
            filter_terms=gmail_filter_terms(inbox.sender_policy),
            unapproved_senders=(
                ()
                if setup.ready
                else _unapproved_senders(inbox=inbox, notifications=notifications)
            ),
        )


def _first_accepted_at(
    notifications: Sequence[NotificationSummary],
) -> PosixTime | None:
    """When this account first let something through, or None.

    The earliest, not the latest: the question is when expenses started
    arriving, and the repository keeps whichever timestamp gets there first
    anyway.
    """
    accepted = [
        notification.received_at
        for notification in notifications
        if notification.status is not ProcessingStatus.IGNORED
    ]

    return min(accepted, key=PosixTime.as_epoch_seconds) if accepted else None


def _unapproved_senders(
    *,
    inbox: UserInbox,
    notifications: Sequence[NotificationSummary],
) -> tuple[EmailAddress, ...]:
    """Distinct senders this inbox is currently turning away, newest first.

    Re-checked against the policy rather than trusted from the record: a
    sender approved a minute ago still has its old rejections stored, and
    showing them would send the user round a loop they already finished.
    `IGNORED` is enough to identify them — an unapproved sender is the only
    reason anything is ignored.

    Ordered by when they last wrote, because the list is cut short: sorted by
    name instead, six banks trying to reach one account could push the one
    the user is actually waiting for off the end of the list that exists to
    name it.
    """
    latest: dict[EmailAddress, PosixTime] = {}

    for notification in notifications:
        if notification.status is not ProcessingStatus.IGNORED:
            continue

        if inbox.sender_policy.is_authorized(notification.sender):
            continue

        seen = latest.get(notification.sender)

        # Compared as seconds: `PosixTime` is an unordered value object, and
        # a `>` between two of them is a `TypeError`, not a date comparison.
        if seen is None or (
            notification.received_at.as_epoch_seconds() > seen.as_epoch_seconds()
        ):
            latest[notification.sender] = notification.received_at

    ordered = sorted(
        latest.items(),
        key=lambda entry: (-entry[1].as_epoch_seconds(), entry[0].value),
    )

    return tuple(sender for sender, _ in ordered[:MAX_UNAPPROVED_SENDERS])
