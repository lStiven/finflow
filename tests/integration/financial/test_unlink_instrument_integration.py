"""Moving a card from the account it was declared on to the right one.

Against a real table, because the half a fake cannot see is the one that
matters: a fingerprint is stored twice — on the account, and as the entry
`find_by_fingerprint` reads — and only the entry decides where the *next*
alert lands. An in-memory repository holding one object would report a card
unlinked while every future movement kept arriving on the old account.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransactionCommand,
    LinkInstrumentCommand,
    OpenAccountCommand,
    RecordMovementCommand,
    RecordTransferCommand,
    UnlinkInstrumentCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageAccountsUseCase,
    ManageTransactionsUseCase,
    RecordMovementUseCase,
    RecordTransferUseCase,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.exceptions import (
    InstrumentNotLinkedError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountKind,
    InstrumentKind,
    MovementDirection,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "financial"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
WHEN = PosixTime.from_datetime(datetime(2026, 8, 23, 14, 5, tzinfo=UTC))
CARD_DIGITS = "0530"


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> str:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )

    return TABLE_NAME


@pytest.fixture
def accounts(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def ledger(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(client=dynamodb_client, table_name=table)


@pytest.fixture
def manage_accounts(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> ManageAccountsUseCase:
    return ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def manage_transactions(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> ManageTransactionsUseCase:
    return ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def record_movement(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> RecordMovementUseCase:
    return RecordMovementUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def record_transfer(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> RecordTransferUseCase:
    return RecordTransferUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    )


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _open(
    manage_accounts: ManageAccountsUseCase,
    name: str,
    *,
    last_four: str | None = None,
) -> Account:
    return manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name=name,
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=None if last_four is None else InstrumentKind.DEBIT_CARD,
            last_four=last_four,
        ),
    )


def _card_alert(
    record_movement: RecordMovementUseCase,
    amount: str,
    *,
    counterparty: str = "EXITO",
) -> None:
    record_movement.execute(
        RecordMovementCommand(
            user_id=USER_ID,
            bank="Bancolombia",
            direction=MovementDirection.OUTGOING,
            amount=_cop(amount),
            occurred_at=WHEN,
            counterparty=counterparty,
            instrument_kind="debit_card",
            last_four=CARD_DIGITS,
        ),
    )


def _unlink(manage_accounts: ManageAccountsUseCase, account: Account) -> Account:
    return manage_accounts.unlink_instrument(
        UnlinkInstrumentCommand(
            user_id=USER_ID,
            account_id=account.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )


def test_the_card_stops_reaching_the_account_it_was_declared_on(
    manage_accounts: ManageAccountsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    """The entry, not the account's own copy, is what routes the next alert."""
    wrong = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)

    _unlink(manage_accounts, wrong)

    fingerprint = AccountFingerprint.from_parts(
        bank="Bancolombia",
        instrument_kind=InstrumentKind.DEBIT_CARD,
        last_four=CARD_DIGITS,
    )
    assert (
        accounts.find_by_fingerprint(user_id=USER_ID, fingerprint=fingerprint) is None
    )

    stored = accounts.find(user_id=USER_ID, account_id=wrong.id)
    assert stored is not None
    assert stored.fingerprints == set()


def test_the_movements_it_brought_go_back_and_the_balance_follows(
    manage_accounts: ManageAccountsUseCase,
    record_movement: RecordMovementUseCase,
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    wrong = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)
    _card_alert(record_movement, "120000")
    _card_alert(record_movement, "80000", counterparty="RAPPI")

    unlinked = _unlink(manage_accounts, wrong)

    assert unlinked.balance.signed_amount == Decimal("0")
    assert unlinked.movements_applied == 0
    assert len(ledger.list_unassigned(USER_ID)) == 2

    stored = accounts.find(user_id=USER_ID, account_id=wrong.id)
    assert stored is not None
    assert stored.balance.signed_amount == Decimal("0")


def test_the_right_account_then_adopts_them(
    manage_accounts: ManageAccountsUseCase,
    record_movement: RecordMovementUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The whole point: a card on the wrong account can now be moved.

    Unlinking releases the movements and linking on the other account adopts
    them, which is the retroactive path that already existed — it simply had
    no way to be reached once a card was declared in the wrong place.
    """
    wrong = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)
    right = _open(manage_accounts, "Corriente")
    _card_alert(record_movement, "120000")

    _unlink(manage_accounts, wrong)
    adopted = manage_accounts.link_instrument(
        LinkInstrumentCommand(
            user_id=USER_ID,
            account_id=right.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )

    assert adopted.balance.signed_amount == Decimal("-120000")
    assert adopted.movements_applied == 1
    assert ledger.list_unassigned(USER_ID) == []


def test_a_movement_under_another_of_its_cards_is_left_alone(
    manage_accounts: ManageAccountsUseCase,
    record_movement: RecordMovementUseCase,
) -> None:
    """Only the key being unlinked lets go. One account answers to several."""
    account = _open(manage_accounts, "Ahorros", last_four="5261")
    manage_accounts.link_instrument(
        LinkInstrumentCommand(
            user_id=USER_ID,
            account_id=account.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )
    record_movement.execute(
        RecordMovementCommand(
            user_id=USER_ID,
            bank="Bancolombia",
            direction=MovementDirection.OUTGOING,
            amount=_cop("50000"),
            occurred_at=WHEN,
            counterparty="NEQUI",
            instrument_kind="debit_card",
            last_four="5261",
        ),
    )
    _card_alert(record_movement, "120000")

    unlinked = _unlink(manage_accounts, account)

    assert unlinked.balance.signed_amount == Decimal("-50000")
    assert unlinked.movements_applied == 1


def test_a_movement_entered_by_hand_stays_where_its_owner_put_it(
    manage_accounts: ManageAccountsUseCase,
    manage_transactions: ManageTransactionsUseCase,
) -> None:
    """It named no card, so no card can take it away."""
    account = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)
    manage_transactions.enter(
        EnterTransactionCommand(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=_cop("30000"),
            occurred_at=WHEN,
            counterparty="Arriendo",
            account_id=account.id,
        ),
    )

    unlinked = _unlink(manage_accounts, account)

    assert unlinked.balance.signed_amount == Decimal("-30000")
    assert unlinked.movements_applied == 1


def test_a_transfer_leg_goes_back_with_the_rest(
    manage_accounts: ManageAccountsUseCase,
    record_transfer: RecordTransferUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Safe here, unlike a leg detached by hand.

    `detach` refuses a leg that names no instrument, because unassigned it
    would claim a payment no balance shows and nothing could ever adopt it.
    A leg released here named the very card being moved, so linking that card
    somewhere else takes it back.
    """
    card = manage_accounts.open(
        OpenAccountCommand(
            user_id=USER_ID,
            name="Tarjeta",
            kind=AccountKind.CREDIT_CARD,
            currency=Currency.COP,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )
    _open(manage_accounts, "Ahorros", last_four="5261")
    record_transfer.execute(
        RecordTransferCommand(
            user_id=USER_ID,
            bank="Bancolombia",
            amount=_cop("200000"),
            occurred_at=WHEN,
            source_instrument_kind="debit_card",
            source_last_four="5261",
            destination_instrument_kind="credit_card",
            destination_last_four=CARD_DIGITS,
        ),
    )

    unlinked = manage_accounts.unlink_instrument(
        UnlinkInstrumentCommand(
            user_id=USER_ID,
            account_id=card.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.CREDIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )

    assert unlinked.movements_applied == 0
    assert unlinked.balance.signed_amount == Decimal("0")
    # The other side of the pair never moved: only one card was unlinked.
    released = ledger.list_unassigned(USER_ID)
    assert len(released) == 1
    assert released[0].is_transfer


def test_running_it_again_finishes_an_unlink_that_stopped_half_way(
    manage_accounts: ManageAccountsUseCase,
    accounts: DynamoDBAccountRepository,
    record_movement: RecordMovementUseCase,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The key is dropped first, so a crash leaves rows and not routing.

    Releasing hundreds of movements is not one write and cannot be. What the
    order buys is that the leftover state is the repairable one: the account
    stopped matching, so no new alert joins the pile, and running the same
    unlink again lets the rows already on it go.
    """
    account = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)
    _card_alert(record_movement, "120000")

    # Exactly the state a crash between the two steps leaves behind: the key
    # gone from both places, the movement still on the account.
    stored = accounts.find(user_id=USER_ID, account_id=account.id)
    assert stored is not None
    stored.unlink_fingerprint(
        AccountFingerprint.from_parts(
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )
    accounts.unlink_fingerprint(
        stored,
        AccountFingerprint.from_parts(
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )

    finished = _unlink(manage_accounts, account)

    assert finished.balance.signed_amount == Decimal("0")
    assert len(ledger.list_unassigned(USER_ID)) == 1


def test_a_card_another_account_has_since_claimed_is_not_taken_from_it(
    manage_accounts: ManageAccountsUseCase,
    accounts: DynamoDBAccountRepository,
) -> None:
    """Linking the same card twice leaves two accounts claiming it.

    The second link overwrites the entry, so unlinking from the first must
    drop only its own copy: deleting the entry would take the card away from
    the account that holds it now, and leave a card nothing answers to.
    """
    first = _open(manage_accounts, "Ahorros", last_four=CARD_DIGITS)
    second = _open(manage_accounts, "Corriente")
    manage_accounts.link_instrument(
        LinkInstrumentCommand(
            user_id=USER_ID,
            account_id=second.id,
            bank="Bancolombia",
            instrument_kind=InstrumentKind.DEBIT_CARD,
            last_four=CARD_DIGITS,
        ),
    )

    unlinked = _unlink(manage_accounts, first)

    assert unlinked.fingerprints == set()

    fingerprint = AccountFingerprint.from_parts(
        bank="Bancolombia",
        instrument_kind=InstrumentKind.DEBIT_CARD,
        last_four=CARD_DIGITS,
    )
    still_routed = accounts.find_by_fingerprint(
        user_id=USER_ID,
        fingerprint=fingerprint,
    )
    assert still_routed is not None
    assert still_routed.id == second.id


def test_a_card_this_account_never_answered_to_is_refused(
    manage_accounts: ManageAccountsUseCase,
) -> None:
    """Not silence: it is presumably on another account, and reporting "done"
    would send its owner looking somewhere else.
    """
    account = _open(manage_accounts, "Ahorros", last_four="5261")

    with pytest.raises(InstrumentNotLinkedError):
        _unlink(manage_accounts, account)
