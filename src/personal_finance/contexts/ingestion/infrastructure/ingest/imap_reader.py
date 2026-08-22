"""Reading the one Gmail account every user forwards their bank email to.

Plain IMAP over SSL with a Google App Password — no OAuth, no Google Cloud
project, no consent screen, and nothing here expires on its own the way a
per-user grant does. `+alias` addressing is what lets one mailbox serve every
user: Gmail delivers `base+anything@gmail.com` straight into `base@gmail.com`,
and the alias survives on `Delivered-To`, which is what this reader keys on.
"""

from __future__ import annotations

from collections.abc import Generator, Sequence
import contextlib
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime
import imaplib
import logging

from personal_finance.contexts.ingestion.application.ports import InboundEmail
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)

_SEEN_FLAG = "(\\Seen)"


class ImapIngestMailboxReader:
    """`IngestMailboxReader` over one Gmail account, via IMAP.

    Opens a fresh connection per call rather than holding one open: this runs
    from a poll loop with a minute or more between calls, and a long-lived
    IMAP connection is exactly the kind of thing a flaky network breaks
    silently, with no warning before the next call fails.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        address: str,
        app_password: str,
    ) -> None:
        self._host = host
        self._port = port
        self._address = address
        self._app_password = app_password
        # Where to find the IMAP position of a message `ack` is asked to mark
        # handled. Keyed by our own domain id rather than carried on
        # `InboundEmail` itself, so the port stays free of an IMAP detail no
        # other implementation would have a use for.
        self._uids_by_message_id: dict[str, str] = {}

    def fetch_new(self) -> Sequence[InboundEmail]:
        with self._connection() as connection:
            # `None` is the charset argument IMAP's SEARCH takes; imaplib's
            # stubs only model `uid`'s trailing args as `str`, so this one
            # call is typed more narrowly than it actually is.
            status, data = connection.uid(
                "search",
                None,  # pyright: ignore[reportArgumentType]
                "UNSEEN",
            )

            if status != "OK":
                _logger.warning("imap search failed", extra={"status": status})

                return ()

            emails: list[InboundEmail] = []

            for raw_uid in data[0].split():
                email = self._fetch_one(connection, raw_uid.decode())

                if email is not None:
                    emails.append(email)

            return tuple(emails)

    def ack(self, emails: Sequence[InboundEmail]) -> None:
        """Mark every one of these messages handled, in a single round trip.

        One connection for the whole batch rather than one per message: a
        backlog of dozens of messages must not turn into dozens of IMAP
        logins, which is both slow and the kind of repeated-auth pattern a
        provider's abuse detection notices.
        """
        uids = [
            uid
            for email in emails
            if (uid := self._uids_by_message_id.pop(email.message_id.value, None))
            is not None
        ]

        if not uids:
            return

        with self._connection() as connection:
            connection.uid("store", ",".join(uids), "+FLAGS", _SEEN_FLAG)

    def _fetch_one(
        self,
        connection: imaplib.IMAP4_SSL,
        uid: str,
    ) -> InboundEmail | None:
        # `BODY.PEEK[]` fetches the full message without the side effect
        # plain `(RFC822)`/`BODY[]` have: IMAP marks a message `\Seen` the
        # moment it is fetched that way, regardless of what the caller does
        # with it afterwards. That would defeat the whole point of `ack` —
        # a message must stay looking new until it is durably recorded, so a
        # crash between fetching and recording leaves it for the next poll
        # to pick up again, not lost.
        status, data = connection.uid("fetch", uid, "(BODY.PEEK[])")

        if status != "OK" or not data or data[0] is None:
            _logger.warning("imap fetch failed", extra={"uid": uid})

            return None

        raw = data[0][1]

        if not isinstance(raw, bytes):
            _logger.warning("unexpected imap fetch payload", extra={"uid": uid})

            return None

        parsed = message_from_bytes(raw, policy=policy.default)
        recipient = _recipient_address(parsed)
        sender = _sender_address(parsed)

        if recipient is None or sender is None:
            _logger.warning(
                "undecodable ingest message, skipped",
                extra={"uid": uid},
            )

            return None

        try:
            email = InboundEmail(
                recipient=EmailAddress(recipient),
                sender=EmailAddress(sender),
                message_id=EmailMessageId(
                    str(parsed.get("Message-ID") or f"imap:{uid}"),
                ),
                subject=str(parsed.get("Subject") or ""),
                raw_content=_body_text(parsed),
                received_at=_received_at(parsed),
            )
        except ValueError:
            _logger.warning(
                "malformed ingest message, skipped",
                extra={"uid": uid},
            )

            return None

        self._uids_by_message_id[email.message_id.value] = uid

        return email

    @contextlib.contextmanager
    def _connection(self) -> Generator[imaplib.IMAP4_SSL]:
        connection = imaplib.IMAP4_SSL(self._host, self._port)

        try:
            connection.login(self._address, self._app_password)
            connection.select("INBOX")
            yield connection
        finally:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                connection.logout()


def _recipient_address(message: EmailMessage) -> str | None:
    """The address this message was actually delivered to.

    `Delivered-To` is what Gmail's own MTA stamps at final delivery — the
    `+alias` that says which user this is — and is more trustworthy than `To`,
    which reflects whatever address a sender or forwarder wrote, not where the
    message actually landed.
    """
    raw = message.get("Delivered-To") or message.get("To")

    return _bare_address(str(raw)) if raw is not None else None


def _sender_address(message: EmailMessage) -> str | None:
    raw = message.get("From")

    return _bare_address(str(raw)) if raw is not None else None


def _bare_address(text: str) -> str | None:
    """The address out of a header that usually also carries a display name.

    `email.utils.getaddresses` (RFC 2822-aware) rather than a hand-rolled
    `<...>` split: a display name containing a comma or its own `<`/`>` — a
    real thing banks put in a `From` header — would otherwise be misparsed.
    """
    addresses = getaddresses([text])

    if not addresses:
        return None

    _display_name, address = addresses[0]

    return address.strip() or None


def _body_text(message: EmailMessage) -> str:
    """The readable text of a message. Prefers `text/plain`, since that is
    what the deterministic parsers were written against.
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


def _received_at(message: EmailMessage) -> PosixTime:
    raw_date = message.get("Date")

    if raw_date is None:
        return PosixTime.now()

    try:
        parsed = parsedate_to_datetime(str(raw_date))
    except (TypeError, ValueError):
        return PosixTime.now()

    if parsed.tzinfo is None:
        return PosixTime.now()

    return PosixTime.from_datetime(parsed)
