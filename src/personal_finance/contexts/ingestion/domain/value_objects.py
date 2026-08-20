from __future__ import annotations

import dataclasses
import enum
import hashlib
import uuid


@dataclasses.dataclass(frozen=True, slots=True)
class NotificationId:
    value: uuid.UUID

    @classmethod
    def new(cls) -> NotificationId:
        return cls(value=uuid.uuid4())


@dataclasses.dataclass(frozen=True, slots=True)
class EmailMessageId:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()

        if not value:
            raise ValueError("Email message ID cannot be empty")

        object.__setattr__(self, "value", value)


@dataclasses.dataclass(frozen=True, slots=True)
class EmailAddress:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip().lower()

        if not value or "@" not in value:
            raise ValueError("Invalid email address")

        object.__setattr__(self, "value", value)


@dataclasses.dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    @classmethod
    def from_message_id(cls, message_id: EmailMessageId) -> IdempotencyKey:
        digest = hashlib.sha256(message_id.value.encode("utf-8")).hexdigest()

        return cls(value=digest)


class ProcessingStatus(enum.Enum):
    RECEIVED = enum.auto()
    QUEUED = enum.auto()
    PROCESSING = enum.auto()
    PROCESSED = enum.auto()
    FAILED = enum.auto()
    IGNORED = enum.auto()
