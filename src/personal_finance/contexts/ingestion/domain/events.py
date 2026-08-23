from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
    NotificationDeferredReason,
    NotificationId,
    NotificationIgnoredReason,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationReceived(Event):
    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId
    sender: EmailAddress
    received_at: PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationQueued(Event):
    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationIgnored(Event):
    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId
    sender: EmailAddress
    reason: NotificationIgnoredReason


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionExtracted(Event):
    """A bank alert was understood. This is what the other contexts wait for."""

    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId
    transaction: ExtractedTransaction


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class TransactionExtractionDeferred(Event):
    """Neither a template nor the fallback produced a transaction."""

    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId
    sender: EmailAddress
    reason: NotificationDeferredReason


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class BankNotificationFailed(Event):
    notification_id: NotificationId
    user_id: UserId
    message_id: EmailMessageId
    reason: str
