"""The join between a movement and its merchant, across two real tables.

Both halves are the real adapters: a counterparty resolved by merchant's own
use case into merchant's own table, and a movement written to Financial's
ledger. Nothing here shares an object between the two contexts — the only
thing connecting them is the counterparty text the bank sent, which is exactly
what production has.

That matters because the join is made on read. A test over fakes can agree
with itself about how a spelling normalizes; only merchant's own storage can
say whether `TIENDAS ARA 123` written by a worker still answers when
Financial asks for it back.
"""

from collections.abc import Sequence
from decimal import Decimal
import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.queries import (
    ListTransactionsUseCase,
    MovementFilter,
    SummarizeSpendingUseCase,
    SummaryGrouping,
    SummaryQuery,
    TransactionQuery,
    TransferView,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import MovementDirection
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    MerchantContextDirectory,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as MERCHANT_PARTITION_KEY,
    SORT_KEY as MERCHANT_SORT_KEY,
    DynamoDBMerchantRepository,
    DynamoDBProcessedEventStore,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


FINANCIAL_TABLE = "financial"
MERCHANT_TABLE = "merchants"

USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")

# Bogotá is UTC-5, so this is 8pm on 31 August locally and already September
# in UTC. Grouped in UTC it would land in the wrong month.
AUGUST_LAST_NIGHT = PosixTime.from_epoch_seconds(1_788_224_400)
AUGUST_MIDDAY = PosixTime.from_epoch_seconds(1_787_500_000)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def ledger(dynamodb_client: DynamoDBClient) -> DynamoDBTransactionLedger:
    provision_table(
        dynamodb_client,
        table_name=FINANCIAL_TABLE,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )

    return DynamoDBTransactionLedger(
        client=dynamodb_client,
        table_name=FINANCIAL_TABLE,
    )


@pytest.fixture
def accounts(
    dynamodb_client: DynamoDBClient,
    ledger: DynamoDBTransactionLedger,
) -> DynamoDBAccountRepository:
    del ledger

    return DynamoDBAccountRepository(
        client=dynamodb_client,
        table_name=FINANCIAL_TABLE,
    )


@pytest.fixture
def resolve_merchant(dynamodb_client: DynamoDBClient) -> ResolveMerchantUseCase:
    provision_table(
        dynamodb_client,
        table_name=MERCHANT_TABLE,
        partition_key=MERCHANT_PARTITION_KEY,
        sort_key=MERCHANT_SORT_KEY,
        sort_key_type="S",
    )

    return ResolveMerchantUseCase(
        repository=DynamoDBMerchantRepository(
            client=dynamodb_client,
            table_name=MERCHANT_TABLE,
        ),
        processed_events=DynamoDBProcessedEventStore(
            client=dynamodb_client,
            table_name=MERCHANT_TABLE,
        ),
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def directory(
    dynamodb_client: DynamoDBClient,
    resolve_merchant: ResolveMerchantUseCase,
) -> MerchantContextDirectory:
    del resolve_merchant

    return MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(
            repository=DynamoDBMerchantRepository(
                client=dynamodb_client,
                table_name=MERCHANT_TABLE,
            ),
        ),
    )


def _spend(
    ledger: DynamoDBTransactionLedger,
    *,
    counterparty: str,
    amount: str = "50000",
    when: PosixTime = AUGUST_MIDDAY,
) -> Transaction:
    """Write a movement the way the worker does: one ledger row, unassigned."""
    movement = Transaction.enter_manually(
        user_id=USER_ID,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal(amount), currency=Currency.COP),
        occurred_at=when,
        counterparty=counterparty,
    )
    ledger.record(transaction=movement, balance_delta=None)

    return movement


def _sight(use_case: ResolveMerchantUseCase, counterparty: str) -> None:
    """What merchant's worker does with the same alert."""
    use_case.execute(
        RecordSightingCommand(
            user_id=USER_ID,
            counterparty=counterparty,
            occurred_at=AUGUST_MIDDAY,
            event_id=uuid.uuid4(),
        ),
    )


def test_a_movement_reads_back_with_the_merchant_its_own_worker_resolved(
    ledger: DynamoDBTransactionLedger,
    resolve_merchant: ResolveMerchantUseCase,
    directory: MerchantContextDirectory,
) -> None:
    _sight(resolve_merchant, "TIENDAS ARA 123")
    _spend(ledger, counterparty="TIENDAS ARA 123")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.INCLUDE),
        ),
    )
    merchant = page.transactions[0].merchant

    assert merchant is not None
    assert merchant.display_name == "Tiendas Ara 123"
    assert merchant.category == "uncategorized"


def test_two_spellings_of_one_business_add_up_under_a_single_merchant(
    ledger: DynamoDBTransactionLedger,
    accounts: DynamoDBAccountRepository,
    resolve_merchant: ResolveMerchantUseCase,
    directory: MerchantContextDirectory,
) -> None:
    # The point of the whole join: `TIENDAS ARA 123` and `ARA 900` are one
    # business, and text search would never have said so.
    _sight(resolve_merchant, "TIENDAS ARA 123")
    _sight(resolve_merchant, "TIENDAS ARA 900")
    _spend(ledger, counterparty="TIENDAS ARA 123", amount="50000")
    _spend(ledger, counterparty="TIENDAS ARA 900", amount="30000")

    summary = SummarizeSpendingUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
    ).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
            group_by=SummaryGrouping.MERCHANT,
        ),
    )

    assert len(summary.groups) == 1
    assert summary.groups[0].totals[0].outgoing == Decimal("80000")


def test_a_movement_whose_sighting_has_not_landed_yet_still_reads(
    ledger: DynamoDBTransactionLedger,
    directory: MerchantContextDirectory,
) -> None:
    # The two workers drain their own queues independently, so this is the
    # ordinary state for a few seconds after an alert arrives.
    _spend(ledger, counterparty="TIENDAS ARA 123")

    page = ListTransactionsUseCase(ledger=ledger, merchants=directory).execute(
        TransactionQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.INCLUDE),
        ),
    )

    assert page.total == 1
    assert page.transactions[0].merchant is None


def test_a_late_evening_purchase_is_summarized_in_its_local_month(
    ledger: DynamoDBTransactionLedger,
    accounts: DynamoDBAccountRepository,
) -> None:
    _spend(ledger, counterparty="TIENDAS ARA 123", when=AUGUST_LAST_NIGHT)

    summary = SummarizeSpendingUseCase(ledger=ledger, accounts=accounts).execute(
        SummaryQuery(
            filter=MovementFilter(user_id=USER_ID, transfers=TransferView.EXCLUDE),
        ),
    )

    assert [group.key for group in summary.groups] == ["2026-08"]
