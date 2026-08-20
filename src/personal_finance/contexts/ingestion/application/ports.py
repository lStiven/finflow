from __future__ import annotations

from typing import Protocol

from personal_finance.contexts.ingestion.application.messages import (
    ParseNotificationMessage,
)
from personal_finance.contexts.ingestion.domain.entities import BankNotification


class BankNotificationRepository(Protocol):
    """Persistence port for `BankNotification`.

    `add_if_new` must be an atomic conditional write keyed on the
    notification's idempotency key, so at-least-once SQS/webhook redelivery
    never processes the same email twice.
    """

    def add_if_new(self, notification: BankNotification) -> BankNotification | None:
        """Persist `notification` and return None. If its idempotency key is
        already taken, write nothing and return the stored record instead, so
        a retry can resume an intake that failed midway.
        """
        ...

    def save(self, notification: BankNotification) -> None:
        """Overwrite the stored notification with its current state."""
        ...


class QueuePublisher(Protocol):
    """Port for handing a notification to the asynchronous parsing queue.

    Delivery is at-least-once: the same message may be enqueued more than
    once when a retry replays a partially completed intake.
    """

    def enqueue(self, message: ParseNotificationMessage) -> None: ...
