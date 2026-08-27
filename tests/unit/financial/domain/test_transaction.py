from datetime import UTC, datetime
from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.events import (
    TransactionAssigned,
    TransactionRecorded,
)
from personal_finance.contexts.financial.domain.exceptions import (
    TransactionAlreadyAssignedError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountId,
    AccountKind,
    BalanceSign,
    InstrumentKind,
    MovementDirection,
    TransactionOrigin,
    TransactionStatus,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER = UserId.from_string("6f8f1f2e-6a5c-4d63-9c2e-9d3b1f7c5a10")
PURCHASE_TIME = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _transaction(**overrides: object) -> Transaction:
    parts: dict[str, object] = {
        "user_id": USER,
        "bank": "bancolombia",
        "direction": MovementDirection.OUTGOING,
        "amount": _cop("50000"),
        "occurred_at": PURCHASE_TIME,
        "counterparty": "TIENDAS ARA 123",
        "instrument_kind": "credit_card",
        "last_four": "7653",
    }
    parts.update(overrides)

    return Transaction.from_alert(**parts)  # type: ignore[arg-type]


def test_the_same_alert_read_twice_is_the_same_transaction() -> None:
    # Identity from content, so a redelivery writes the same ledger row.
    assert _transaction().id == _transaction().id
    assert _transaction().account_fingerprint == _transaction().account_fingerprint


def test_two_different_movements_are_two_transactions() -> None:
    assert _transaction().id != _transaction(amount=_cop("50001")).id


def test_a_movement_starts_unassigned_until_an_account_takes_it() -> None:
    transaction = _transaction()

    assert transaction.status is TransactionStatus.UNASSIGNED
    assert transaction.needs_assignment
    assert transaction.account_id is None


def test_an_alert_naming_no_instrument_is_kept_and_waits_for_a_person() -> None:
    transaction = _transaction(instrument_kind=None, last_four=None)

    assert not transaction.is_routable
    assert transaction.account_fingerprint is None
    # Still a real movement: the money moved, and refusing the record loses it.
    assert transaction.amount == _cop("50000")


def test_a_card_without_its_digits_cannot_pick_an_account() -> None:
    # Matching on bank and kind alone would merge every card the user holds
    # at that bank into one wrong balance.
    assert not _transaction(last_four=None).is_routable


def test_an_instrument_nobody_declared_still_gets_a_key_to_wait_under() -> None:
    # An instrument Financial has no opinion about is still a key an account
    # could be declared for later. What decides routing is whether somebody
    # declared an account answering to it, not whether the name is familiar.
    transaction = _transaction(instrument_kind="prepaid_wallet")

    assert transaction.is_routable
    assert transaction.account_fingerprint is not None


def test_digits_no_account_key_can_use_cost_the_routing_not_the_movement() -> None:
    # Ingestion gates `last_four` on bare `str.isdigit`, so Arabic-Indic
    # numerals reach Financial intact.
    transaction = _transaction(last_four="٤٥٦٧")

    assert not transaction.is_routable
    assert transaction.counterparty == "TIENDAS ARA 123"


def test_a_readable_alert_knows_which_account_to_look_for() -> None:
    transaction = _transaction()

    assert transaction.is_routable
    assert transaction.origin is TransactionOrigin.BANK_ALERT


def test_the_account_a_movement_looks_for_is_the_one_a_sighting_opens() -> None:
    # The seam between the two aggregates. If these two ever spell the
    # fingerprint differently, every auto-opened account is invisible to the
    # movements that opened it, and every balance stays at zero.
    transaction = _transaction()
    account = Account.open(
        user_id=USER,
        name="Tarjeta de crédito",
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PURCHASE_TIME,
    )

    assert transaction.account_fingerprint is not None
    assert account.matches(transaction.account_fingerprint)


def test_spending_on_a_credit_card_raises_what_it_owes() -> None:
    transaction = _transaction()
    account = Account.open(
        user_id=USER,
        name="Tarjeta de crédito",
        bank="bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PURCHASE_TIME,
    )

    transaction.assign_to(account.id)
    account.apply(transaction.as_movement())

    assert transaction.status is TransactionStatus.ASSIGNED
    assert account.balance.amount == _cop("50000")
    assert account.balance.sign is BalanceSign.POSITIVE


def test_the_ledger_row_carries_the_movement_that_caused_it() -> None:
    transaction = _transaction()
    movement = transaction.as_movement()

    assert movement.movement_id == transaction.id
    assert movement.amount == _cop("50000")
    assert movement.direction is MovementDirection.OUTGOING
    assert movement.occurred_at == PURCHASE_TIME


def test_assigning_the_same_movement_twice_changes_nothing() -> None:
    # A redelivery must not record the assignment a second time.
    transaction = _transaction()
    account_id = AccountId.new()

    transaction.assign_to(account_id)
    transaction.pull_events()
    transaction.assign_to(account_id)

    assert transaction.account_id == account_id
    assert transaction.pull_events() == []


def test_a_movement_cannot_be_moved_to_another_account_behind_the_balance() -> None:
    transaction = _transaction()
    transaction.assign_to(AccountId.new())

    with pytest.raises(TransactionAlreadyAssignedError):
        transaction.assign_to(AccountId.new())


def test_recording_a_movement_announces_it_with_its_routing() -> None:
    transaction = _transaction()
    events = transaction.pull_events()

    assert len(events) == 1
    recorded = events[0]
    assert isinstance(recorded, TransactionRecorded)
    assert recorded.movement_id == transaction.id
    assert recorded.user_id == USER
    assert recorded.bank == "bancolombia"
    assert recorded.movement_occurred_at == PURCHASE_TIME
    assert recorded.account_fingerprint == transaction.account_fingerprint


def test_an_unassigned_movement_says_so_when_it_is_announced() -> None:
    # Nothing else explains why a movement sits outside every balance.
    transaction = _transaction(instrument_kind=None, last_four=None)
    recorded = transaction.pull_events()[0]

    assert isinstance(recorded, TransactionRecorded)
    assert recorded.account_fingerprint is None


def test_assigning_announces_which_account_took_it() -> None:
    transaction = _transaction()
    transaction.pull_events()
    account_id = AccountId.new()

    transaction.assign_to(account_id)
    events = transaction.pull_events()

    assert len(events) == 1
    assigned = events[0]
    assert isinstance(assigned, TransactionAssigned)
    assert assigned.account_id == account_id
    assert assigned.movement_id == transaction.id


def test_the_bank_is_normalized_the_way_account_matching_expects() -> None:
    assert _transaction(bank="  Bancolombia ").bank == "bancolombia"
    assert _transaction(bank="  Bancolombia ").id == _transaction().id


def test_the_counterparty_is_kept_as_written_for_a_person_to_read() -> None:
    # Folding happens inside the fingerprint; what is displayed stays as the
    # bank wrote it.
    assert _transaction(counterparty="  Tiendas ARA 123 ").counterparty == (
        "Tiendas ARA 123"
    )


def test_an_alert_that_names_no_bank_is_refused() -> None:
    # Without it, two banks' cards sharing four digits merge into one account.
    with pytest.raises(ValueError):
        _transaction(bank="   ")
