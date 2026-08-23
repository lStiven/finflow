from __future__ import annotations

import dataclasses
import enum
import hashlib
from typing import Self
import uuid

from personal_finance.shared.domain.value_objects import UserId, ValueObject


_NOTIFICATION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "ingestion.finflow")


def _scope(user_id: UserId, message_id: EmailMessageId) -> str:
    return f"{user_id.value}:{message_id.value}"


@dataclasses.dataclass(frozen=True, slots=True)
class NotificationId(ValueObject):
    value: uuid.UUID

    @classmethod
    def for_message(cls, *, user_id: UserId, message_id: EmailMessageId) -> Self:
        """Derive the identity deterministically from the owner and the email
        message id, so a redelivery of the same email always resolves to the
        same aggregate.

        The user is part of the derivation because a forwarded bank email keeps
        its original `Message-ID`: two people on a shared account would
        otherwise collapse into one notification.
        """
        return cls(
            value=uuid.uuid5(_NOTIFICATION_NAMESPACE, _scope(user_id, message_id)),
        )


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
    def from_message(cls, *, user_id: UserId, message_id: EmailMessageId) -> Self:
        """Scoped to the owner for the same reason `NotificationId` is: the
        message id alone is not unique across users.
        """
        digest = hashlib.sha256(_scope(user_id, message_id).encode("utf-8")).hexdigest()

        return cls(value=digest)


class ProcessingStatus(enum.Enum):
    """Explicit string values: this enum is persisted and serialized, so the
    stored representation must survive a reordering of the members.
    """

    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    PROCESSED = "processed"
    # No deterministic template matched, and no transaction came out of the
    # LLM fallback either — whether because it ran and found nothing, or
    # because there is none configured. Which one is `deferred_reason`, on
    # the notification itself: the name says "not extracted", not "still to
    # be tried".
    PENDING_FALLBACK = "pending_fallback"
    FAILED = "failed"
    IGNORED = "ignored"


class NotificationIgnoredReason(enum.Enum):
    UNAUTHORIZED_SENDER = "unauthorized_sender"


class NotificationDeferredReason(enum.Enum):
    """Why a notification sits in `PENDING_FALLBACK`. Both members are
    final for this attempt — neither means "still in progress".
    """

    # No LLM configured for this deployment; the fallback never ran.
    NO_FALLBACK_CONFIGURED = "no_fallback_configured"
    # The fallback ran — declined the email, or produced something that
    # failed validation — and still came back with nothing.
    FALLBACK_FOUND_NOTHING = "fallback_found_nothing"
