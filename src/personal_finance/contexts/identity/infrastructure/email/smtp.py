"""Sending this context's mail, over SMTP, and the local stand-in for it.

The transport is the same Gmail arrangement the rest of the deployment already
runs on: one account, an App Password, implicit TLS on 465. No SES, because
SES needs a verified domain and a sandbox exit before it will mail a stranger,
and this deployment has no domain to verify.

Two things here are security, not plumbing:

* **The recipient address is refused if it could become more than one header.**
  It is the one value in the message a stranger chose, and a bare newline in
  it is the classic way to append `Bcc:` to somebody else's mail.
* **`LoggingCredentialNotifier` writes codes and reset links to the log**, so
  it is built only where the deployment is a developer's own machine. Anywhere
  else, an unconfigured mailbox is a startup failure — see the router — rather
  than a silent downgrade to printing credentials into CloudWatch.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import formataddr
import logging
import smtplib

from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.contexts.identity.infrastructure.email import messages
from personal_finance.contexts.identity.infrastructure.email.messages import MailMessage


_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


class MailDeliveryError(Exception):
    """Raised when the message could not be handed to the mail server.

    Never swallowed into a success: the caller is about to tell somebody to go
    and read their inbox.
    """


def _checked_recipient(email: Email) -> str:
    """The address, or a refusal to put it in a header.

    `Email` already rejects anything without an `@`, but not a control
    character, and the header writer would happily fold on one.
    """
    address = email.value

    if any(character in address for character in "\r\n") or not address.isprintable():
        raise MailDeliveryError("Refusing to mail an address with a line break in it")

    return address


class SmtpCredentialNotifier:
    """`CredentialNotifier` over one SMTP account.

    Opens a connection per message rather than holding one open, for the same
    reason the IMAP reader does: these are minutes or hours apart, and a
    long-lived connection is a thing that breaks silently between uses.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        from_name: str = "Finflow",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address
        self._from_name = from_name
        self._timeout = timeout

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        self._send(
            email,
            messages.verification_code(
                code=code,
                expires_in_minutes=expires_in_minutes,
            ),
        )

    def send_registration_notice_for_existing_account(self, *, email: Email) -> None:
        self._send(email, messages.registration_attempt_on_existing_account())

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None:
        self._send(
            email,
            messages.password_reset(link=link, expires_in_minutes=expires_in_minutes),
        )

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None:
        self._send(email, messages.password_reset_for_unknown_account())

    def _send(self, email: Email, message: MailMessage) -> None:
        recipient = _checked_recipient(email)
        envelope = EmailMessage()
        envelope["Subject"] = message.subject
        envelope["From"] = formataddr((self._from_name, self._from_address))
        envelope["To"] = recipient
        # Nothing in this mail is worth replying to, and every reply would
        # land in the mailbox the ingest worker reads.
        envelope["Auto-Submitted"] = "auto-generated"
        envelope.set_content(message.text)
        envelope.add_alternative(message.html, subtype="html")

        try:
            with smtplib.SMTP_SSL(
                self._host,
                self._port,
                timeout=self._timeout,
            ) as server:
                server.login(self._username, self._password)
                server.send_message(envelope)
        except (smtplib.SMTPException, OSError) as error:
            # The address never goes in the log line: whether a given address
            # is mid-registration is exactly what this context does not say
            # out loud.
            _logger.exception("Could not send a credential email")

            raise MailDeliveryError("Could not send the email") from error


class LoggingCredentialNotifier:
    """`CredentialNotifier` that prints what it would have sent.

    For a developer's own machine, where there is no mailbox and a script has
    to be able to finish the flow. It writes live credentials to the log, so
    nothing may build it outside `ENVIRONMENT=local`.
    """

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        _logger.warning(
            "[local] verification code for %s: %s (valid %s minutes)",
            email.value,
            code,
            expires_in_minutes,
        )

    def send_registration_notice_for_existing_account(self, *, email: Email) -> None:
        _logger.warning("[local] %s already has an account; no code sent", email.value)

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None:
        _logger.warning(
            "[local] password reset link for %s: %s (valid %s minutes)",
            email.value,
            link,
            expires_in_minutes,
        )

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None:
        _logger.warning("[local] no account for %s; no reset link sent", email.value)
