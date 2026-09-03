from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.parsing.lulobank import LuloBankParser
from personal_finance.contexts.ingestion.domain.parsing.registry import ParserRegistry
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress


def test_the_real_alert_sender_resolves_to_bancolombia() -> None:
    registry = ParserRegistry()
    sender = EmailAddress("alertasynotificaciones@an.notificacionesbancolombia.com")

    parser = registry.for_sender(sender)

    assert isinstance(parser, BancolombiaParser)


def test_matching_is_by_domain_not_by_full_address() -> None:
    registry = ParserRegistry()

    # Banks rotate the local part of their alert addresses; the domain is the
    # stable part.
    assert (
        registry.for_sender(
            EmailAddress("otro-remitente@an.notificacionesbancolombia.com"),
        )
        is not None
    )


def test_an_unknown_sender_has_no_parser() -> None:
    registry = ParserRegistry()

    assert registry.for_sender(EmailAddress("alerts@some-other-bank.com")) is None


def test_a_lookalike_domain_does_not_match() -> None:
    registry = ParserRegistry()

    # Email content is untrusted: a domain that merely contains the bank's name
    # must not select its parser.
    assert (
        registry.for_sender(
            EmailAddress("alerts@an.notificacionesbancolombia.com.evil.co"),
        )
        is None
    )


def test_lulo_bank_resolves_by_its_from_domain() -> None:
    registry = ParserRegistry()
    sender = EmailAddress("notificaciones@lulobank.com")

    assert isinstance(registry.for_sender(sender), LuloBankParser)


def test_the_ses_domain_lulo_sends_through_is_not_a_bank() -> None:
    registry = ParserRegistry()

    # Lulo's message ids come from `email.amazonses.com`, which is shared with
    # every other SES customer. Keying on it would hand any of them Lulo's
    # templates.
    assert registry.for_sender(EmailAddress("bounce@email.amazonses.com")) is None


FORWARDED_HEADER = (
    "---------- Forwarded message ---------\r\n"
    "De: <alertasynotificaciones@bancolombia.com.co>\r\n"
    "To: <alguien@gmail.com>\r\n"
    "\r\n"
    "Bancolombia: Pagaste $2,724,006 en la tarjeta de credito *7653 desde la\r\n"
    "cuenta *5261, el 02/09/2026 16:14.\r\n"
)


def test_a_forwarded_message_resolves_by_the_bank_that_sent_the_original() -> None:
    registry = ParserRegistry()

    assert isinstance(
        registry.for_forwarded_message(FORWARDED_HEADER),
        BancolombiaParser,
    )


def test_an_email_that_is_not_a_forward_resolves_to_nothing() -> None:
    registry = ParserRegistry()

    # The second question is only ever asked after `for_sender` said None, and
    # it has to be as quiet as that answer was.
    assert registry.for_forwarded_message("Bancolombia: Compraste $1 ...") is None


def test_a_forward_from_a_bank_nobody_parses_resolves_to_nothing() -> None:
    registry = ParserRegistry()
    text = "---------- Forwarded message ---------\nDe: <alertas@otro-banco.com>\n"

    assert registry.for_forwarded_message(text) is None


def test_a_lookalike_domain_in_a_forward_header_does_not_match_either() -> None:
    registry = ParserRegistry()
    text = (
        "---------- Forwarded message ---------\n"
        "De: <alertas@bancolombia.com.co.evil.co>\n"
    )

    assert registry.for_forwarded_message(text) is None
