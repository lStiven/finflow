"""Parser tests built from real Lulo bank alerts.

The wording, the punctuation and the labelled legs are verbatim — that is
exactly what the parser has to survive. Names, account digits, transfer keys
and receipt numbers are replaced with fakes of the same shape.

Lulo sends HTML only, so the strings here are what `extract_text` produces
from those emails: one line, entities resolved, the surrounding boilerplate
still in it. One test goes through the markup itself to keep that claim
honest.
"""

from datetime import datetime
from decimal import Decimal
import time
from zoneinfo import ZoneInfo

import pytest

from personal_finance.contexts.ingestion.domain.parsing.lulobank import LuloBankParser
from personal_finance.contexts.ingestion.domain.parsing.text import extract_text
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.shared.domain.value_objects import Currency, PosixTime


BOGOTA = ZoneInfo("America/Bogota")


# Every alert carries the same footer, and the templates have to match with it
# sitting behind them.
FOOTER = (
    "© 2026. Lulo bank, Bogotá, Colombia. En Lulo bank, nunca te pediremos "
    "datos como claves o usuarios mediante correo electrónico. Si tienes "
    "alguna pregunta nos puedes contactar a través de tu app en la sección "
    "Ayuda o consulta www.lulobank.com"
)

BRE_B_INCOMING = (
    "Conoce el detalle de la transacción "
    "Recibiste $1,000 de NOMBRE APELLIDO SEGUNDO "
    "Origen cuenta • 5261 Destino ahorro • 4407 "
    "ID. transacción • 155682205 "
    "Fecha 1 de septiembre de 2026 Hora 1:07 a.m. " + FOOTER
)
BRE_B_OUTGOING = (
    "Conoce los detalles de tu transacción "
    "Realizaste una transferencia a NOMBRE APELLIDO por $1,169 "
    "Origen cuenta • 4407 Llave • CORREO@EJEMPLO.COM "
    "ID. transacción • 155997215 Costo de envío ¡Sin costo! "
    "Fecha 1 de septiembre de 2026 Hora 1:42 a.m. " + FOOTER
)
INCOMING_TRANSFER = (
    "Recibiste dinero en tu cuenta "
    "Recibiste de OMNIPRO COLOMBIA $6.468.490. "
    "Origen ahorro • 7291 BANCO DAVIVIENDA Destino cuenta • 4407 Lulo Bank "
    "Lulo Bank 3216294628 "
    "No. comprobante 202505289012527291000010514416157 "
    "Costo de recepción ¡Sin costo! "
    "Fecha 28 de mayo de 2025 Hora 5:05 p.m. " + FOOTER
)
INCOMING_TRANSFER_MULTIWORD_PAYER = (
    "Recibiste dinero en tu cuenta "
    "Recibiste de ACCIONES-Y-VALOR ACCIONES-Y-VALOR $631.439. "
    "Origen ahorro • 1562 BANCO GNB SUDAMERIS Destino cuenta • 4407 Lulo Bank "
    "Lulo Bank 3216294628 "
    "No. comprobante 202507310860071562000010120030796 "
    "Costo de recepción ¡Sin costo! "
    "Fecha 31 de julio de 2025 Hora 12:05 p.m. " + FOOTER
)

BRE_B_INCOMING_HTML = """
<html><head><style>p { color: red; }</style></head><body>
  <div>
    <h1>Conoce el detalle de la transacci&oacute;n</h1>
    <p>Recibiste $1,000 de NOMBRE APELLIDO SEGUNDO<br></p>
    <p>
        Origen cuenta &#8226; 5261<br/>
        Destino ahorro &#8226; 4407<br/>
        ID. transacci&oacute;n &#8226; 155682205<br/><br/>

        Fecha 1 de septiembre de 2026<br/>
        Hora 1:07 a.m.
    </p>
  </div>
</body></html>
"""


@pytest.fixture
def parser() -> LuloBankParser:
    return LuloBankParser()


def test_bre_b_incoming(parser: LuloBankParser) -> None:
    transaction = parser.parse(BRE_B_INCOMING)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.INCOMING_PAYMENT
    assert transaction.direction is TransactionDirection.INCOMING
    # A comma here groups thousands: `$1,000` is a thousand pesos, and reading
    # it as one peso would be off by three orders of magnitude.
    assert transaction.amount.amount == Decimal("1000")
    assert transaction.amount.currency is Currency.COP
    assert transaction.counterparty == "NOMBRE APELLIDO SEGUNDO"
    assert transaction.bank == "lulo bank"


def test_incoming_money_lands_on_the_destination_leg(parser: LuloBankParser) -> None:
    transaction = parser.parse(BRE_B_INCOMING)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.instrument is not None
    # 5261 is the payer's account at their own bank. Routing the movement
    # there would place it on an account the reader does not hold.
    assert transaction.instrument.last_four == "4407"
    assert transaction.instrument.kind is InstrumentKind.ACCOUNT


def test_bre_b_incoming_is_read_in_bogota(parser: LuloBankParser) -> None:
    transaction = parser.parse(BRE_B_INCOMING)

    assert isinstance(transaction, ExtractedTransaction)
    # 1:07 a.m. in Bogotá, not in UTC: read as UTC it would land on the
    # previous evening and be counted in the wrong day.
    assert transaction.occurred_at == PosixTime.from_datetime(
        datetime(2026, 9, 1, 1, 7, tzinfo=BOGOTA),
    )


def test_bre_b_outgoing(parser: LuloBankParser) -> None:
    transaction = parser.parse(BRE_B_OUTGOING)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.TRANSFER
    assert transaction.direction is TransactionDirection.OUTGOING
    assert transaction.amount.amount == Decimal("1169")
    # The recipient's name, not the key the money was sent to.
    assert transaction.counterparty == "NOMBRE APELLIDO"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "4407"
    assert transaction.instrument.kind is InstrumentKind.ACCOUNT


def test_incoming_transfer(parser: LuloBankParser) -> None:
    transaction = parser.parse(INCOMING_TRANSFER)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.INCOMING_PAYMENT
    assert transaction.direction is TransactionDirection.INCOMING
    # Dots group thousands in this one and commas did in the Bre-B alert
    # above: the same bank writes both, so neither may be assumed.
    assert transaction.amount.amount == Decimal("6468490")
    assert transaction.counterparty == "OMNIPRO COLOMBIA"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "4407"


def test_the_counterparty_bank_between_the_legs_is_skipped(
    parser: LuloBankParser,
) -> None:
    transaction = parser.parse(INCOMING_TRANSFER_MULTIWORD_PAYER)

    assert isinstance(transaction, ExtractedTransaction)
    # `Origen ahorro • 1562 BANCO GNB SUDAMERIS` puts a bank name between the
    # two legs, and the payer's own name is four words long.
    assert transaction.counterparty == "ACCIONES-Y-VALOR ACCIONES-Y-VALOR"
    assert transaction.amount.amount == Decimal("631439")
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "4407"


def test_noon_is_not_shifted(parser: LuloBankParser) -> None:
    transaction = parser.parse(INCOMING_TRANSFER_MULTIWORD_PAYER)

    assert isinstance(transaction, ExtractedTransaction)
    # 12:05 p.m. is five past noon. `+12` on every p.m. hour would make it
    # five past midnight of the next day.
    assert transaction.occurred_at == PosixTime.from_datetime(
        datetime(2025, 7, 31, 12, 5, tzinfo=BOGOTA),
    )


def test_the_same_account_is_one_fingerprint_whatever_lulo_calls_it(
    parser: LuloBankParser,
) -> None:
    incoming = parser.parse(BRE_B_INCOMING)
    transfer = parser.parse(INCOMING_TRANSFER)

    assert isinstance(incoming, ExtractedTransaction)
    assert isinstance(transfer, ExtractedTransaction)
    assert incoming.instrument is not None
    assert transfer.instrument is not None
    # `Destino ahorro • 4407` in one alert and `Destino cuenta • 4407` in the
    # other, for one real account. Honouring the bank's word would fingerprint
    # it two ways and make the owner declare it twice.
    assert incoming.instrument == transfer.instrument


def test_the_html_the_bank_actually_sends(parser: LuloBankParser) -> None:
    transaction = parser.parse(extract_text(BRE_B_INCOMING_HTML))

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.amount.amount == Decimal("1000")
    assert transaction.counterparty == "NOMBRE APELLIDO SEGUNDO"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "4407"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "Tu clave dinámica es 123456. No la compartas con nadie. " + FOOTER,
            "an alert that moves no money",
        ),
        (
            "Recibiste $1,000 de NOMBRE APELLIDO SEGUNDO Origen cuenta • 5261 "
            "Destino ahorro • 4407 ID. transacción • 155682205",
            "a movement with no date",
        ),
        (
            "Recibiste de OMNIPRO COLOMBIA $6.468.490. Destino cuenta • 4407 "
            "Fecha 28 de mayo de 2025 Hora 5:05 p.m.",
            "a movement with no origin leg",
        ),
    ],
)
def test_a_partial_match_is_a_miss(
    parser: LuloBankParser,
    text: str,
    reason: str,
) -> None:
    # A miss falls through to the LLM. A half-filled transaction would not.
    assert parser.parse(text) is None, reason


def test_an_alert_missing_a_leg_does_not_borrow_from_the_next(
    parser: LuloBankParser,
) -> None:
    body = (
        "Recibiste $1,000 de PAGADOR UNO Origen cuenta • 5261 "
        "Conoce el detalle de la transacción "
        "Recibiste $9,999 de PAGADOR DOS Origen cuenta • 1111 "
        "Destino ahorro • 9999 Fecha 5 de mayo de 2020 Hora 3:00 p.m."
    )

    transaction = parser.parse(body)

    assert isinstance(transaction, ExtractedTransaction)
    # The first alert has no destination leg, so it is not a movement this
    # parser can place. What must never happen is the two being spliced: its
    # $1,000 booked against 9999 and dated from the alert below it.
    assert transaction.amount.amount == Decimal("9999")
    assert transaction.counterparty == "PAGADOR DOS"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "9999"


def test_a_long_near_miss_does_not_stall_the_worker(parser: LuloBankParser) -> None:
    # 14 KB of text that starts every template and finishes none. With
    # unbounded gaps this backtracked for 45 seconds — long enough for SQS to
    # redeliver the message and for the worker to do it all over again.
    body = "Recibiste $1,000 de NOMBRE APELLIDO Origen cuenta • 5261 " * 250

    started = time.monotonic()
    transaction = parser.parse(body)
    elapsed = time.monotonic() - started

    assert transaction is None
    assert elapsed < 1.0


@pytest.mark.parametrize(
    ("when", "reason"),
    [
        ("Fecha 1 de sep de 2026 Hora 1:07 a.m.", "an abbreviated month"),
        ("Fecha 1 de septiembre de 2026 Hora 13:07 p.m.", "a 13 o'clock"),
    ],
)
def test_a_moment_this_parser_cannot_read_is_a_miss(
    parser: LuloBankParser,
    when: str,
    reason: str,
) -> None:
    body = (
        "Recibiste $1,000 de NOMBRE APELLIDO Origen cuenta • 5261 "
        f"Destino ahorro • 4407 {when}"
    )

    # A miss falls through to the LLM. Matching and *then* failing to read the
    # field would mark the notification FAILED and lose the movement, which is
    # the worse of the two answers to a wording nobody has seen.
    assert parser.parse(body) is None, reason
