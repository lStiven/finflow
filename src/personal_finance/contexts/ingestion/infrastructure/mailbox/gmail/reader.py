"""Reading a Gmail mailbox, filtered to the senders the user approved."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from email import message_from_bytes, policy
from email.message import EmailMessage
import logging
from typing import Protocol

from personal_finance.contexts.ingestion.application.mailbox import (
    InboundEmail,
    MailboxBatch,
    MailboxConnection,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.api import (
    GmailApiClient,
    GmailHistoryExpiredError,
)
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)

# A page of history can name a lot of messages. This caps how many are
# fetched in one pass; the cursor only advances over what was handled, so the
# rest arrives on the next notification.
MAX_MESSAGES_PER_SYNC = 100


class GmailAccessTokenProviderProtocol(Protocol):
    """Anything that can produce a usable access token for a mailbox."""

    def access_token(self, address: EmailAddress) -> str: ...


def _matches(sender: str, senders: Sequence[str]) -> bool:
    domain = sender.rsplit("@", 1)[-1].lower()

    return sender.lower() in senders or domain in senders


def _decode(raw: str) -> bytes | None:
    """Gmail returns the message base64url-encoded, without padding."""
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return None


def _body_text(message: EmailMessage) -> str:
    """The readable text of a message.

    Prefers `text/plain`. Bank alerts carry the same content in both parts,
    and the plain one is what the deterministic parsers were written against.
    """
    if message.is_multipart():
        plain = message.get_body(preferencelist=("plain",))

        if plain is not None:
            content = plain.get_content()

            return content if isinstance(content, str) else str(content)

        html = message.get_body(preferencelist=("html",))

        if html is not None:
            content = html.get_content()

            return content if isinstance(content, str) else str(content)

        return ""

    content = message.get_content()

    return content if isinstance(content, str) else str(content)


class GmailMailboxReader:
    """`MailboxReader` for Gmail, driven by the history API.

    Strictly read-only: it fetches and never marks, labels, or moves
    anything, so a mailbox looks untouched afterwards — unread messages stay
    unread.
    """

    provider = MailboxProvider.GMAIL

    def __init__(
        self,
        *,
        api: GmailApiClient,
        token_provider: GmailAccessTokenProviderProtocol,
    ) -> None:
        self._api = api
        self._token_provider = token_provider

    def fetch_new(
        self,
        *,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> MailboxBatch:
        if not senders:
            # Belt and braces: the caller already refuses to sync without a
            # filter, and this must never be the place that forgets.
            return MailboxBatch()

        if connection.cursor is None:
            # No position yet. `watch` hands one back, so there is nothing to
            # catch up on until the first notification arrives.
            return MailboxBatch()

        access_token = self._token_provider.access_token(connection.address)
        normalized = tuple(sender.lower() for sender in senders)

        try:
            message_ids, latest = self._collect_message_ids(
                access_token=access_token,
                start_history_id=connection.cursor,
            )
        except GmailHistoryExpiredError:
            # The grant is fine; our bookmark is simply older than Gmail's
            # history window. Keeping the old cursor would fail forever, so
            # the caller is told to start fresh from the next notification.
            _logger.warning(
                "gmail history expired, resuming from the current position",
                extra={"user_id": str(connection.user_id.value)},
            )

            return MailboxBatch(emails=(), cursor=None)

        emails = tuple(
            email
            for message_id in message_ids[:MAX_MESSAGES_PER_SYNC]
            if (
                email := self._fetch_if_approved(
                    access_token=access_token,
                    message_id=message_id,
                    connection=connection,
                    senders=normalized,
                )
            )
            is not None
        )

        return MailboxBatch(emails=emails, cursor=latest or connection.cursor)

    def _collect_message_ids(
        self,
        *,
        access_token: str,
        start_history_id: str,
    ) -> tuple[tuple[str, ...], str | None]:
        message_ids: list[str] = []
        latest: str | None = None
        page_token: str | None = None

        while True:
            page = self._api.list_history(
                access_token=access_token,
                start_history_id=start_history_id,
                page_token=page_token,
            )
            message_ids.extend(page.message_ids)
            latest = page.history_id or latest
            page_token = page.next_page_token

            if page_token is None or len(message_ids) >= MAX_MESSAGES_PER_SYNC:
                return tuple(message_ids), latest

    def _fetch_if_approved(
        self,
        *,
        access_token: str,
        message_id: str,
        connection: MailboxConnection,
        senders: Sequence[str],
    ) -> InboundEmail | None:
        message = self._api.get_raw_message(
            access_token=access_token,
            message_id=message_id,
        )
        decoded = _decode(message.raw)

        if decoded is None:
            _logger.warning("undecodable gmail message", extra={"id": message_id})

            return None

        # `policy.default` is what makes this an EmailMessage rather than the
        # older Message, and `get_body` only exists on the former.
        parsed = message_from_bytes(decoded, policy=policy.default)
        sender = _sender_address(parsed)

        if sender is None or not _matches(sender, senders):
            return None

        rfc_message_id = parsed.get("Message-ID") or f"gmail:{message_id}"

        return InboundEmail(
            message_id=EmailMessageId(str(rfc_message_id)),
            recipient=connection.address,
            sender=EmailAddress(sender),
            subject=str(parsed.get("Subject") or ""),
            raw_content=_body_text(parsed),
            received_at=PosixTime.from_epoch_milliseconds(
                message.internal_date_epoch_millis,
            )
            if message.internal_date_epoch_millis
            else PosixTime.now(),
        )


def _sender_address(message: EmailMessage) -> str | None:
    """The bare address out of a `From` header, which usually also carries a
    display name.
    """
    raw = message.get("From")

    if raw is None:
        return None

    text = str(raw)

    if "<" in text and ">" in text:
        return text.split("<", 1)[1].split(">", 1)[0].strip()

    return text.strip() or None
