"""Parser tests built from real Bancolombia alerts.

The wording is verbatim — including the missing accents and the inconsistent
masking — because that is exactly what the parser has to survive. Names,
account digits and transfer keys are replaced with fakes of the same shape.
"""

import datetime as dt
from decimal import Decimal

import pytest

from personal_finance.contexts.ingestion.domain.parsing.bancolombia import (
    BancolombiaParser,
)
from personal_finance.contexts.ingestion.domain.transactions import (
    ExtractedTransaction,
    ExtractedTransfer,
    InstrumentKind,
    TransactionDirection,
    TransactionKind,
    TransferKind,
)
from personal_finance.shared.domain.value_objects import Currency, PosixTime


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
CARD_PAYMENT = (
    "Bancolombia: Pagaste $3,540,258 en la tarjeta de credito *7653 desde la "
    "cuenta *5261, el 21/05/2026 16:30. ¿Dudas? Llamanos al 018000912345. "
    "Estamos cerca."
)
CARD_PAYMENT_ACCENTED = (
    "Bancolombia: Pagaste $1.200.000,50 en la tarjeta de crédito 7653 desde "
    "la cuenta 5261 el 21/05/2026 a las 16:30. Estamos cerca."
)
PAYMENT_TO_ANOTHER_BANK = (
    "Bancolombia: Pagaste $3,625,733.00 a BANCO COMERCIAL AV VILLAS desde tu "
    "producto *5261 el 30/12/2025 11:17:03. ¿Dudas? Llamanos al 6045109095. "
    "Estamos cerca."
)
PAYMENT_TO_ANOTHER_BANK_UNMASKED = (
    "Bancolombia: Pagaste $504,179.72 a LULO BANK S A desde tu producto 5261 el "
    "21/09/2026 16:05:18. ¿Dudas? Llamanos al 6045109095. Estamos cerca"
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

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.CARD_PURCHASE
    assert transaction.direction is TransactionDirection.OUTGOING
    assert transaction.amount.amount == Decimal("29259.00")
    assert transaction.amount.currency is Currency.COP
    assert transaction.counterparty == "TIENDAS ARA"
    assert transaction.bank == "bancolombia"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.CREDIT_CARD
    assert transaction.instrument.last_four == "7653"


def test_card_purchase_keeps_a_multi_word_merchant_intact(
    parser: BancolombiaParser,
) -> None:
    transaction = parser.parse(CARD_PURCHASE_MULTIWORD_MERCHANT)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.counterparty == "TIENDA D1 VAL TULUA"
    assert transaction.amount.amount == Decimal("37680.00")


def test_card_purchase_time_is_bogota_local(parser: BancolombiaParser) -> None:
    transaction = parser.parse(CARD_PURCHASE)

    assert isinstance(transaction, ExtractedTransaction)
    # 12:00 in Bogotá is 17:00 UTC. Reading the alert as UTC would move every
    # evening purchase into the next day.
    assert transaction.occurred_at.to_datetime().isoformat() == (
        "2026-08-20T17:00:00+00:00"
    )


def test_qr_payment(parser: BancolombiaParser) -> None:
    transaction = parser.parse(QR_PAYMENT)

    assert isinstance(transaction, ExtractedTransaction)
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

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.TRANSFER
    assert transaction.amount.amount == Decimal("112700.00")
    assert transaction.counterparty == "3017402695"
    assert transaction.instrument is not None
    # This alert writes "cuenta 5261" with no asterisk, unlike the QR one.
    assert transaction.instrument.last_four == "5261"


def test_incoming_payroll(parser: BancolombiaParser) -> None:
    transaction = parser.parse(INCOMING_PAYROLL)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.INCOMING_PAYMENT
    assert transaction.direction is TransactionDirection.INCOMING
    assert transaction.amount.amount == Decimal("19850806.00")
    assert transaction.counterparty == "BOLD.CO SAS"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.SAVINGS_ACCOUNT
    assert transaction.instrument.last_four is None


@pytest.mark.parametrize(
    "alert",
    [
        "Bancolombia: Aprobamos tu compra por COP29.259,00 en TIENDAS ARA con "
        "tu T.Cred *7653, el 20/08/2026 a las 12:00.",
        "Bancolombia: Autorizamos una compra por COP150.000,00 en HOTEL "
        "ESTELAR con tu T.Cred *7653, el 20/08/2026 a las 12:00.",
        "Bancolombia: Tienes una compra en proceso por COP29.259,00 en "
        "TIENDAS ARA con tu T.Cred *7653, el 20/08/2026 a las 12:00.",
        "Bancolombia: Retencion por COP150.000,00 en HOTEL ESTELAR con tu "
        "T.Cred *7653, el 20/08/2026 a las 12:00.",
    ],
)
def test_an_authorization_is_not_a_purchase(
    parser: BancolombiaParser,
    alert: str,
) -> None:
    """A hold is money reserved, not money spent.

    The bank announces the real charge later in its own email, often for a
    different amount — a hotel holds more than it finally bills. Parsing both
    would put one expense on a card twice, and nothing downstream could tell
    afterwards which of the two was real.

    The templates only match a completed fact (`Compraste`, `Pagaste`,
    `Transferiste`, `Recibiste`), so this holds today by construction. The
    test is here so a future pattern cannot loosen that without saying so.
    """
    assert parser.parse(alert) is None


def test_unknown_template_returns_none(parser: BancolombiaParser) -> None:
    # The signal to fall back to the LLM. A partial match must never become a
    # half-filled transaction.
    assert parser.parse("Bancolombia: Tu clave fue actualizada el 20/08/2026.") is None


def test_unrelated_text_returns_none(parser: BancolombiaParser) -> None:
    assert parser.parse("Compraste algo en alguna parte") is None


# --------------------------------------------------------- pago de tarjeta


def test_a_card_payment_is_a_transfer_not_a_movement(
    parser: BancolombiaParser,
) -> None:
    """The alert that names two of the holder's own instruments.

    Read as one movement it is wrong whichever side it lands on, so the parser
    answers with a different type entirely and the caller cannot accidentally
    treat it as spending.
    """
    transfer = parser.parse(CARD_PAYMENT)

    assert isinstance(transfer, ExtractedTransfer)
    assert transfer.kind is TransferKind.CARD_PAYMENT
    assert transfer.amount.amount == Decimal("3540258")
    assert transfer.amount.currency is Currency.COP
    assert transfer.bank == "bancolombia"


def test_a_card_payment_reads_the_account_as_source_and_the_card_as_destination(
    parser: BancolombiaParser,
) -> None:
    """The direction of the whole thing. Swapped, a payment would empty the
    account *and* raise the card's debt."""
    transfer = parser.parse(CARD_PAYMENT)

    assert isinstance(transfer, ExtractedTransfer)
    assert transfer.source.kind is InstrumentKind.ACCOUNT
    assert transfer.source.last_four == "5261"
    assert transfer.destination.kind is InstrumentKind.CREDIT_CARD
    assert transfer.destination.last_four == "7653"


def test_a_card_payment_time_is_bogota_local(parser: BancolombiaParser) -> None:
    transfer = parser.parse(CARD_PAYMENT)

    assert isinstance(transfer, ExtractedTransfer)
    # 16:30 in Bogotá is 21:30 UTC.
    assert transfer.occurred_at.as_epoch_seconds() == 1_779_399_000


def test_a_card_payment_survives_the_accent_and_the_missing_asterisks(
    parser: BancolombiaParser,
) -> None:
    """Same template, the way the bank writes it on another day: `crédito`
    with its accent, no masking marker, `a las` back in the date, and cents."""
    transfer = parser.parse(CARD_PAYMENT_ACCENTED)

    assert isinstance(transfer, ExtractedTransfer)
    assert transfer.amount.amount == Decimal("1200000.50")
    assert transfer.source.last_four == "5261"
    assert transfer.destination.last_four == "7653"


def test_a_card_payment_is_never_read_as_one_of_the_single_sided_templates(
    parser: BancolombiaParser,
) -> None:
    """The regression this whole path exists for: before the template, this
    alert fell through to the fallback, which reports one movement."""
    assert not isinstance(parser.parse(CARD_PAYMENT), ExtractedTransaction)


def test_a_transfer_to_somebody_else_stays_a_single_movement(
    parser: BancolombiaParser,
) -> None:
    """Money leaving for an account the bank does not say is yours is one
    expense, and must not be turned into a pair."""
    assert isinstance(parser.parse(TRANSFER), ExtractedTransaction)


# ------------------------------------------------- pago a otra entidad


def test_a_payment_to_another_bank_is_one_outgoing_movement(
    parser: BancolombiaParser,
) -> None:
    """One instrument and an institution: a single movement. Only the owner
    knows the institution holds their card, so declaring it a transfer is
    theirs to do afterwards — the parser must not guess it into a pair."""
    transaction = parser.parse(PAYMENT_TO_ANOTHER_BANK)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.kind is TransactionKind.TRANSFER
    assert transaction.direction is TransactionDirection.OUTGOING
    assert transaction.amount.amount == Decimal("3625733.00")
    assert transaction.amount.currency is Currency.COP
    assert transaction.counterparty == "BANCO COMERCIAL AV VILLAS"
    assert transaction.bank == "bancolombia"
    assert transaction.instrument is not None
    assert transaction.instrument.kind is InstrumentKind.ACCOUNT
    assert transaction.instrument.last_four == "5261"


def test_a_payment_to_another_bank_reads_exactly_what_the_fallback_read(
    parser: BancolombiaParser,
) -> None:
    """Before this template the model read these alerts, and a movement's
    identity is its content. Recorded from the fallback on 2026-09-26 for
    this very alert: account *5261, "BANCO COMERCIAL AV VILLAS", 11:17 in
    Bogotá. Any field read differently here — the seconds kept, the
    instrument called a savings account — would give a movement already
    recorded a second identity, and a redelivery would count it twice."""
    transaction = parser.parse(PAYMENT_TO_ANOTHER_BANK)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.occurred_at == PosixTime.from_datetime(
        dt.datetime(2025, 12, 30, 16, 17, tzinfo=dt.UTC),
    )


def test_a_payment_to_another_bank_survives_the_missing_asterisk(
    parser: BancolombiaParser,
) -> None:
    transaction = parser.parse(PAYMENT_TO_ANOTHER_BANK_UNMASKED)

    assert isinstance(transaction, ExtractedTransaction)
    assert transaction.amount.amount == Decimal("504179.72")
    assert transaction.counterparty == "LULO BANK S A"
    assert transaction.instrument is not None
    assert transaction.instrument.last_four == "5261"


def test_a_card_payment_at_this_bank_still_wins_over_a_payment_to_another(
    parser: BancolombiaParser,
) -> None:
    """Both open with "Pagaste". The one naming a card of the owner's at this
    same bank is two movements and has to keep being read as the pair."""
    assert isinstance(parser.parse(CARD_PAYMENT), ExtractedTransfer)
    assert isinstance(parser.parse(CARD_PAYMENT_ACCENTED), ExtractedTransfer)
