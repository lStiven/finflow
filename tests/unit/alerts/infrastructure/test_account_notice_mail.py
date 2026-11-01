"""What the suspension notice says, and how it is mailed."""

import smtplib
import ssl
from typing import Any, ClassVar, Self

from pydantic import SecretStr
import pytest

from personal_finance.contexts.alerts.application.messages import (
    MailRecipient,
    ModelPause,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.infrastructure.email import smtp
from personal_finance.contexts.alerts.infrastructure.email.smtp import (
    SmtpAccountMailer,
)
from personal_finance.contexts.alerts.infrastructure.notice_copy import (
    TEMPLATE_BANKS,
    compose_model_pause_chat,
    compose_model_pause_mail,
    format_long_moment,
)
from personal_finance.contexts.ingestion.domain.parsing.registry import known_banks
from personal_finance.shared.domain.value_objects import PosixTime


TIMEZONE = "America/Bogota"
# 2026-11-01 09:05 in Bogotá.
UNTIL = PosixTime.from_epoch_seconds(1_793_541_900)


def _pause(strike: int) -> ModelPause:
    return ModelPause(
        strike=strike,
        paused_until=None if strike >= 3 else UNTIL,
        offenses=12,
    )


def test_the_end_of_a_pause_reads_as_a_person_would_say_it() -> None:
    assert format_long_moment(UNTIL, timezone=TIMEZONE) == (
        "1 de noviembre a las 9:05 a. m."
    )


@pytest.mark.parametrize(
    ("strike", "subject"),
    [
        (1, "Pausamos la lectura de tus correos por 24 horas"),
        (2, "Segundo y último aviso: pausa de 3 días"),
        (3, "Finflow dejó de leer los correos que no reconoce"),
    ],
)
def test_each_strike_has_its_approved_subject(strike: int, subject: str) -> None:
    assert (
        compose_model_pause_mail(
            _pause(strike),
            name="Ana",
            timezone=TIMEZONE,
        ).subject
        == subject
    )


def test_a_pause_says_until_when_and_how_many() -> None:
    mail = compose_model_pause_mail(_pause(1), name="Ana", timezone=TIMEZONE)

    assert mail.text.startswith("Hola, Ana:")
    assert "12 correos" in mail.text
    assert "hasta el 1 de noviembre a las 9:05 a. m." in mail.text


def test_the_last_strike_promises_no_channel_that_does_not_exist_yet() -> None:
    mail = compose_model_pause_mail(_pause(3), name="Ana", timezone=TIMEZONE)

    assert "{canal}" not in mail.text
    assert "servicio al cliente (disponible muy pronto)" in mail.text


def test_an_account_without_a_name_is_greeted_without_one() -> None:
    mail = compose_model_pause_mail(_pause(1), name=None, timezone=TIMEZONE)

    assert mail.text.startswith("Hola:")


def test_a_name_cannot_add_lines_or_markup() -> None:
    mail = compose_model_pause_mail(
        _pause(1),
        name="Ana\n\nBcc: x@y.com <b>hola</b>",
        timezone=TIMEZONE,
    )

    assert mail.text.splitlines()[0] == "Hola, Ana Bcc: x@y.com <b>hola</b>:"
    assert "<b>" not in mail.html
    assert "&lt;b&gt;" in mail.html


def test_the_chat_notice_says_what_comes_next() -> None:
    assert "3 días" in compose_model_pause_chat(_pause(1), timezone=TIMEZONE)
    assert "último aviso" in compose_model_pause_chat(_pause(2), timezone=TIMEZONE)
    assert "servicio al cliente" in compose_model_pause_chat(
        _pause(3),
        timezone=TIMEZONE,
    )


def test_the_copy_names_every_bank_a_template_reads() -> None:
    """The notice promises these keep being read; a parser added in
    Ingestion has to be promised here too."""
    for bank in known_banks():
        assert bank.name.split()[0] in TEMPLATE_BANKS


# ----------------------------------------------------------------- sending


class FakeSMTP:
    sent: ClassVar[list[Any]] = []
    error: ClassVar[Exception | None] = None

    contexts: ClassVar[list[ssl.SSLContext | None]] = []

    def __init__(
        self,
        host: str,
        port: int,
        *,
        timeout: float,
        context: ssl.SSLContext | None = None,
    ) -> None:
        del host, port, timeout
        FakeSMTP.contexts.append(context)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def login(self, username: str, password: str) -> None:
        del username, password

    def send_message(self, message: Any) -> None:  # noqa: ANN401
        if FakeSMTP.error is not None:
            raise FakeSMTP.error

        FakeSMTP.sent.append(message)


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> SmtpAccountMailer:
    FakeSMTP.sent = []
    FakeSMTP.error = None
    FakeSMTP.contexts = []
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", FakeSMTP)

    return SmtpAccountMailer(
        host="smtp.example.com",
        port=465,
        username="finflow@example.com",
        password=SecretStr("app-password"),
        from_address="finflow@example.com",
        display_timezone=TIMEZONE,
    )


def test_the_notice_is_mailed_to_the_account_and_says_nobody_should_reply(
    mailer: SmtpAccountMailer,
) -> None:
    mailer.send_model_pause(
        to=MailRecipient(address="ana@example.com", name="Ana"),
        pause=_pause(1),
    )

    [message] = FakeSMTP.sent
    assert message["To"] == "ana@example.com"
    assert message["Auto-Submitted"] == "auto-generated"
    # The server's certificate is checked before the App Password goes out.
    [context] = FakeSMTP.contexts
    assert context is not None
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_an_address_that_could_become_two_headers_is_refused(
    mailer: SmtpAccountMailer,
) -> None:
    with pytest.raises(DestinationRefusedError):
        mailer.send_model_pause(
            to=MailRecipient(address="ana@example.com\nBcc: x@y.com", name=None),
            pause=_pause(1),
        )

    assert FakeSMTP.sent == []


def test_a_mail_server_that_fails_is_worth_another_try(
    mailer: SmtpAccountMailer,
) -> None:
    FakeSMTP.error = smtplib.SMTPServerDisconnected("gone")

    with pytest.raises(TransportUnavailableError):
        mailer.send_model_pause(
            to=MailRecipient(address="ana@example.com", name=None),
            pause=_pause(1),
        )


def test_a_mailbox_the_server_refuses_is_not_retried(
    mailer: SmtpAccountMailer,
) -> None:
    FakeSMTP.error = smtplib.SMTPRecipientsRefused({"ana@example.com": (550, b"no")})

    with pytest.raises(DestinationRefusedError):
        mailer.send_model_pause(
            to=MailRecipient(address="ana@example.com", name=None),
            pause=_pause(1),
        )
