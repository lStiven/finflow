"""Two users, one table, real DynamoDB: can either one reach the other's data?

The unit tests already pin that an endpoint answers 404 for somebody else's
id. What they cannot pin is the layer underneath: every repository here scopes
by partition key, and a query written without the owner would still pass a
test whose fake dictionary was keyed by user. So these run against the real
adapters, with both users' records in the same table — which is the shape
production has, and the only one where a missing partition condition shows up.
"""

from collections.abc import Sequence
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    EnterTransferLegCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    ManageTransactionsUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    ListTransactionsUseCase,
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryQuery,
    TransactionQuery,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransferRole,
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


TABLE = "financial"

ANA = UserId.from_string("11111111-1111-1111-1111-111111111111")
BRUNO = UserId.from_string("22222222-2222-2222-2222-222222222222")

WHEN = PosixTime.from_epoch_seconds(1_787_500_000)

# The same bank and the same last four for both of them. `AccountFingerprint`
# carries no user of its own, so this is the collision the partition key is
# the only thing preventing — two people at one bank whose cards happen to end
# the same way.
BANK = "bancolombia"
LAST_FOUR = "1234"


@pytest.fixture
def ledger(dynamodb_client: DynamoDBClient) -> DynamoDBTransactionLedger:
    provision_table(
        dynamodb_client,
        table_name=TABLE,
        billing_mode="PAY_PER_REQUEST",
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )

    return DynamoDBTransactionLedger(client=dynamodb_client, table_name=TABLE)


@pytest.fixture
def accounts(
    dynamodb_client: DynamoDBClient,
    ledger: DynamoDBTransactionLedger,
) -> DynamoDBAccountRepository:
    del ledger

    return DynamoDBAccountRepository(client=dynamodb_client, table_name=TABLE)


def _declare(
    repository: DynamoDBAccountRepository,
    *,
    user_id: UserId,
    name: str,
) -> Account:
    account = Account.open(
        user_id=user_id,
        name=name,
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=WHEN,
        bank=BANK,
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four=LAST_FOUR,
    )
    repository.add(account)

    return account


def _spend(
    ledger: DynamoDBTransactionLedger,
    *,
    user_id: UserId,
    counterparty: str,
    amount: str,
) -> Transaction:
    movement = Transaction.enter_manually(
        user_id=user_id,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=WHEN,
        counterparty=counterparty,
    )
    ledger.record(transaction=movement, balance_delta=None)

    return movement


def _both_spend(ledger: DynamoDBTransactionLedger) -> tuple[Transaction, Transaction]:
    return (
        _spend(ledger, user_id=ANA, counterparty="TIENDAS ARA", amount="50000"),
        _spend(ledger, user_id=BRUNO, counterparty="RAPPI COLOMBIA", amount="20000"),
    )


class _NullPublisher:
    """Nothing here asserts on events; only on who can reach whose rows."""

    def publish(self, events: Sequence[Event]) -> None:
        del events


def test_a_ledger_read_returns_only_the_owners_movements(
    ledger: DynamoDBTransactionLedger,
) -> None:
    ana_movement, bruno_movement = _both_spend(ledger)

    ana_rows = [row.id for row in ledger.list_all(ANA)]
    bruno_rows = [row.id for row in ledger.list_all(BRUNO)]

    assert ana_rows == [ana_movement.id]
    assert bruno_rows == [bruno_movement.id]


def test_a_movement_cannot_be_fetched_with_the_wrong_owner(
    ledger: DynamoDBTransactionLedger,
) -> None:
    # Knowing an id is not authorisation: the id is derived from content, so
    # somebody who guessed one must still not be able to read the row.
    _, bruno_movement = _both_spend(ledger)

    assert ledger.find(user_id=ANA, transaction_id=bruno_movement.id.value) is None
    assert (
        ledger.find(user_id=BRUNO, transaction_id=bruno_movement.id.value) is not None
    )


def test_two_users_can_hold_the_same_card_digits_without_colliding(
    accounts: DynamoDBAccountRepository,
) -> None:
    """`AccountFingerprint` has no user in it, so this is the case the
    partition key alone keeps apart.
    """
    ana_account = _declare(accounts, user_id=ANA, name="Tarjeta Ana")
    bruno_account = _declare(accounts, user_id=BRUNO, name="Tarjeta Bruno")

    fingerprint = AccountFingerprint.from_parts(
        bank=BANK,
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four=LAST_FOUR,
    )

    assert ana_account.id != bruno_account.id
    assert (
        accounts.find_by_fingerprint(user_id=ANA, fingerprint=fingerprint)
    ).id == ana_account.id  # type: ignore[union-attr]
    assert (
        accounts.find_by_fingerprint(user_id=BRUNO, fingerprint=fingerprint)
    ).id == bruno_account.id  # type: ignore[union-attr]


def test_an_account_cannot_be_loaded_with_the_wrong_owner(
    accounts: DynamoDBAccountRepository,
) -> None:
    bruno_account = _declare(accounts, user_id=BRUNO, name="Tarjeta Bruno")

    assert accounts.find(user_id=ANA, account_id=bruno_account.id) is None
    assert accounts.list_by_user(ANA) == []


def test_an_alert_never_lands_on_another_users_account(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """The retroactive-adoption query is the dangerous one: it looks movements
    up by fingerprint, and that fingerprint is not unique across users.
    """
    _declare(accounts, user_id=ANA, name="Tarjeta Ana")
    bruno_movement = Transaction.from_alert(
        user_id=BRUNO,
        bank=BANK,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal("31000"), currency=Currency.COP),
        occurred_at=WHEN,
        counterparty="RAPPI COLOMBIA",
        instrument_kind="credit_card",
        last_four=LAST_FOUR,
    )
    ledger.record(transaction=bruno_movement, balance_delta=None)

    fingerprint = AccountFingerprint.from_parts(
        bank=BANK,
        instrument_kind=InstrumentKind.CREDIT_CARD,
        last_four=LAST_FOUR,
    )
    waiting = ledger.list_unassigned_matching(user_id=ANA, fingerprint=fingerprint)

    assert waiting == [], "Ana's account must not adopt Bruno's movement"


def test_a_summary_never_totals_another_users_spending(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    _both_spend(ledger)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(filter=MovementFilter(user_id=ANA)),
    )

    assert summary.totals[0].outgoing == Decimal("50000")


def test_a_search_never_reaches_another_users_counterparties(
    ledger: DynamoDBTransactionLedger,
) -> None:
    # Filtering happens in memory over what the repository loaded, so a
    # repository scoped wrongly would surface right here.
    _both_spend(ledger)

    page = ListTransactionsUseCase(ledger=ledger).execute(
        TransactionQuery(filter=MovementFilter(user_id=ANA, search="RAPPI")),
    )

    assert page.total == 0


def test_clearing_a_credit_limit_removes_it_from_storage(
    accounts: DynamoDBAccountRepository,
) -> None:
    """`save` assigns; an attribute the entity dropped has to be removed.

    An update that only SETs leaves the previous value in place, so clearing a
    limit answered success and changed nothing — visible only on the next read
    from real storage, which is why this test cannot live beside an in-memory
    repository.
    """
    account = _declare(accounts, user_id=ANA, name="Tarjeta Ana")
    account.set_credit_limit(Money(amount=Decimal("12000000"), currency=Currency.COP))
    accounts.save(account)

    stored = accounts.find(user_id=ANA, account_id=account.id)
    assert stored is not None
    assert stored.credit_limit is not None
    assert stored.available == Decimal("12000000")

    stored.set_credit_limit(None)
    accounts.save(stored)

    cleared = accounts.find(user_id=ANA, account_id=account.id)
    assert cleared is not None
    assert cleared.credit_limit is None
    assert cleared.available is None


def test_a_transfer_leg_cannot_be_entered_on_another_users_account(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
) -> None:
    """Paying a card from outside the app names its account by id, and an id
    travels in a request body where a partition key does not. Ana asking to
    clear Bruno's card must miss in her own partition, not find his."""
    bruno_card = _declare(accounts, user_id=BRUNO, name="Tarjeta de Bruno")
    owed_before = bruno_card.balance.signed_amount
    use_case = ManageTransactionsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=_NullPublisher(),
    )

    with pytest.raises(AccountNotFoundError):
        use_case.enter_transfer_leg(
            EnterTransferLegCommand(
                user_id=ANA,
                role=TransferRole.DESTINATION,
                amount=Money(amount=Decimal("100000"), currency=Currency.COP),
                occurred_at=WHEN,
                counterparty="Nequi",
                account_id=bruno_card.id,
            ),
        )

    still_there = accounts.find(user_id=BRUNO, account_id=bruno_card.id)
    assert still_there is not None
    assert still_there.balance.signed_amount == owed_before
    assert ledger.list_all(ANA) == []
