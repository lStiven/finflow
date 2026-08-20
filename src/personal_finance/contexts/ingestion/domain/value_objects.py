from __future__ import annotations

import dataclasses
import enum
import hashlib
from typing import Self
import uuid

from personal_finance.shared.domain.value_objects import ValueObject


_NOTIFICATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "ingestion.finflow")


@dataclasses.dataclass(frozen=True, slots=True)
class NotificationId(ValueObject):
    value: uuid.UUID

    @classmethod
    def for_message(cls, message_id: EmailMessageId) -> Self:
        """Derive the identity deterministically from the email message id, so
        a redelivery of the same email always resolves to the same aggregate.
        """
        return cls(value=uuid.uuid5(_NOTIFICATION_NAMESPACE, message_id.value))


@dataclasses.dataclass(frozen=True, slots=True)
class EmailMessageId(ValueObject):
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Email message ID cannot be empty")

        object.__setattr__(self, "value", value)


@dataclasses.dataclass(frozen=True, slots=True)
class EmailAddress(ValueObject):
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip().lower()

        if not value or "@" not in value:
            raise ValueError("Invalid email address")

        object.__setattr__(self, "value", value)

    @property
    def domain(self) -> str:
        return self.value.rsplit("@", 1)[1]


@dataclasses.dataclass(frozen=True, slots=True)
class IdempotencyKey(ValueObject):
    value: str

    @classmethod
    def from_message_id(cls, message_id: EmailMessageId) -> Self:
        digest = hashlib.sha256(message_id.value.encode("utf-8")).hexdigest()

        return cls(value=digest)


class ProcessingStatus(enum.Enum):
    """Explicit string values: this enum is persisted and serialized, so the
    stored representation must survive a reordering of the members.
    """

    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class NotificationIgnoredReason(enum.Enum):
    UNAUTHORIZED_SENDER = "unauthorized_sender"
