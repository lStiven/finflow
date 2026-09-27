"""A movement its owner says, afterwards, was a transfer.

The alert for paying a card at another bank names one account and an
institution, so it is recorded — correctly, as far as the alert goes — as
spending. These tests are about the three ways declaring it can go wrong: a
balance moved that should not move, a side counted twice, and a declaration
that cannot be taken back.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.events import (
    TransactionReclassified,
    TransactionRecorded,
)
from personal_finance.contexts.financial.domain.exceptions import (
    TransferDeclarationError,
)
from personal_finance.contexts.financial.domain.transfers import (
    could_be_other_side,
    names_institution,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    DeclarationRefusal,
    InstrumentKind,
    MovementDirection,
    MovementId,
    TransactionOrigin,
    TransferBasis,
    TransferId,
    TransferLeg,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
SAVINGS = AccountId.from_string("0b7f9a52-3d51-4c4e-8f55-0a0b5a8d2c11")
CARD = AccountId.from_string("7d3c2b1a-9e8f-4a6b-b5c4-d3e2f1a0b9c8")
PAID_AT = PosixTime.from_datetime(datetime(2025, 12, 30, 16, 17, tzinfo=UTC))
DAY = 24 * 60 * 60


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _alert(**overrides: object) -> Transaction:
    """The Bancolombia alert, as the template reads it."""
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "direction": MovementDirection.OUTGOING,
        "amount": _cop("3625733.00"),
        "occurred_at": PAID_AT,
        "counterparty": "BANCO COMERCIAL AV VILLAS",
        "instrument_kind": "account",
        "last_four": "5261",
    }
    parts.update(overrides)
    movement = Transaction.from_alert(**parts)  # pyright: ignore[reportArgumentType]
    movement.pull_events()

    return movement


def _on(account_id: AccountId, movement: Transaction) -> Transaction:
    movement.assign_to(account_id)
    movement.pull_events()

    return movement


# ------------------------------------------------------------------ lone side


def test_a_declared_outgoing_movement_is_a_source_with_no_other_side() -> None:
    movement = _on(SAVINGS, _alert())

    movement.declare_transfer()

    assert movement.transfer is not None
    assert movement.transfer.role is TransferRole.SOURCE
    assert movement.transfer.basis is TransferBasis.RECLASSIFIED
    assert movement.transfer.counterpart_is_external is True
    assert movement.is_transfer is True


def test_a_declared_incoming_movement_is_a_destination() -> None:
    movement = _on(CARD, _alert(direction=MovementDirection.INCOMING))

    movement.declare_transfer()

    assert movement.transfer is not None
    assert movement.transfer.role is TransferRole.DESTINATION


def test_declaring_keeps_the_identity_the_alert_gave_it() -> None:
    """The identity is what refuses a redelivered alert. Declaring must not
    move it, or the same email would come back as a second expense."""
    movement = _on(SAVINGS, _alert())
    before = movement.id

    movement.declare_transfer()

    assert movement.id == before
    assert movement.id == _alert().id


def test_declaring_announces_a_reclassification_and_nothing_else() -> None:
    """No `TransactionRecorded`: no new money moved, and a subscriber reading
    one would tell the owner about a payment a second time."""
    movement = _on(SAVINGS, _alert())

    movement.declare_transfer()
    events = movement.pull_events()

    assert [type(event) for event in events] == [TransactionReclassified]
    assert isinstance(events[0], TransactionReclassified)
    assert events[0].transfer is True


def test_a_transfer_already_is_refused() -> None:
    movement = _on(SAVINGS, _alert())
    movement.declare_transfer()

    with pytest.raises(TransferDeclarationError, match="already"):
        movement.declare_transfer()


def test_a_side_of_an_alert_pair_is_refused() -> None:
    source, _ = Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("100000"),
        occurred_at=PAID_AT,
        source_instrument_kind="account",
        source_last_four="5261",
        destination_instrument_kind="credit_card",
        destination_last_four="7653",
    )

    assert source.declaration_refusal is DeclarationRefusal.ALREADY_TRANSFER


@pytest.mark.parametrize(
    "origin",
    [TransactionOrigin.ACCRUAL, TransactionOrigin.SCHEDULED],
)
def test_a_row_this_app_wrote_is_refused(origin: TransactionOrigin) -> None:
    movement = Transaction(
        id=MovementId.new(),
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=_cop("120000"),
        occurred_at=PAID_AT,
        counterparty="Gimnasio",
        bank="",
        origin=origin,
        account_id=SAVINGS,
    )

    assert movement.declaration_refusal is DeclarationRefusal.SELF_WRITTEN

    with pytest.raises(TransferDeclarationError):
        movement.declare_transfer()


def test_a_manual_movement_on_no_account_is_refused() -> None:
    """The state `detach` refuses: a transfer on no balance, naming no
    instrument, that nothing would ever adopt."""
    movement = Transaction.enter_manually(
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=_cop("50000"),
        occurred_at=PAID_AT,
        counterparty="Efectivo",
    )

    assert movement.declaration_refusal is DeclarationRefusal.UNPLACEABLE


def test_an_unassigned_alert_can_still_be_declared() -> None:
    """Its account may not be declared yet; when it is, it adopts the row the
    way it adopts any alert — as a transfer."""
    movement = _alert()

    assert movement.declaration_refusal is None


# -------------------------------------------------------- the side written


def test_the_written_side_mirrors_the_declared_one() -> None:
    movement = _on(SAVINGS, _alert())

    written = movement.declare_counterpart(
        account_id=CARD,
        counterparty="Ahorros Bancolombia",
        bank="AV Villas",
    )

    assert written.direction is MovementDirection.INCOMING
    assert written.amount == movement.amount
    assert written.occurred_at == movement.occurred_at
    assert written.account_id == CARD
    assert written.bank == "av villas"
    assert written.origin is TransactionOrigin.MANUAL


def test_the_two_sides_name_each_other_under_one_transfer() -> None:
    movement = _on(SAVINGS, _alert())

    written = movement.declare_counterpart(account_id=CARD, counterparty="Ahorros")

    assert movement.transfer is not None
    assert written.transfer is not None
    assert movement.transfer.transfer_id == written.transfer.transfer_id
    assert movement.transfer.counterpart_id == written.id
    assert written.transfer.counterpart_id == movement.id
    assert movement.transfer.role is TransferRole.SOURCE
    assert written.transfer.role is TransferRole.DESTINATION
    assert movement.transfer.basis is TransferBasis.RECLASSIFIED
    assert written.transfer.basis is TransferBasis.COUNTERPART


def test_the_written_side_has_one_possible_identity() -> None:
    """Two presses, two tabs, or two accounts named: one key, so the ledger's
    conditional write keeps one row and the debt falls once."""
    first = _on(SAVINGS, _alert()).declare_counterpart(
        account_id=CARD,
        counterparty="Ahorros",
    )
    second = _on(SAVINGS, _alert()).declare_counterpart(
        account_id=AccountId.new(),
        counterparty="Otra",
    )

    assert first.id == second.id


def test_the_written_side_is_announced_as_money_that_moved() -> None:
    movement = _on(SAVINGS, _alert())

    written = movement.declare_counterpart(account_id=CARD, counterparty="Ahorros")

    assert [type(event) for event in written.pull_events()] == [TransactionRecorded]


def test_the_other_side_on_the_same_account_is_refused() -> None:
    movement = _on(SAVINGS, _alert())

    with pytest.raises(TransferDeclarationError, match="different account"):
        movement.declare_counterpart(account_id=SAVINGS, counterparty="Ahorros")

    assert movement.transfer is None


def test_on_a_card_the_written_side_is_the_debt_falling() -> None:
    """The point of it all. The savings already fell when the alert landed;
    this is the other half, and with it net worth is back where it was."""
    savings = _account(AccountKind.SAVINGS, holds="5000000")
    card = _account(AccountKind.CREDIT_CARD, holds="3625733")
    movement = _alert()
    savings.apply(movement.as_movement())
    worth_before_paying = Decimal("5000000") - Decimal("3625733")

    written = movement.declare_counterpart(account_id=card.id, counterparty="Ahorros")
    card.apply(written.as_movement())

    assert card.balance.signed_amount == Decimal("0.00")
    assert savings.balance.signed_amount - card.balance.signed_amount == (
        worth_before_paying
    )


def _account(kind: AccountKind, holds: str) -> Account:
    account = Account.open(
        user_id=USER,
        name=kind.value,
        kind=kind,
        currency=Currency.COP,
        bank="bancolombia",
        instrument_kind=InstrumentKind.ACCOUNT,
        last_four="5261" if kind is AccountKind.SAVINGS else "0001",
        opening_balance=_cop(holds),
        opened_at=PAID_AT,
    )
    account.pull_events()

    return account


# ------------------------------------------------------------ pairing two


def _arrival(**overrides: object) -> Transaction:
    """Lulo announcing the same money arriving."""
    parts: dict[str, object] = {
        "bank": "lulo bank",
        "direction": MovementDirection.INCOMING,
        "counterparty": "NOMBRE APELLIDO",
        "last_four": "0042",
    }
    parts.update(overrides)

    return _alert(**parts)


def test_pairing_ties_both_movements_under_one_transfer() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(CARD, _arrival())

    sent.pair_with(arrived)

    assert sent.transfer is not None
    assert arrived.transfer is not None
    assert sent.transfer.transfer_id == arrived.transfer.transfer_id
    assert sent.transfer.counterpart_id == arrived.id
    assert arrived.transfer.counterpart_id == sent.id
    assert sent.transfer.role is TransferRole.SOURCE
    assert arrived.transfer.role is TransferRole.DESTINATION


def test_pairing_is_the_same_transfer_whichever_side_asks() -> None:
    sent, arrived = _on(SAVINGS, _alert()), _on(CARD, _arrival())
    sent_again, arrived_again = _on(SAVINGS, _alert()), _on(CARD, _arrival())

    sent.pair_with(arrived)
    arrived_again.pair_with(sent_again)

    assert sent.transfer is not None
    assert sent_again.transfer is not None
    assert sent.transfer.transfer_id == sent_again.transfer.transfer_id


def test_pairing_two_movements_going_the_same_way_is_refused() -> None:
    sent = _on(SAVINGS, _alert())
    also_sent = _on(CARD, _arrival(direction=MovementDirection.OUTGOING))

    with pytest.raises(TransferDeclarationError, match="same way"):
        sent.pair_with(also_sent)


def test_pairing_different_amounts_is_refused_and_changes_neither() -> None:
    """A fee between them is real spending; pairing would make it vanish."""
    sent = _on(SAVINGS, _alert())
    arrived = _on(CARD, _arrival(amount=_cop("3620000")))

    with pytest.raises(TransferDeclarationError, match="different amounts"):
        sent.pair_with(arrived)

    assert sent.transfer is None
    assert arrived.transfer is None


def test_pairing_two_movements_on_one_account_is_refused() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(SAVINGS, _arrival())

    with pytest.raises(TransferDeclarationError, match="same account"):
        sent.pair_with(arrived)


def test_pairing_with_a_transfer_already_is_refused_and_changes_neither() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(CARD, _arrival())
    arrived.declare_transfer()

    with pytest.raises(TransferDeclarationError, match="already"):
        sent.pair_with(arrived)

    assert sent.transfer is None


def test_a_movement_cannot_be_its_own_other_side() -> None:
    sent = _on(SAVINGS, _alert())

    with pytest.raises(TransferDeclarationError):
        sent.pair_with(sent)


# ------------------------------------------------------------------- undo


def test_undoing_puts_the_movement_back_to_what_it_was() -> None:
    movement = _on(SAVINGS, _alert())
    movement.declare_transfer()
    movement.pull_events()

    movement.undo_declaration()

    assert movement.transfer is None
    assert movement.is_transfer is False
    assert [type(event) for event in movement.pull_events()] == [
        TransactionReclassified,
    ]


def test_a_transfer_the_bank_stated_cannot_be_undone() -> None:
    source, _ = Transaction.as_transfer(
        user_id=USER,
        bank="bancolombia",
        amount=_cop("100000"),
        occurred_at=PAID_AT,
        source_instrument_kind="account",
        source_last_four="5261",
        destination_instrument_kind="credit_card",
        destination_last_four="7653",
    )

    with pytest.raises(TransferDeclarationError, match="declared"):
        source.undo_declaration()


def test_the_written_side_is_erased_not_put_back() -> None:
    """Nothing but the declaration ever said it happened: restored, it would
    be an income of three and a half million on a credit card."""
    movement = _on(SAVINGS, _alert())
    written = movement.declare_counterpart(account_id=CARD, counterparty="Ahorros")

    with pytest.raises(TransferDeclarationError):
        written.undo_declaration()


def test_an_ordinary_movement_has_nothing_to_undo() -> None:
    with pytest.raises(TransferDeclarationError, match="not a transfer"):
        _on(SAVINGS, _alert()).undo_declaration()


# ----------------------------------------------------- the shape of the leg


def test_a_declared_leg_naming_an_instrument_is_refused() -> None:
    with pytest.raises(ValueError, match="by movement"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.SOURCE,
            counterpart_id=MovementId.new(),
            counterpart_instrument_kind="credit_card",
            counterpart_last_four="7653",
            basis=TransferBasis.RECLASSIFIED,
        )


def test_a_written_side_answering_no_movement_is_refused() -> None:
    with pytest.raises(ValueError, match="names the movement"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.DESTINATION,
            basis=TransferBasis.COUNTERPART,
        )


def test_a_stated_leg_naming_only_a_movement_is_still_refused() -> None:
    """What a declared leg may be, a stated one still may not: the old rule
    holds for every transfer that existed before this one."""
    with pytest.raises(ValueError, match="fully or not at all"):
        TransferLeg(
            transfer_id=TransferId(value="a-transfer"),
            role=TransferRole.SOURCE,
            counterpart_id=MovementId.new(),
        )


# ------------------------------------------------------- what gets proposed


@pytest.mark.parametrize(
    ("counterparty", "bank"),
    [
        ("BANCO COMERCIAL AV VILLAS", "AV Villas"),
        ("BANCO COMERCIAL AV VILLAS", "avvillas"),
        ("BANCO COMERCIAL AV VILLAS", "Banco AV Villas"),
        ("LULO BANK S A", "Lulo Bank"),
        ("LULO BANK S A", "lulo bank"),
        ("BANCO DE BOGOTA", "Banco de Bogotá"),
        ("NU COLOMBIA", "Nu"),
    ],
)
def test_the_bank_an_alert_names_is_recognised(counterparty: str, bank: str) -> None:
    assert names_institution(counterparty, bank) is True


@pytest.mark.parametrize(
    ("counterparty", "bank"),
    [
        # A substring is not a name: "nu" is inside "NUEVA".
        ("NUEVA EPS", "Nu"),
        ("BANCO COMERCIAL AV VILLAS", "Bancolombia"),
        # A bank declared only as "Banco" would match every institution.
        ("BANCO COMERCIAL AV VILLAS", "Banco"),
        ("BANCO COMERCIAL AV VILLAS", None),
        ("BANCO COMERCIAL AV VILLAS", ""),
    ],
)
def test_a_bank_the_alert_does_not_name_is_not(
    counterparty: str,
    bank: str | None,
) -> None:
    assert names_institution(counterparty, bank) is False


def test_the_same_money_arriving_a_minute_later_could_be_the_other_side() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(
        CARD,
        _arrival(
            occurred_at=PosixTime.from_epoch_seconds(PAID_AT.as_epoch_seconds() + 60)
        ),
    )

    assert could_be_other_side(sent, arrived) is True


def test_the_same_money_arriving_over_a_weekend_could_still_be_it() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(
        CARD,
        _arrival(
            occurred_at=PosixTime.from_epoch_seconds(
                PAID_AT.as_epoch_seconds() + 3 * DAY
            ),
        ),
    )

    assert could_be_other_side(sent, arrived) is True


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "occurred_at": PosixTime.from_epoch_seconds(
                PAID_AT.as_epoch_seconds() + 5 * DAY
            )
        },
        {"amount": _cop("3625733.01")},
        {"direction": MovementDirection.OUTGOING},
        {"amount": Money(amount=Decimal("3625733.00"), currency=Currency.USD)},
    ],
)
def test_what_differs_in_time_amount_direction_or_currency_is_not_proposed(
    overrides: dict[str, object],
) -> None:
    sent = _on(SAVINGS, _alert())

    assert could_be_other_side(sent, _on(CARD, _arrival(**overrides))) is False


def test_a_movement_on_the_same_account_is_not_proposed() -> None:
    sent = _on(SAVINGS, _alert())

    assert could_be_other_side(sent, _on(SAVINGS, _arrival())) is False


def test_a_movement_already_a_transfer_is_not_proposed() -> None:
    sent = _on(SAVINGS, _alert())
    arrived = _on(CARD, _arrival())
    arrived.declare_transfer()

    assert could_be_other_side(sent, arrived) is False
