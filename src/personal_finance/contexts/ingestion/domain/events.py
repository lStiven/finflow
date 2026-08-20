from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationId,
    NotificationIgnoredReason,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationReceived(Event):
    notification_id: NotificationId
    message_id: EmailMessageId
    sender: EmailAddress
    received_at: PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationQueued(Event):
    notification_id: NotificationId
    message_id: EmailMessageId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationIgnored(Event):
    notification_id: NotificationId
    message_id: EmailMessageId
    sender: EmailAddress
    reason: NotificationIgnoredReason
