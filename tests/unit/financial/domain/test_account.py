from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import (
    MAX_ACCOUNT_NAME_LENGTH,
    Account,
)
from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
)
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountKind,
    AccountStatus,
    Balance,
    LedgerMovement,
    MovementDirection,
    MovementId,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)
LATER = PosixTime.from_epoch_seconds(1_700_086_400)


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _movement(
    amount: str,
    direction: MovementDirection = MovementDirection.OUTGOING,
    *,
    movement_id: str = "movement-1",
) -> LedgerMovement:
    return LedgerMovement(
        movement_id=MovementId(value=movement_id),
        direction=direction,
        amount=_cop(amount),
        occurred_at=NOW,
    )


def _discovered(kind: AccountKind = AccountKind.SAVINGS) -> Account:
    account = Account.open_automatically(
        user_id=USER_ID,
        bank="bancolombia",
        instrument_kind="debit_card",
        last_four="7653",
        kind=kind,
        currency=Currency.COP,
        opened_at=NOW,
    )
    account.pull_events()

    return account


def test_a_discovered_account_is_named_after_the_bank_that_announced_it() -> None:
    account = Account.open_automatically(
        user_id=USER_ID,
        bank="Bancolombia",
        instrument_kind="credit_card",
        last_four="7653",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=NOW,
    )

    assert account.name == "Bancolombia ••7653"
    assert account.bank == "bancolombia"
    assert account.needs_review
    assert account.balance == Balance.zero(Currency.COP)
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountOpened",
        "AccountFingerprintLinked",
    ]


def test_a_discovered_account_answers_to_the_pair_that_created_it() -> None:
    account = _discovered()

    assert account.matches(
        AccountFingerprint.from_parts(
            bank="Bancolombia",
            instrument_kind="debit_card",
            last_four="7653",
        ),
    )


def test_one_account_can_answer_to_several_of_its_banks_names() -> None:
    account = _discovered()
    transfers = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind="savings_account",
        last_four="1234",
    )
    account.link_fingerprint(transfers)

    assert account.matches(transfers)
    assert len(account.fingerprints) == 2
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountFingerprintLinked",
    ]


def test_linking_a_pair_the_account_already_answers_to_changes_nothing() -> None:
    account = _discovered()
    account.link_fingerprint(next(iter(account.fingerprints)))

    assert len(account.fingerprints) == 1
    assert account.pull_events() == []


def test_spending_lowers_an_asset_and_income_raises_it() -> None:
    account = _discovered()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.apply(_movement("80000", MovementDirection.INCOMING))

    assert account.balance.signed_amount == Decimal("30000")
    assert account.movements_applied == 2


def test_spending_on_a_credit_card_raises_what_you_owe() -> None:
    card = _discovered(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))

    assert card.category is AccountCategory.LIABILITY
    assert card.balance.signed_amount == Decimal("1200000")
    assert not card.balance.is_negative


def test_paying_a_credit_card_lowers_what_you_owe() -> None:
    card = _discovered(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))
    card.apply(_movement("500000", MovementDirection.INCOMING))

    assert card.balance.signed_amount == Decimal("700000")


def test_an_account_discovered_mid_life_may_go_below_zero() -> None:
    # Its real opening balance is unknown: the running total is movement
    # since discovery, and inventing a starting number would be worse.
    account = _discovered()
    account.apply(_movement("50000", MovementDirection.OUTGOING))

    assert account.balance.is_negative
    assert account.balance.signed_amount == Decimal("-50000")


def test_a_balance_change_names_the_movement_behind_it() -> None:
    account = _discovered()
    account.apply(_movement("50000", movement_id="movement-42"))
    (event,) = account.pull_events()

    assert isinstance(event, AccountBalanceChanged)
    assert event.movement_id == MovementId(value="movement-42")
    assert event.balance == account.balance


def test_a_movement_in_another_currency_is_refused_not_converted() -> None:
    account = _discovered()

    with pytest.raises(CurrencyMismatchError):
        account.apply(
            LedgerMovement(
                movement_id=MovementId(value="movement-usd"),
                direction=MovementDirection.OUTGOING,
                amount=Money(amount=Decimal("20"), currency=Currency.USD),
                occurred_at=NOW,
            ),
        )

    assert account.balance == Balance.zero(Currency.COP)
    assert account.movements_applied == 0


def test_a_declared_mortgage_starts_at_what_is_owed() -> None:
    mortgage = Account.open_manually(
        user_id=USER_ID,
        name="Apartment mortgage",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=NOW,
        opening_balance=_cop("180000000"),
    )

    assert mortgage.status is AccountStatus.CONFIRMED
    assert not mortgage.needs_review
    assert mortgage.opening_balance.signed_amount == Decimal("180000000")
    assert mortgage.balance == mortgage.opening_balance
    assert [type(event).__name__ for event in mortgage.pull_events()] == [
        "AccountOpened",
    ]


def test_a_declared_account_cannot_open_with_another_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        Account.open_manually(
            user_id=USER_ID,
            name="Savings",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            opened_at=NOW,
            opening_balance=Money(amount=Decimal("100"), currency=Currency.USD),
        )


def test_an_account_needs_a_name() -> None:
    with pytest.raises(ValueError):
        Account.open_manually(
            user_id=USER_ID,
            name="   ",
            kind=AccountKind.CASH,
            currency=Currency.COP,
            opened_at=NOW,
        )


def test_naming_an_account_counts_as_reviewing_it() -> None:
    account = _discovered()
    account.rename("  Daily savings  ")

    assert account.name == "Daily savings"
    assert not account.needs_review
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountRenamed",
    ]


def test_an_account_can_be_accepted_with_the_name_it_was_given() -> None:
    account = _discovered()
    account.confirm()

    assert account.status is AccountStatus.CONFIRMED
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountConfirmed",
    ]


def test_a_closed_account_takes_no_further_movements() -> None:
    account = _discovered()
    account.close(LATER)

    assert account.is_closed
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountClosed"
    ]

    with pytest.raises(AccountClosedError):
        account.apply(_movement("50000"))


def test_closing_an_already_closed_account_announces_nothing() -> None:
    account = _discovered()
    account.close(NOW)
    account.pull_events()
    account.close(LATER)

    assert account.closed_at == NOW
    assert account.pull_events() == []


def test_replaying_the_ledger_reproduces_the_running_total() -> None:
    account = _discovered()
    movements = [
        _movement("50000", MovementDirection.OUTGOING, movement_id="a"),
        _movement("80000", MovementDirection.INCOMING, movement_id="b"),
        _movement("12500", MovementDirection.OUTGOING, movement_id="c"),
    ]

    for movement in movements:
        account.apply(movement)

    incremental = account.balance
    account.pull_events()
    account.rebuild(movements)

    assert account.balance == incremental
    assert account.movements_applied == 3
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountBalanceRebuilt",
    ]


def test_replaying_the_ledger_repairs_a_total_that_drifted() -> None:
    account = _discovered()
    truth = [_movement("50000", MovementDirection.OUTGOING, movement_id="a")]
    # What a redelivered movement would have done before the ledger caught it.
    account.apply(truth[0])
    account.apply(truth[0])
    account.pull_events()

    account.rebuild(truth)

    assert account.balance.signed_amount == Decimal("-50000")
    assert account.movements_applied == 1


def test_a_declared_opening_balance_survives_a_replay() -> None:
    mortgage = Account.open_manually(
        user_id=USER_ID,
        name="Apartment mortgage",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=NOW,
        opening_balance=_cop("180000000"),
    )
    mortgage.pull_events()
    mortgage.rebuild([_movement("2000000", MovementDirection.INCOMING)])

    assert mortgage.balance.signed_amount == Decimal("178000000")


def test_a_replay_that_cannot_finish_leaves_the_balance_untouched() -> None:
    account = _discovered()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.pull_events()

    with pytest.raises(CurrencyMismatchError):
        account.rebuild(
            [
                _movement("50000", MovementDirection.OUTGOING),
                LedgerMovement(
                    movement_id=MovementId(value="movement-usd"),
                    direction=MovementDirection.OUTGOING,
                    amount=Money(amount=Decimal("20"), currency=Currency.USD),
                    occurred_at=NOW,
                ),
            ],
        )

    assert account.balance.signed_amount == Decimal("-50000")
    assert account.movements_applied == 1
    assert account.pull_events() == []


def test_a_closed_account_can_still_be_repaired() -> None:
    account = _discovered()
    account.close(LATER)
    account.pull_events()
    account.rebuild([_movement("50000", MovementDirection.OUTGOING)])

    assert account.balance.signed_amount == Decimal("-50000")


def test_a_long_bank_name_still_produces_a_usable_placeholder() -> None:
    account = Account.open_automatically(
        user_id=USER_ID,
        bank="b" * 200,
        instrument_kind="credit_card",
        last_four="7653",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=NOW,
    )

    assert len(account.name) <= MAX_ACCOUNT_NAME_LENGTH
    assert account.name.endswith("••7653")
