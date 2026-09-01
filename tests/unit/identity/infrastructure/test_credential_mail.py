"""The one value in this mail a stranger chose is the address it goes to."""

import pytest

from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.contexts.identity.infrastructure.email import messages
from personal_finance.contexts.identity.infrastructure.email.smtp import (
    LoggingCredentialNotifier,
    MailDeliveryError,
    SmtpCredentialNotifier,
)


@pytest.mark.parametrize(
    "address",
    [
        "victim@example.com\nBcc: attacker@evil.example",
        "victim@example.com\r\nBcc: attacker@evil.example",
        "victim@example.com\x00padding",
    ],
)
def test_an_address_that_could_become_a_second_header_is_refused(
    address: str,
) -> None:
    """The classic way to make somebody else's mail carry a `Bcc:`.

    Refused before the header is written rather than trusted to the header
    writer, because this address came off an unauthenticated request body.
    """
    notifier = SmtpCredentialNotifier(
        host="smtp.invalid",
        port=465,
        username="finflow@example.com",
        password="not-a-real-password",
        from_address="finflow@example.com",
    )

    with pytest.raises(MailDeliveryError, match="line break"):
        notifier.send_verification_code(
            email=Email(address),
            code="123456",
            expires_in_minutes=15,
        )


def test_the_code_is_in_the_message_and_the_address_is_not() -> None:
    # Nothing the caller typed is interpolated into the body: an address is
    # never echoed back into mail that could be aimed at somebody else.
    message = messages.verification_code(code="123456", expires_in_minutes=15)

    assert "123456" in message.text
    assert "123456" in message.html
    assert "@" not in message.text


def test_a_reset_link_is_escaped_into_the_html() -> None:
    message = messages.password_reset(
        link="https://app.example/restablecer?token=a&b",
        expires_in_minutes=30,
    )

    assert "token=a&amp;b" in message.html
    assert "token=a&b" in message.text


def test_the_local_notifier_never_pretends_to_have_sent_anything() -> None:
    # It exists only where no mailbox is configured and the environment is a
    # developer's own machine; the router is what enforces that.
    notifier = LoggingCredentialNotifier()

    notifier.send_verification_code(
        email=Email("person@example.com"),
        code="123456",
        expires_in_minutes=15,
    )
    notifier.send_password_reset(
        email=Email("person@example.com"),
        link="https://app.example/restablecer?token=x",
        expires_in_minutes=30,
    )
