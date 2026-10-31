"""Emailing a notice about somebody's account, and the local stand-in for it.

The same Gmail mailbox and App Password Identity sends its codes from: one
account, implicit TLS on 465, a connection per message because these are
days apart. Its own adapter rather than Identity's, the way each context
keeps its own Telegram or secret plumbing — what it sends and when it gives
up are this context's decisions.

The address is the one value in the envelope that is not ours, so one that
could fold into a second header is refused before it reaches one.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr
import logging
import smtplib
import ssl

from pydantic import SecretStr

from personal_finance.contexts.alerts.application.messages import (
    MailRecipient,
    ModelPause,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.infrastructure.notice_copy import (
    NoticeMail,
    compose_model_pause_mail,
)


_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


def _checked_address(recipient: MailRecipient) -> str:
    address = recipient.address

    if (
        "@" not in address
        or any(c in address for c in "\r\n")
        or not address.isprintable()
    ):
        raise DestinationRefusedError(
            "Refusing to mail an address that is not one line"
        )

    return address


class SmtpAccountMailer:
    """`AccountMailer` over one SMTP account."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: SecretStr,
        from_address: str,
        display_timezone: str,
        from_name: str = "Finflow",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._from_name = from_name
        self._display_timezone = display_timezone
        self._timeout = timeout

    def send_model_pause(self, *, to: MailRecipient, pause: ModelPause) -> None:
        self._send(
            to,
            compose_model_pause_mail(
                pause,
                name=to.name,
                timezone=self._display_timezone,
            ),
        )

    def _send(self, recipient: MailRecipient, mail: NoticeMail) -> None:
        envelope = EmailMessage()
        envelope["Subject"] = mail.subject
        envelope["From"] = formataddr((self._from_name, self._from_address))
        envelope["To"] = _checked_address(recipient)
        # Every reply would land in the mailbox the ingest worker reads.
        envelope["Auto-Submitted"] = "auto-generated"
        envelope.set_content(mail.text)
        envelope.add_alternative(mail.html, subtype="html")

        try:
            # A verifying context, explicitly: without one `smtplib` checks
            # neither the certificate nor the host name, and the App Password
            # goes to whoever answered.
            with smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout,
                context=ssl.create_default_context(),
            ) as server:
                server.login(self._username, self._password.get_secret_value())
                server.send_message(envelope)
        except smtplib.SMTPRecipientsRefused as error:
            raise DestinationRefusedError(
                "The mail server refused the address"
            ) from error
        except (smtplib.SMTPException, OSError) as error:
            # By type: the address stays out of the log.
            raise TransportUnavailableError(
                f"The mail server could not take the notice ({type(error).__name__})",
            ) from None

        _logger.info("account notice emailed")


class LoggingAccountMailer:
    """`AccountMailer` that writes the notice instead of sending it.

    For a developer's own machine, chosen by `ENVIRONMENT` alone, like the
    logging Telegram sender: nothing a setting could switch on in production.
    """

    def __init__(self, *, display_timezone: str) -> None:
        self._display_timezone = display_timezone

    def send_model_pause(self, *, to: MailRecipient, pause: ModelPause) -> None:
        mail = compose_model_pause_mail(
            pause,
            name=to.name,
            timezone=self._display_timezone,
        )
        _logger.info(
            "account notice email (not sent: local)\nsubject: %s\n\n%s",
            mail.subject,
            mail.text,
        )
