"""Parser tests built from real Bancolombia alerts.

The wording is verbatim — including the missing accents and the inconsistent
masking — because that is exactly what the parser has to survive. Names,
account digits and transfer keys are replaced with fakes of the same shape.
"""

from decimal import Decimal

import pytest

from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
)
from personal_finance.shared.domain.value_objects import Currency


CARD_PURCHASE = (
    "Bancolombia: Compraste COP29.259,00 en TIENDAS ARA con tu T.Cred *7653, "
    "el 20/08/2026 a las 12:00. Si tienes dudas, encuentranos aqui: "
    "6045109095 o 018000931987. Estamos cerca."
)
CARD_PURCHASE_MULTIWORD_MERCHANT = (
    "Bancolombia: Compraste COP37.680,00 en TIENDA D1 VAL TULUA con tu "
    "T.Cred *7653, el 20/08/2026 a las 10:21. Si tienes dudas, encuentranos "
    "aqui: 6045109095 o 018000931987. Estamos cerca."
)
QR_PAYMENT = (
    "Bancolombia: NOMBRE APELLIDO SEGUNDO pagaste $13,600.00 por codigo QR "
    "desde tu cuenta *5261 a la llave 0090860169 el 18/08/2026 a las 13:59. "
    "Con codigo QR es facil y de una. Dudas al 018000912345."
)
TRANSFER = (
    "Bancolombia: Transferiste $112,700.00 desde tu cuenta 5261 a la cuenta "
    "*3017402695 el 11/08/2026 a las 10:16. ¿Dudas? Llamanos al 018000931987. "
    "Estamos cerca."
)
INCOMING_PAYROLL = (
    "Bancolombia: Recibiste un pago de Nomina de BOLD.CO SAS por $19,850,806.00 "
    "en tu cuenta de Ahorros el 10/08/2026 a las 17:30. Si tienes dudas, "
    "llamanos al 018000931987. A tu lado siempre."
)


@pytest.fixture
def parser() -> BancolombiaParser:
    return BancolombiaParser()


def test_card_purchase(parser: BancolombiaParser) -> None:
    transaction = parser.parse(CARD_PURCHASE)

    assert transaction is not None
    assert transaction.kind is TransactionKind.CARD_PURCHASE
    assert transaction.direction is TransactionDirection.OUTGOING
    assert transaction.amount.amount == Decimal("29259.00")
    assert transaction.amount.currency is Currency.COP
    assert transaction.counterparty == "TIENDAS ARA"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.CREDIT_CARD
    assert transaction.instrument.last_four == "7653"


def test_card_purchase_keeps_a_multi_word_merchant_intact(
    parser: BancolombiaParser,
) -> None:
    transaction = parser.parse(CARD_PURCHASE_MULTIWORD_MERCHANT)

    assert transaction is not None
    assert transaction.counterparty == "TIENDA D1 VAL TULUA"
    assert transaction.amount.amount == Decimal("37680.00")


def test_card_purchase_time_is_bogota_local(parser: BancolombiaParser) -> None:
    transaction = parser.parse(CARD_PURCHASE)

    assert transaction is not None
    # 12:00 in Bogotá is 17:00 UTC. Reading the alert as UTC would move every
    # evening purchase into the next day.
    assert transaction.occurred_at.to_datetime().isoformat() == (
        "2026-08-20T17:00:00+00:00"
    )


def test_qr_payment(parser: BancolombiaParser) -> None:
    transaction = parser.parse(QR_PAYMENT)

    assert transaction is not None
    assert transaction.kind is TransactionKind.QR_PAYMENT
    assert transaction.direction is TransactionDirection.OUTGOING
    assert transaction.amount.amount == Decimal("13600.00")
    assert transaction.counterparty == "0090860169"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "5261"


def test_transfer_accepts_an_unmasked_source_account(
    parser: BancolombiaParser,
) -> None:
    transaction = parser.parse(TRANSFER)

    assert transaction is not None
    assert transaction.kind is TransactionKind.TRANSFER
    assert transaction.amount.amount == Decimal("112700.00")
    assert transaction.counterparty == "3017402695"
    assert transaction.instrument is not None
    # This alert writes "cuenta 5261" with no asterisk, unlike the QR one.
    assert transaction.instrument.last_four == "5261"


def test_incoming_payroll(parser: BancolombiaParser) -> None:
    transaction = parser.parse(INCOMING_PAYROLL)

    assert transaction is not None
    assert transaction.kind is TransactionKind.INCOMING_PAYMENT
    assert transaction.direction is TransactionDirection.INCOMING
    assert transaction.amount.amount == Decimal("19850806.00")
    assert transaction.counterparty == "BOLD.CO SAS"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.SAVINGS_ACCOUNT
    assert transaction.instrument.last_four is None


def test_unknown_template_returns_none(parser: BancolombiaParser) -> None:
    # The signal to fall back to the LLM. A partial match must never become a
    # half-filled transaction.
    assert parser.parse("Bancolombia: Tu clave fue actualizada el 20/08/2026.") is None


def test_unrelated_text_returns_none(parser: BancolombiaParser) -> None:
    assert parser.parse("Compraste algo en alguna parte") is None
