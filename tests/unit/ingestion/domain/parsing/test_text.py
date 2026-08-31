from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.parsing.text import extract_text
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
