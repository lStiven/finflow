"""How money moved between the owner's own balances is announced.

A transfer is two rows and one movement of money. Announced row by row, its
owner heard about a purchase and an income that never happened — «Gasto»
on the card they paid and «Ingreso» on the savings it came out of. So one
side speaks for both, naming where the money left and where it arrived, and
the other side is quiet.
"""

from datetime import UTC, datetime
from decimal import Decimal

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.events import TransactionRecorded
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    MovementRoute,
    RouteEnd,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
PAID_AT = PosixTime.from_datetime(datetime(2026, 11, 1, 22, 8, tzinfo=UTC))
CARD = AccountId.from_string("7d3c2b1a-9e8f-4a6b-b5c4-d3e2f1a0b9c8")
SAVINGS = AccountId.from_string("0b7f9a52-3d51-4c4e-8f55-0a0b5a8d2c11")


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _account(name: str, kind: AccountKind, last_four: str) -> Account:
    account = Account.open(
        user_id=USER,
        name=name,
        kind=kind,
        currency=Currency.COP,
        bank="bancolombia",
        instrument_kind=(
            InstrumentKind.CREDIT_CARD
            if kind is AccountKind.CREDIT_CARD
            else InstrumentKind.ACCOUNT
        ),
        last_four=last_four,
        opening_balance=_cop("5000000"),
        opened_at=PAID_AT,
    )
    account.pull_events()

    return account


def _announcement(movement: Transaction) -> TransactionRecorded:
    [event] = [
        event
        for event in movement.pull_events()
        if isinstance(event, TransactionRecorded)
    ]

    return event


# ------------------------------------------------ a pair read from one alert


def test_an_alert_derived_pair_speaks_once_from_where_the_money_left() -> None:
    source, destination = Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("2926752"),
        occurred_at=PAID_AT,
        source_instrument_kind="account",
        source_last_four="5261",
        destination_instrument_kind="credit_card",
        destination_last_four="7653",
    )

    speaking = _announcement(source)
    silent = _announcement(destination)

    assert (speaking.transfer, speaking.quiet) == (True, False)
    assert (silent.transfer, silent.quiet) == (True, True)
    assert speaking.route == MovementRoute(
        source=RouteEnd(instrument_kind="account", last_four="5261"),
        destination=RouteEnd(instrument_kind="credit_card", last_four="7653"),
    )


def test_naming_the_ends_keeps_what_was_not_named() -> None:
    source, _ = Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("2926752"),
        occurred_at=PAID_AT,
        source_instrument_kind="account",
        source_last_four="5261",
        destination_instrument_kind="credit_card",
        destination_last_four="7653",
    )

    # The card is declared, the savings account is not.
    source.name_route(source=None, destination="Visa Platinum")

    route = _announcement(source).route
    assert route is not None
    assert route.source == RouteEnd(instrument_kind="account", last_four="5261")
    assert route.destination == RouteEnd(
        name="Visa Platinum",
        instrument_kind="credit_card",
        last_four="7653",
    )


def test_an_alert_that_printed_the_whole_number_travels_with_four_digits() -> None:
    """The fingerprint keeps the trailing four; so must what is announced, or
    the full number rides the bus and sits in an inbox for a month."""
    source, _ = Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("2926752"),
        occurred_at=PAID_AT,
        source_instrument_kind="account",
        source_last_four="00012345675261",
        destination_instrument_kind="credit_card",
        destination_last_four="7653",
    )

    route = _announcement(source).route
    assert route is not None
    assert route.source.last_four == "5261"


# --------------------------------------------- a pair entered by its owner


def test_a_card_paid_from_savings_is_one_announcement_naming_both() -> None:
    savings = _account("Cuenta de ahorros", AccountKind.SAVINGS, "5261")
    card = _account("Visa Platinum", AccountKind.CREDIT_CARD, "7653")

    leaving, arriving = Transaction.enter_transfer_between(
        user_id=USER,
        source=savings,
        destination=card,
        amount=_cop("2926752"),
        occurred_at=PAID_AT,
    )

    speaking = _announcement(leaving)
    assert speaking.quiet is False
    assert speaking.direction is MovementDirection.OUTGOING
    assert speaking.route == MovementRoute(
        source=RouteEnd(name="Cuenta de ahorros"),
        destination=RouteEnd(name="Visa Platinum"),
    )
    assert _announcement(arriving).quiet is True


def test_a_cuota_speaks_as_spending_and_says_where_it_went() -> None:
    savings = _account("Cuenta de ahorros", AccountKind.SAVINGS, "5261")
    mortgage = Account.open(
        user_id=USER,
        name="Hipoteca",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opening_balance=_cop("60000000"),
        opened_at=PAID_AT,
    )

    leaving, arriving = Transaction.enter_transfer_between(
        user_id=USER,
        source=savings,
        destination=mortgage,
        amount=_cop("2000000"),
        occurred_at=PAID_AT,
    )

    speaking = _announcement(leaving)
    # Spending, not a transfer: a watched credit is counted where it leaves.
    assert (speaking.transfer, speaking.quiet) == (False, False)
    assert speaking.route is not None
    assert speaking.route.destination == RouteEnd(name="Hipoteca")
    assert _announcement(arriving).quiet is True


# ---------------------------------------------- a side whose other end is out


def test_a_payment_into_a_card_from_elsewhere_names_where_it_came_from() -> None:
    leg = Transaction.enter_transfer_leg(
        user_id=USER,
        role=TransferRole.DESTINATION,
        amount=_cop("500000"),
        occurred_at=PAID_AT,
        counterparty="Nequi",
        account_id=CARD,
        account_name="Visa Platinum",
    )

    announced = _announcement(leg)
    assert (announced.transfer, announced.quiet) == (True, False)
    assert announced.route == MovementRoute(
        source=RouteEnd(name="Nequi"),
        destination=RouteEnd(name="Visa Platinum"),
    )


def test_money_sent_out_to_elsewhere_leaves_from_the_owners_account() -> None:
    leg = Transaction.enter_transfer_leg(
        user_id=USER,
        role=TransferRole.SOURCE,
        amount=_cop("500000"),
        occurred_at=PAID_AT,
        counterparty="Davivienda",
        account_id=SAVINGS,
        account_name="Cuenta de ahorros",
    )

    assert _announcement(leg).route == MovementRoute(
        source=RouteEnd(name="Cuenta de ahorros"),
        destination=RouteEnd(name="Davivienda"),
    )


# ------------------------------------------------- declared after the fact


def test_the_side_written_by_a_declaration_is_quiet() -> None:
    """The movement was announced when its alert arrived; what the owner
    writes on screen to correct it is not news to them."""
    movement = Transaction.from_alert(
        user_id=USER,
        bank="bancolombia",
        direction=MovementDirection.OUTGOING,
        amount=_cop("3625733"),
        occurred_at=PAID_AT,
        counterparty="BANCO COMERCIAL AV VILLAS",
        instrument_kind="account",
        last_four="5261",
    )
    movement.assign_to(SAVINGS)
    movement.pull_events()

    written = movement.declare_counterpart(account_id=CARD, counterparty="Ahorros")

    assert _announcement(written).quiet is True


def test_an_ordinary_alert_is_neither_a_transfer_nor_quiet() -> None:
    movement = Transaction.from_alert(
        user_id=USER,
        bank="bancolombia",
        direction=MovementDirection.OUTGOING,
        amount=_cop("45000"),
        occurred_at=PAID_AT,
        counterparty="EXITO CALI",
        instrument_kind="credit_card",
        last_four="7653",
    )

    announced = _announcement(movement)
    assert (announced.transfer, announced.quiet, announced.route) == (
        False,
        False,
        None,
    )
