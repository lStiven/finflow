"""Decoding Google's push notification.

Gmail does not call us directly: it publishes to a Pub/Sub topic, and Pub/Sub
delivers a wrapper with the real payload base64-encoded inside. The payload
names a mailbox and a history id, never a message — which is exactly the
shape the rest of the context expects.
"""

from __future__ import annotations

import base64
import binascii
import json

from pydantic import BaseModel, Field

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxEvent,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.infrastructure.serialization import (
    as_json_object,
    read_string,
)


class PubSubMessage(BaseModel):
    data: str = Field(default="", max_length=64_000)
    message_id: str = Field(default="", alias="messageId", max_length=256)


class PubSubEnvelope(BaseModel):
    """What Pub/Sub posts to a push endpoint."""

    message: PubSubMessage
    subscription: str = Field(default="", max_length=512)


class UndecodableNotificationError(ValueError):
    """The envelope did not contain a readable Gmail notification."""


def to_mailbox_event(envelope: PubSubEnvelope) -> MailboxEvent:
    """Unwrap the envelope into the event the context understands.

    The history id inside is deliberately ignored: our own stored cursor is
    the position we trust. A notification only ever means "look again", and
    taking Google's number instead would skip whatever arrived between two
    notifications we handled out of order.
    """
    try:
        decoded = base64.b64decode(envelope.message.data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise UndecodableNotificationError("data was not valid base64") from error

    try:
        payload = as_json_object(json.loads(decoded))
    except json.JSONDecodeError as error:
        raise UndecodableNotificationError("data was not JSON") from error

    address = read_string(payload, "emailAddress")

    if not address:
        raise UndecodableNotificationError("notification named no mailbox")

    try:
        return MailboxEvent(
            provider=MailboxProvider.GMAIL,
            address=EmailAddress(address),
        )
    except ValueError as error:
        raise UndecodableNotificationError(str(error)) from error
