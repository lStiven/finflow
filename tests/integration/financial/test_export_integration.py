"""An export read from the real tables: Financial's ledger and accounts, and
Merchant's merchants and categories, joined the way production joins them.

The fakes agree with themselves about how an account id reads back and what a
category is called. Only the stored rows can say whether the file names the
account its owner declared and the category they wrote for themselves.
"""

from collections.abc import Sequence
import csv
from decimal import Decimal
import io
import uuid
from zoneinfo import ZoneInfo

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.export import (
    ExportTransactionsUseCase,
)
from personal_finance.contexts.financial.application.queries import (
    MovementFilter,
    TransferView,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountKind,
    MovementDirection,
)
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    MerchantContextDirectory,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.contexts.financial.presentation.http.export import (
    encode_csv,
    export_rows,
)
from personal_finance.contexts.merchant.application.categories import CategoryCatalog
from personal_finance.contexts.merchant.application.commands import (
    ClassifyCounterpartyCommand,
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ClassifyCounterpartyUseCase,
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY as MERCHANT_PARTITION_KEY,
    SORT_KEY as MERCHANT_SORT_KEY,
    DynamoDBCategoryRepository,
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
AUGUST_MIDDAY = PosixTime.from_epoch_seconds(1_787_500_000)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def tables(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=FINANCIAL_TABLE,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
        enable_ttl=False,
    )
    provision_table(
        dynamodb_client,
        table_name=MERCHANT_TABLE,
        partition_key=MERCHANT_PARTITION_KEY,
        sort_key=MERCHANT_SORT_KEY,
        sort_key_type="S",
    )

    return dynamodb_client


def test_the_file_names_the_declared_account_and_the_owners_own_category(
    tables: DynamoDBClient,
) -> None:
    ledger = DynamoDBTransactionLedger(client=tables, table_name=FINANCIAL_TABLE)
    accounts = DynamoDBAccountRepository(client=tables, table_name=FINANCIAL_TABLE)
    merchants = DynamoDBMerchantRepository(client=tables, table_name=MERCHANT_TABLE)
    category_rows = DynamoDBCategoryRepository(client=tables, table_name=MERCHANT_TABLE)
    catalog = CategoryCatalog(repository=category_rows)
    classify = ClassifyCounterpartyUseCase(
        repository=merchants,
        event_publisher=NullEventPublisher(),
        categories=catalog,
    )
    directory = MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(repository=merchants),
        catalog=catalog,
        classify=classify,
    )

    account = Account.open(
        user_id=USER_ID,
        name="Ahorros Bancolombia",
        kind=AccountKind.SAVINGS,
        currency=Currency.COP,
        opened_at=AUGUST_MIDDAY,
    )
    accounts.add(account)
    cats = Category.create(user_id=USER_ID, label="Gatos", created_at=AUGUST_MIDDAY)
    category_rows.add(cats)

    ResolveMerchantUseCase(
        repository=merchants,
        processed_events=DynamoDBProcessedEventStore(
            client=tables,
            table_name=MERCHANT_TABLE,
        ),
        event_publisher=NullEventPublisher(),
    ).execute(
        RecordSightingCommand(
            user_id=USER_ID,
            counterparty="VETERINARIA PELOS",
            occurred_at=AUGUST_MIDDAY,
            event_id=uuid.uuid4(),
        ),
    )
    classify.execute(
        ClassifyCounterpartyCommand(
            user_id=USER_ID,
            counterparty="VETERINARIA PELOS",
            category=cats.id,
            occurred_at=AUGUST_MIDDAY,
        ),
    )
    ledger.record(
        transaction=Transaction.enter_manually(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal("120000.50"), currency=Currency.COP),
            occurred_at=AUGUST_MIDDAY,
            counterparty="VETERINARIA PELOS",
            account_id=account.id,
        ),
        balance_delta=Decimal("-120000.50"),
    )

    export = ExportTransactionsUseCase(
        ledger=ledger,
        accounts=accounts,
        merchants=directory,
        categories=directory,
    ).execute(MovementFilter(user_id=USER_ID, transfers=TransferView.INCLUDE))
    body = encode_csv(export_rows(export, zone=ZoneInfo("America/Bogota")))
    [row] = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))

    assert row["Cuenta"] == "Ahorros Bancolombia"
    assert row["Categoría"] == "Gatos"
    assert row["Monto"] == "120000.50"
    assert row["Fecha"] == "2026-08-23 10:46"
