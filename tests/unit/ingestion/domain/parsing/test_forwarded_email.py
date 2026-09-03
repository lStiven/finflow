from personal_finance.contexts.ingestion.domain.parsing.forwarded_email import (
    forwarded_sender,
)
from personal_finance.contexts.ingestion.domain.parsing.text import extract_text


# The header Gmail writes on a hand-forward, verbatim: the marker in English
# and the field in Spanish, which is what the same message really carries.
FORWARDED = (
    "---------- Forwarded message ---------\r\n"
    "De: <alertasynotificaciones@bancolombia.com.co>\r\n"
    "Date: mié, 2 sept 2026 a la(s) 4:14 p.m.\r\n"
    "Subject: Alertas y Notificaciones\r\n"
    "To: <STIVEN.DDH@gmail.com>\r\n"
    "\r\n"
    "Bancolombia: Pagaste $2,724,006 en la tarjeta de credito *7653 desde la\r\n"
    "cuenta *5261, el 02/09/2026 16:14.\r\n"
)


def test_reads_who_the_forwarded_message_came_from() -> None:
    sender = forwarded_sender(FORWARDED)

    assert sender is not None
    assert sender.value == "alertasynotificaciones@bancolombia.com.co"


def test_the_normalised_text_no_longer_carries_the_answer() -> None:
    # Why this reads the raw body. `extract_text` sees `<alertas@…>` as a tag
    # and drops it, leaving a "De:" with nothing after it — asking it the
    # question would always answer None.
    assert "alertasynotificaciones" not in extract_text(FORWARDED)
    assert forwarded_sender(extract_text(FORWARDED)) is None


def test_accepts_a_client_that_writes_the_marker_in_spanish() -> None:
    text = (
        "---------- Mensaje reenviado ---------\n"
        "De: Bancolombia <alertasynotificaciones@bancolombia.com.co>\n"
        "Para: alguien@gmail.com\n"
    )

    sender = forwarded_sender(text)

    assert sender is not None
    assert sender.domain == "bancolombia.com.co"


def test_accepts_a_client_that_writes_the_field_in_english() -> None:
    text = (
        "---------- Forwarded message ---------\n"
        "From: alertasynotificaciones@bancolombia.com.co\n"
        "Date: Wed, 2 Sep 2026\n"
    )

    sender = forwarded_sender(text)

    assert sender is not None
    assert sender.domain == "bancolombia.com.co"


def test_an_email_that_is_not_a_forward_has_no_original_sender() -> None:
    # The ordinary case, and the one that must not change: an alert delivered
    # by the bank's own forwarding rule keeps the bank as its real sender and
    # never reaches this code with anything to find.
    text = (
        "Bancolombia: Compraste $67,400 en CREPES & WAFFLES 45 con tu T.Cred "
        "*1234, el 29/08/2026 20:10."
    )

    assert forwarded_sender(text) is None


def test_the_anti_fraud_footer_is_not_read_as_the_sender() -> None:
    # Every Bancolombia alert asks the reader to report suspicious mail to an
    # address on the bank's own domain. Scanning the body for the first
    # bank-looking address would attribute any email quoting that footer to
    # Bancolombia; only the forward header counts.
    text = (
        "Un mensaje cualquiera, sin encabezado de reenvío. "
        "Si recibes un correo sospechoso repórtalo a "
        "correosospechoso@bancolombia.com.co para validar la veracidad."
    )

    assert forwarded_sender(text) is None


def test_an_address_far_below_the_marker_is_not_the_origin() -> None:
    # The origin sits immediately under the marker. A "De:" hundreds of
    # characters later belongs to something else — a second quoted message, a
    # signature — and guessing from it is how a forward gets misattributed.
    text = (
        "---------- Forwarded message ---------\n"
        + "relleno " * 120
        + "\nDe: <alertasynotificaciones@bancolombia.com.co>\n"
    )

    assert forwarded_sender(text) is None


def test_a_malformed_address_is_no_answer_rather_than_a_crash() -> None:
    text = "---------- Forwarded message ---------\nDe: <no-es-un-correo>\n"

    assert forwarded_sender(text) is None


# What Gmail actually sends when the message is HTML: the field name preceded
# by a tag rather than a space, a display name in its own element, and the
# address inside a `mailto:` anchor.
HTML_FORWARD = (
    '<div dir="ltr"><br><div class="gmail_quote">'
    '<div dir="ltr" class="gmail_attr">---------- Forwarded message ---------'
    '<br>De: <strong class="gmail_sendername" dir="auto">'
    "Alertas y Notificaciones Bancolombia</strong> "
    '<span dir="auto">&lt;<a href="mailto:alertasynotificaciones@bancolombia.com.co">'
    "alertasynotificaciones@bancolombia.com.co</a>&gt;</span><br>"
    "Date: mié, 2 sept 2026<br>To: &lt;alguien@gmail.com&gt;<br></div>"
    "<br>Bancolombia: Pagaste $2,724,006 en la tarjeta de credito *7653 "
    "desde la cuenta *5261, el 02/09/2026 16:14.</div></div>"
)


def test_reads_the_sender_out_of_an_html_forward() -> None:
    sender = forwarded_sender(HTML_FORWARD)

    assert sender is not None
    assert sender.value == "alertasynotificaciones@bancolombia.com.co"


def test_the_mailto_attribute_yields_the_address_and_not_the_attribute() -> None:
    # `href="mailto:alertas@…"` is where the address is written in HTML. Read
    # carelessly it comes back as `"mailto:alertas@…"`, whose domain then ends
    # in a quote and matches no bank.
    sender = forwarded_sender(HTML_FORWARD)

    assert sender is not None
    assert sender.domain == "bancolombia.com.co"


def test_a_word_ending_in_de_is_not_read_as_the_field() -> None:
    # The lookbehind's whole job: "grande:" contains "de:".
    text = (
        "---------- Forwarded message ---------\n"
        "algo grande: <alertasynotificaciones@bancolombia.com.co>\n"
    )

    assert forwarded_sender(text) is None
