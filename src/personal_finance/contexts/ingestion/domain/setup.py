"""How far one user got connecting their bank, derived rather than stored.

Nothing here is a counter a client writes. A "current step" that some screen
increments is wrong the moment the same person opens a second browser, and it
knows nothing about the half of this that happens in a mailbox nobody is
watching. So the progress is computed, every time, from facts that had to be
true anyway: the inbox exists, it trusts somebody, Google confirmed a
forwarding request for it, an alert actually arrived.

The steps are independent booleans, not a chain. People do configure Gmail
before approving a sender, and the order they happen in is theirs — the only
thing this decides is which one to point at next.

The two sub-steps a user still has to do by hand — copying the address,
writing the Gmail filter — leave no trace here on purpose: nothing in this
deployment can observe them, and a step nobody can verify does not belong in
a state machine that claims to know. The client shows them and moves on; the
verifiable step behind them turns green on its own.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Self

from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.shared.domain.value_objects import PosixTime, ValueObject


class SetupStep(enum.Enum):
    """The four things that have to be true before money arrives on its own.

    In the order a screen shows them, which is also the order they usually
    happen in.
    """

    # The forwarding address exists. True from registration onwards; listed
    # anyway because it is the step whose *content* the user needs — it is
    # where the address they have to paste is shown.
    ADDRESS_ASSIGNED = "address_assigned"
    # At least one sender is approved. Until then the address accepts nothing,
    # which is the safe default and an invisible one.
    SENDERS_APPROVED = "senders_approved"
    # Google confirmed a forwarding request aimed at this address.
    FORWARDING_CONFIRMED = "forwarding_confirmed"
    # An email got past the sender filter. The only step that proves the whole
    # route works end to end rather than one piece of it.
    FIRST_ALERT = "first_alert"


@dataclasses.dataclass(frozen=True, slots=True)
class SetupStepState(ValueObject):
    """One step, and when it was closed if that is known.

    `at` is null for the two steps that are states rather than events: the
    address exists and somebody is approved, neither of which is worth a
    timestamp of its own.
    """

    step: SetupStep
    done: bool
    at: PosixTime | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class InboxSetup(ValueObject):
    """Everything a "connect your bank" screen needs to answer itself."""

    steps: tuple[SetupStepState, ...]

    @classmethod
    def of(cls, inbox: UserInbox) -> Self:
        policy = inbox.sender_policy
        approves_somebody = bool(policy.allowed_addresses or policy.allowed_domains)

        return cls(
            steps=(
                SetupStepState(step=SetupStep.ADDRESS_ASSIGNED, done=True),
                SetupStepState(
                    step=SetupStep.SENDERS_APPROVED,
                    done=approves_somebody,
                ),
                SetupStepState(
                    step=SetupStep.FORWARDING_CONFIRMED,
                    done=inbox.forwarding_confirmed_at is not None,
                    at=inbox.forwarding_confirmed_at,
                ),
                SetupStepState(
                    step=SetupStep.FIRST_ALERT,
                    done=inbox.first_accepted_at is not None,
                    at=inbox.first_accepted_at,
                ),
            ),
        )

    @property
    def ready(self) -> bool:
        """Whether expenses are arriving on their own right now.

        Deliberately not "every step is done". A confirmed forwarding rule is
        the usual way an alert gets here, not the only one — somebody who
        forwards each alert by hand has a working setup and no confirmation
        to show for it, and telling them they are not connected while their
        movements appear would be a lie the screen cannot recover from.

        What it does require is that somebody is still approved: emptying the
        allow-list stops the next alert cold, so this has to go back to false
        even though a first one already arrived.
        """
        return self._done(SetupStep.FIRST_ALERT) and self._done(
            SetupStep.SENDERS_APPROVED,
        )

    @property
    def current(self) -> SetupStep | None:
        """The step to point the user at, or None once there is nothing left.

        None rather than the last step when ready, so a client never has to
        special-case "done" out of a value that otherwise means "do this".
        """
        if self.ready:
            return None

        return next((state.step for state in self.steps if not state.done), None)

    def _done(self, step: SetupStep) -> bool:
        return any(state.step is step and state.done for state in self.steps)
