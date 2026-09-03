from decimal import Decimal

import pytest

from personal_finance.contexts.financial.domain.entities import (
    MAX_ACCOUNT_NAME_LENGTH,
    Account,
)
from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    AccountBalanceReversed,
)
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountKind,
    Balance,
    InstrumentKind,
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


def _declared(kind: AccountKind = AccountKind.SAVINGS) -> Account:
    account = Account.open(
        user_id=USER_ID,
        name="Cuenta de ahorros",
        bank="bancolombia",
        instrument_kind=InstrumentKind.DEBIT_CARD,
        last_four="7653",
        kind=kind,
        currency=Currency.COP,
        opened_at=NOW,
    )
    account.pull_events()

    return account


def test_a_declared_account_answers_to_the_card_it_was_given() -> None:
    account = Account.open(
        user_id=USER_ID,
        name="Tarjeta de crédito",
        bank="Bancolombia",
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four="7653",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=NOW,
    )

    assert account.name == "Tarjeta de crédito"
    assert account.bank == "bancolombia"
    # Zero because its owner named no opening balance, which simply means the
    # running total is movement from here on.
    assert account.balance == Balance.zero(Currency.COP)
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountOpened",
        "AccountFingerprintLinked",
    ]


def test_an_account_without_an_instrument_never_matches_an_alert() -> None:
    # Cash in a drawer, a mortgage that emails nothing. Perfectly valid, and
    # simply never claimed by a movement.
    account = Account.open(
        user_id=USER_ID,
        name="Efectivo",
        kind=AccountKind.CASH,
        currency=Currency.COP,
        opened_at=NOW,
    )

    assert account.fingerprints == set()
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountOpened",
    ]


def test_a_declared_account_answers_to_the_pair_that_created_it() -> None:
    account = _declared()

    assert account.matches(
        AccountFingerprint.from_parts(
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four="7653",
        ),
    )


def test_one_account_can_answer_to_several_of_its_banks_names() -> None:
    account = _declared()
    transfers = AccountFingerprint.from_parts(
        bank="bancolombia",
        instrument_kind=InstrumentKind.SAVINGS_ACCOUNT,
        last_four="1234",
    )
    account.link_fingerprint(transfers)

    assert account.matches(transfers)
    assert len(account.fingerprints) == 2
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountFingerprintLinked",
    ]


def test_linking_a_pair_the_account_already_answers_to_changes_nothing() -> None:
    account = _declared()
    account.link_fingerprint(next(iter(account.fingerprints)))

    assert len(account.fingerprints) == 1
    assert account.pull_events() == []


def test_spending_lowers_an_asset_and_income_raises_it() -> None:
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.apply(_movement("80000", MovementDirection.INCOMING))

    assert account.balance.signed_amount == Decimal("30000")
    assert account.movements_applied == 2


def test_spending_on_a_credit_card_raises_what_you_owe() -> None:
    card = _declared(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))

    assert card.category is AccountCategory.LIABILITY
    assert card.balance.signed_amount == Decimal("1200000")
    assert not card.balance.is_negative


def test_paying_a_credit_card_lowers_what_you_owe() -> None:
    card = _declared(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))
    card.apply(_movement("500000", MovementDirection.INCOMING))

    assert card.balance.signed_amount == Decimal("700000")


def test_an_account_discovered_mid_life_may_go_below_zero() -> None:
    # Its real opening balance is unknown: the running total is movement
    # since discovery, and inventing a starting number would be worse.
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))

    assert account.balance.is_negative
    assert account.balance.signed_amount == Decimal("-50000")


def test_a_balance_change_names_the_movement_behind_it() -> None:
    account = _declared()
    account.apply(_movement("50000", movement_id="movement-42"))
    (event,) = account.pull_events()

    assert isinstance(event, AccountBalanceChanged)
    assert event.movement_id == MovementId(value="movement-42")
    assert event.balance == account.balance


def test_a_movement_in_another_currency_is_refused_not_converted() -> None:
    account = _declared()

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
    mortgage = Account.open(
        user_id=USER_ID,
        name="Apartment mortgage",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=NOW,
        opening_balance=_cop("180000000"),
    )

    assert mortgage.opening_balance.signed_amount == Decimal("180000000")
    assert mortgage.balance == mortgage.opening_balance
    assert [type(event).__name__ for event in mortgage.pull_events()] == [
        "AccountOpened",
    ]


def test_a_declared_account_cannot_open_with_another_currency() -> None:
    with pytest.raises(CurrencyMismatchError):
        Account.open(
            user_id=USER_ID,
            name="Savings",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            opened_at=NOW,
            opening_balance=Money(amount=Decimal("100"), currency=Currency.USD),
        )


def test_an_account_needs_a_name() -> None:
    with pytest.raises(ValueError):
        Account.open(
            user_id=USER_ID,
            name="   ",
            kind=AccountKind.CASH,
            currency=Currency.COP,
            opened_at=NOW,
        )


def test_renaming_an_account_keeps_everything_else() -> None:
    account = _declared()
    account.rename("  Daily savings  ")

    assert account.name == "Daily savings"
    assert account.fingerprints
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountRenamed",
    ]


def test_a_closed_account_takes_no_further_movements() -> None:
    account = _declared()
    account.close(LATER)

    assert account.is_closed
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountClosed"
    ]

    with pytest.raises(AccountClosedError):
        account.apply(_movement("50000"))


def test_closing_an_already_closed_account_announces_nothing() -> None:
    account = _declared()
    account.close(NOW)
    account.pull_events()
    account.close(LATER)

    assert account.closed_at == NOW
    assert account.pull_events() == []


def test_replaying_the_ledger_reproduces_the_running_total() -> None:
    account = _declared()
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
    account = _declared()
    truth = [_movement("50000", MovementDirection.OUTGOING, movement_id="a")]
    # What a redelivered movement would have done before the ledger caught it.
    account.apply(truth[0])
    account.apply(truth[0])
    account.pull_events()

    account.rebuild(truth)

    assert account.balance.signed_amount == Decimal("-50000")
    assert account.movements_applied == 1


def test_a_declared_opening_balance_survives_a_replay() -> None:
    mortgage = Account.open(
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
    account = _declared()
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
    account = _declared()
    account.close(LATER)
    account.pull_events()
    account.rebuild([_movement("50000", MovementDirection.OUTGOING)])

    assert account.balance.signed_amount == Decimal("-50000")


def test_a_name_longer_than_the_limit_is_refused() -> None:
    with pytest.raises(ValueError):
        Account.open(
            user_id=USER_ID,
            name="b" * (MAX_ACCOUNT_NAME_LENGTH + 1),
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            opened_at=NOW,
        )


def test_an_account_kind_is_not_an_instrument_kind() -> None:
    """The mistake this enum exists to make impossible.

    A savings account is declared `SAVINGS`, but its transfers arrive naming
    the instrument `ACCOUNT`. Spelling the key with the account kind used to
    be accepted and then matched nothing, forever, reporting nothing.
    """
    assert {kind.value for kind in InstrumentKind} == {
        "credit_card",
        "debit_card",
        "savings_account",
        "checking_account",
        "account",
    }
    assert "savings" not in {kind.value for kind in InstrumentKind}


def test_declaring_and_receiving_the_same_instrument_meet_on_one_key() -> None:
    """The two constructors exist for different sides of the same key, and a
    difference between them is an account that never matches its own alerts.
    """
    declared = AccountFingerprint.from_parts(
        bank="Bancolombia",
        instrument_kind=InstrumentKind.ACCOUNT,
        last_four="5261",
    )
    arrived = AccountFingerprint.from_alert(
        bank="bancolombia",
        instrument_kind="account",
        last_four="5261",
    )

    assert declared == arrived


def test_a_credit_card_reports_what_is_left_of_its_limit() -> None:
    account = Account.open(
        user_id=USER_ID,
        name="Tarjeta",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_700_000_000),
        # What has been spent against the limit so far.
        opening_balance=Money(amount=Decimal("200000"), currency=Currency.COP),
        credit_limit=Money(amount=Decimal("12000000"), currency=Currency.COP),
    )

    assert account.available == Decimal("11800000")

    account.apply(
        LedgerMovement(
            movement_id=MovementId(value="m1"),
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal("46150"), currency=Currency.COP),
            occurred_at=LATER,
        ),
    )

    # Spending raises the debt and lowers what is left. Both readings of the
    # same fact, which is why the limit has to be stated separately.
    assert account.balance.signed_amount == Decimal("246150")
    assert account.available == Decimal("11753850")


def test_a_card_can_be_over_its_limit_and_says_so() -> None:
    account = Account.open(
        user_id=USER_ID,
        name="Tarjeta",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_700_000_000),
        opening_balance=Money(amount=Decimal("120000"), currency=Currency.COP),
        credit_limit=Money(amount=Decimal("100000"), currency=Currency.COP),
    )

    # Clamping this to zero would hide the one case somebody needs to see.
    assert account.available == Decimal("-20000")


def test_an_asset_has_no_credit_limit() -> None:
    with pytest.raises(ValueError, match="no credit limit"):
        Account.open(
            user_id=USER_ID,
            name="Ahorros",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            opened_at=PosixTime.from_epoch_seconds(1_700_000_000),
            credit_limit=Money(amount=Decimal("12000000"), currency=Currency.COP),
        )


def test_a_liability_without_a_stated_limit_reports_no_available() -> None:
    account = Account.open(
        user_id=USER_ID,
        name="Hipoteca",
        kind=AccountKind.MORTGAGE,
        currency=Currency.COP,
        opened_at=PosixTime.from_epoch_seconds(1_700_000_000),
    )

    # Not zero: zero would read as "no credit left", which is a different fact.
    assert account.available is None


def test_a_limit_in_another_currency_is_refused() -> None:
    with pytest.raises(CurrencyMismatchError):
        Account.open(
            user_id=USER_ID,
            name="Tarjeta",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            opened_at=PosixTime.from_epoch_seconds(1_700_000_000),
            credit_limit=Money(amount=Decimal("3000"), currency=Currency.USD),
        )


def _balance(amount: str) -> Balance:
    return Balance.from_signed(Decimal(amount), Currency.COP)


def test_restating_a_balance_solves_the_opening_balance_backwards() -> None:
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING, movement_id="a"))
    account.apply(_movement("30000", MovementDirection.INCOMING, movement_id="b"))
    movements = [
        _movement("50000", MovementDirection.OUTGOING, movement_id="a"),
        _movement("30000", MovementDirection.INCOMING, movement_id="b"),
    ]
    account.pull_events()

    account.restate_balance(_balance("1200000"), movements)

    # The movements already net to -20000, so the account must have started
    # 20000 above what its owner says it holds now.
    assert account.opening_balance.signed_amount == Decimal("1220000")
    assert account.balance.signed_amount == Decimal("1200000")
    assert account.movements_applied == 2
    assert [type(event).__name__ for event in account.pull_events()] == [
        "AccountBalanceRestated",
    ]


def test_a_restated_balance_still_replays_to_the_same_number() -> None:
    account = _declared()
    movements = [
        _movement("50000", MovementDirection.OUTGOING, movement_id="a"),
        _movement("30000", MovementDirection.INCOMING, movement_id="b"),
    ]
    account.restate_balance(_balance("1200000"), movements)
    account.pull_events()

    account.rebuild(movements)

    assert account.balance.signed_amount == Decimal("1200000")


def test_a_movement_after_a_restatement_lands_on_top_of_it() -> None:
    account = _declared()
    account.restate_balance(_balance("1200000"), [])
    account.pull_events()

    account.apply(_movement("200000", MovementDirection.OUTGOING))

    assert account.balance.signed_amount == Decimal("1000000")


def test_restating_a_liability_states_what_is_owed() -> None:
    card = _declared(AccountKind.CREDIT_CARD)
    # Spending raises a card's balance, because what it holds is debt.
    movements = [_movement("450000", MovementDirection.OUTGOING)]
    card.restate_balance(_balance("450000"), movements)

    assert card.opening_balance.signed_amount == Decimal("0")
    assert card.balance.signed_amount == Decimal("450000")


def test_restating_a_balance_below_what_was_spent_goes_negative() -> None:
    account = _declared()
    movements = [_movement("50000", MovementDirection.OUTGOING)]

    account.restate_balance(_balance("10000"), movements)

    assert account.opening_balance.signed_amount == Decimal("60000")
    assert account.balance.signed_amount == Decimal("10000")


def test_a_restatement_in_another_currency_is_refused() -> None:
    account = _declared()

    with pytest.raises(CurrencyMismatchError):
        account.restate_balance(
            Balance.from_signed(Decimal("300"), Currency.USD),
            [],
        )

    assert account.balance.signed_amount == Decimal("0")
    assert account.pull_events() == []


def test_a_restatement_that_cannot_finish_leaves_the_account_untouched() -> None:
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.pull_events()

    with pytest.raises(CurrencyMismatchError):
        account.restate_balance(
            _balance("1200000"),
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

    assert account.opening_balance.signed_amount == Decimal("0")
    assert account.balance.signed_amount == Decimal("-50000")
    assert account.pull_events() == []


def test_a_closed_account_can_still_be_restated() -> None:
    account = _declared()
    account.close(LATER)
    account.pull_events()

    account.restate_balance(_balance("1200000"), [])

    assert account.balance.signed_amount == Decimal("1200000")


# --------------------------------------------- taking a movement back off


def test_reversing_a_movement_puts_the_balance_exactly_back() -> None:
    """The inverse of `apply`, to the cent: a purchase erased is money the
    account holds again, and nothing else moved.
    """
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.apply(_movement("80000", MovementDirection.INCOMING, movement_id="m2"))

    account.reverse(_movement("50000", MovementDirection.OUTGOING))

    assert account.balance.signed_amount == Decimal("80000")
    assert account.movements_applied == 1


def test_reversing_a_card_purchase_lowers_what_it_owes() -> None:
    """Direction alone never says which way a balance goes, in either
    direction: spending raises a card's debt, so erasing it has to lower it.
    """
    card = _declared(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))

    card.reverse(_movement("1200000", MovementDirection.OUTGOING))

    assert card.balance.signed_amount == Decimal("0")


def test_reversing_a_payment_to_a_card_raises_the_debt_again() -> None:
    card = _declared(AccountKind.CREDIT_CARD)
    card.apply(_movement("1200000", MovementDirection.OUTGOING))
    card.apply(_movement("500000", MovementDirection.INCOMING, movement_id="m2"))

    card.reverse(_movement("500000", MovementDirection.INCOMING, movement_id="m2"))

    assert card.balance.signed_amount == Decimal("1200000")


def test_reversing_announces_where_the_balance_landed() -> None:
    """Its own fact rather than a balance change with the direction flipped: a
    reader following `direction` back would be chasing a row that is gone.
    """
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.pull_events()

    account.reverse(_movement("50000", MovementDirection.OUTGOING))
    events = account.pull_events()

    assert len(events) == 1
    reversed_ = events[0]
    assert isinstance(reversed_, AccountBalanceReversed)
    assert reversed_.movement_id == MovementId(value="movement-1")
    assert reversed_.direction is MovementDirection.OUTGOING
    assert reversed_.amount == _cop("50000")
    assert reversed_.balance.signed_amount == Decimal("0")


def test_a_closed_account_can_still_have_a_movement_taken_off_it() -> None:
    """Closed stops it taking *new* movements. Removing one that should never
    have been on it is a correction of what is already there — the same reason
    `rebuild` and `restate_balance` are allowed on a closed account. Refusing
    would leave a wrong row with nothing that could ever take it off.
    """
    account = _declared()
    account.apply(_movement("50000", MovementDirection.OUTGOING))
    account.close(LATER)

    account.reverse(_movement("50000", MovementDirection.OUTGOING))

    assert account.balance.signed_amount == Decimal("0")

    with pytest.raises(AccountClosedError):
        account.apply(_movement("1000", MovementDirection.OUTGOING))


def test_the_tally_of_applied_movements_never_goes_below_zero() -> None:
    """A negative count would be reported to the owner as a fact about their
    account instead of as the bug it is.
    """
    account = _declared()

    account.reverse(_movement("50000", MovementDirection.OUTGOING))

    assert account.movements_applied == 0
