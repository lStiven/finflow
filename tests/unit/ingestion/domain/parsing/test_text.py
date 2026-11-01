from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.parsing.text import (
    extract_text,
    readable_text,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
)


# Verbatim from a delivered Bancolombia alert: quoted-printable, with `=3D`
# for every `=` and soft breaks splitting words mid-token ("Re=\ncibiste").
RAW_HTML = """<tbody> <tr> <td height=3D'38' =
style=3D'font-size:1px;line-height:1px;'>&nbsp;</td> </tr> <tr> <td align=
=3D'left' style=3D'font-family:Arial, sans-serif;font-size:13px;line-height=
:16px;color:#000000;font-weight: normal;' valign=3D'middle'>Bancolombia: Re=
cibiste un pago de Nomina de BOLD.CO SAS por $19,850,806.00 en tu cuenta de=
 Ahorros el 10/08/2026 a las 17:30. Si tienes dudas, llamanos al 0180009319=
87. A tu lado siempre.</td> </tr> <tr> <td height=3D'30' style=3D'font-size=
:1px;line-height:1px;'>&nbsp;</td> </tr> </tbody>"""


def test_soft_breaks_do_not_split_words() -> None:
    text = extract_text(RAW_HTML)

    # "Re=\ncibiste" and "0180009319=\n87" must come back whole; decoding after
    # stripping tags would leave both mangled.
    assert "Recibiste" in text
    assert "018000931987" in text
    assert "=3D" not in text


def test_markup_and_entities_are_gone() -> None:
    text = extract_text(RAW_HTML)

    assert "<td" not in text
    assert "font-family" not in text
    assert "&nbsp;" not in text


def test_the_parser_reads_the_real_html_alert() -> None:
    transaction = BancolombiaParser().parse(extract_text(RAW_HTML))

    assert isinstance(transaction, ExtractedTransaction)
    assert str(transaction.amount.amount) == "19850806.00"
    assert transaction.counterparty == "BOLD.CO SAS"


def test_plain_text_passes_through_unchanged() -> None:
    plain = "Bancolombia: Compraste COP29.259,00 en TIENDAS ARA"

    assert extract_text(plain) == plain


def test_readable_text_reads_the_real_html_alert_without_its_markup() -> None:
    text = readable_text(RAW_HTML)

    assert "Recibiste un pago de Nomina de BOLD.CO SAS por $19,850,806.00" in text
    assert "018000931987" in text
    assert "font-family" not in text
    assert "&nbsp;" not in text


def test_readable_text_keeps_a_label_next_to_its_own_value() -> None:
    body = (
        "<table><tr><td>Valor</td><td>$45.000</td></tr>"
        "<tr><td>Saldo</td><td>$1.200.000</td></tr></table>"
    )

    # Flattened into one line, the amount and the balance are one misreading
    # apart.
    assert readable_text(body) == "Valor | $45.000\nSaldo | $1.200.000"


def test_readable_text_strips_inline_markup_and_styles() -> None:
    body = (
        "<!DOCTYPE html><style>.a { color: red; }</style>"
        "<b>Compraste</b> <strong>$45.000</strong> en <a href='x'>TIENDAS ARA</a>"
    )

    assert readable_text(body) == "Compraste $45.000 en TIENDAS ARA"


def test_readable_text_keeps_the_alt_text_of_an_image() -> None:
    body = "<div><img src='logo.png' alt='Bancolombia'></div><p>Compraste $45.000</p>"

    assert "Bancolombia" in readable_text(body)


def test_readable_text_leaves_plain_text_exactly_as_it_came() -> None:
    body = (
        "---------- Forwarded message ---------\n"
        "De: Bancolombia <alertasynotificaciones@bancolombia.com.co>\n"
        "\n"
        "Compraste $45.000 en TIENDAS ARA. Tu clave=AB12 <a@b.co>\n"
    )

    # The address is not a tag, and `=AB` is not quoted-printable.
    assert readable_text(body) == body


def test_readable_text_keeps_a_forwarded_address_in_front_of_an_html_body() -> None:
    body = (
        "De: Bancolombia <alertasynotificaciones@bancolombia.com.co>\n"
        "<div>Compraste $45.000 en TIENDAS ARA</div>"
    )

    text = readable_text(body)

    assert "<alertasynotificaciones@bancolombia.com.co>" in text
    assert "Compraste $45.000 en TIENDAS ARA" in text


def test_readable_text_does_not_take_a_separator_line_for_transport_encoding() -> None:
    body = "==========\nCompraste $50.000\nclave=AB12\n"

    assert readable_text(body) == body


def test_readable_text_decodes_entities_in_plain_text_as_the_templates_do() -> None:
    assert readable_text("Compraste en Almac&eacute;n &amp; Cia") == (
        "Compraste en Almacén & Cia"
    )


def test_readable_text_survives_a_head_nobody_closed() -> None:
    body = "<html><head><meta charset='utf-8'><body><p>Compraste $50.000</p>"

    assert readable_text(body) == "Compraste $50.000"


def test_readable_text_survives_a_head_nobody_closed_and_no_body_tag() -> None:
    body = "<HEAD><TITLE>Alerta</TITLE><P>Compraste $50.000 en TIENDA"

    assert readable_text(body) == "Compraste $50.000 en TIENDA"


def test_readable_text_drops_what_is_never_shown() -> None:
    body = (
        "<html><head><title>Banco</title><style>p{color:red}</style></head>"
        "<body><script>track()</script><p>Compraste $50.000</p></body></html>"
    )

    assert readable_text(body) == "Compraste $50.000"
