"""Declared bills against a real table.

Why a fake is not enough here. An in-memory repository stores the object it
was handed, so every question about *storage* is begged: whether the row lands
in the right partition under the right prefix, whether a date survives the
round trip as the same calendar day, and — the one that would go unnoticed
longest — whether a whole-item put actually removes an attribute that became
absent. A bill whose account was cleared and that still carries the old
`account_id` in DynamoDB would look correct in every unit test and charge the
wrong account the day confirming exists.

The user-isolation test is here rather than upstairs for the same reason: the
scoping is a key, and a key is only real once something writes it.
"""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.bills import (
    AmendBillCommand,
    DeclareBillCommand,
    ListBillsQuery,
    ListBillsUseCase,
    ManageBillsUseCase,
    NoSuchBillError,
)
from personal_finance.contexts.financial.application.commands import OpenAccountCommand
from personal_finance.contexts.financial.application.handlers import (
    AccountNotFoundError,
    ManageAccountsUseCase,
)
from personal_finance.contexts.financial.domain.bills import (
    BillCadence,
    BillId,
    BillStatus,
)
from personal_finance.contexts.financial.domain.entities import Account
from personal_finance.contexts.financial.domain.value_objects import AccountKind
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    BILL_PREFIX,
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBAccountRepository,
    DynamoDBScheduledBillRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import Currency, Money, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "financial-bills-test"
TIMEZONE = "America/Bogota"

OWNER = UserId.from_string("22222222-2222-2222-2222-222222222222")
STRANGER = UserId.from_string("33333333-3333-3333-3333-333333333333")


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
def bills(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBScheduledBillRepository:
    return DynamoDBScheduledBillRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def accounts(dynamodb_client: DynamoDBClient, table: str) -> DynamoDBAccountRepository:
    return DynamoDBAccountRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def manage(
    bills: DynamoDBScheduledBillRepository,
    accounts: DynamoDBAccountRepository,
) -> ManageBillsUseCase:
    return ManageBillsUseCase(bills=bills, accounts=accounts)


@pytest.fixture
def listing(
    bills: DynamoDBScheduledBillRepository,
    accounts: DynamoDBAccountRepository,
) -> ListBillsUseCase:
    return ListBillsUseCase(bills=bills, accounts=accounts)


def _declare(user_id: UserId = OWNER, **overrides: object) -> DeclareBillCommand:
    values: dict[str, object] = {
        "user_id": user_id,
        "name": "Gimnasio",
        "amount": Money(amount=Decimal("120000"), currency=Currency.COP),
        "cadence": BillCadence.MONTHLY,
        "starts_on": dt.date(2026, 9, 4),
    }

    return DeclareBillCommand(**(values | overrides))  # type: ignore[arg-type]


def _open_account(
    accounts: DynamoDBAccountRepository,
    ledger: DynamoDBTransactionLedger,
    user_id: UserId = OWNER,
) -> Account:
    return ManageAccountsUseCase(
        accounts=accounts,
        ledger=ledger,
        event_publisher=NullEventPublisher(),
    ).open(
        OpenAccountCommand(
            user_id=user_id,
            name="Ahorros",
            kind=AccountKind.SAVINGS,
            currency=Currency.COP,
        ),
    )


# ----------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------


def test_a_bill_survives_the_round_trip_unchanged(
    manage: ManageBillsUseCase,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    declared = manage.declare(_declare(category="salud")).bill

    stored = bills.find(user_id=OWNER, bill_id=declared.id)

    assert stored is not None
    assert stored.id == declared.id
    assert stored.name == declared.name
    assert stored.amount == declared.amount
    assert stored.cadence is declared.cadence
    assert stored.category == "salud"
    # The one that a timestamp would break: a calendar day has to come back as
    # the same day whatever zone reads it.
    assert stored.starts_on == dt.date(2026, 9, 4)


def test_a_bill_lands_under_its_own_prefix_in_its_owners_partition(
    manage: ManageBillsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    bill = manage.declare(_declare()).bill

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": f"{BILL_PREFIX}{bill.id.value}"},
        },
    ).get("Item")

    assert item is not None


def test_clearing_an_account_removes_the_attribute_rather_than_leaving_it(
    manage: ManageBillsUseCase,
    accounts: DynamoDBAccountRepository,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    """The bug a fake cannot show: a stale `account_id` in the row would read
    back as a bill still pointing at an account its owner detached."""
    account = _open_account(
        accounts,
        DynamoDBTransactionLedger(client=dynamodb_client, table_name=table),
    )
    bill = manage.declare(_declare(account_id=account.id)).bill

    manage.amend(
        AmendBillCommand(user_id=OWNER, bill_id=bill.id, clear_account=True),
    )

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": f"{BILL_PREFIX}{bill.id.value}"},
        },
    ).get("Item")

    assert item is not None
    assert "account_id" not in item


def test_a_forgotten_bill_is_gone_and_saying_so_twice_is_false(
    manage: ManageBillsUseCase,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    bill = manage.declare(_declare()).bill

    assert bills.remove(user_id=OWNER, bill_id=bill.id) is True
    assert bills.remove(user_id=OWNER, bill_id=bill.id) is False
    assert bills.find(user_id=OWNER, bill_id=bill.id) is None


def test_pausing_survives_a_reload(
    manage: ManageBillsUseCase,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    bill = manage.declare(_declare()).bill
    manage.pause(user_id=OWNER, bill_id=bill.id)

    reloaded = bills.find(user_id=OWNER, bill_id=bill.id)

    assert reloaded is not None
    assert reloaded.status is BillStatus.PAUSED


# ----------------------------------------------------------------------
# One person's bills are their own
# ----------------------------------------------------------------------


def test_a_stranger_cannot_read_a_bill_by_its_id(
    manage: ManageBillsUseCase,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    bill = manage.declare(_declare()).bill

    assert bills.find(user_id=STRANGER, bill_id=bill.id) is None


def test_a_stranger_cannot_delete_a_bill_by_its_id(
    manage: ManageBillsUseCase,
    bills: DynamoDBScheduledBillRepository,
) -> None:
    bill = manage.declare(_declare()).bill

    assert bills.remove(user_id=STRANGER, bill_id=bill.id) is False
    assert bills.find(user_id=OWNER, bill_id=bill.id) is not None


def test_a_stranger_cannot_amend_a_bill_by_its_id(
    manage: ManageBillsUseCase,
) -> None:
    bill = manage.declare(_declare()).bill

    with pytest.raises(NoSuchBillError):
        manage.amend(AmendBillCommand(user_id=STRANGER, bill_id=bill.id, name="Otro"))


def test_a_listing_only_ever_holds_its_own_owners_bills(
    manage: ManageBillsUseCase,
    listing: ListBillsUseCase,
) -> None:
    manage.declare(_declare())
    manage.declare(_declare(user_id=STRANGER, name="Ajeno"))

    view = listing.execute(ListBillsQuery(user_id=OWNER, timezone=TIMEZONE))

    assert [summary.bill.name for summary in view.bills] == ["Gimnasio"]


def test_a_bill_cannot_be_declared_against_a_stranger_s_account(
    manage: ManageBillsUseCase,
    accounts: DynamoDBAccountRepository,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    theirs = _open_account(
        accounts,
        DynamoDBTransactionLedger(client=dynamodb_client, table_name=table),
        user_id=STRANGER,
    )

    with pytest.raises(AccountNotFoundError):
        manage.declare(_declare(account_id=theirs.id))


# ----------------------------------------------------------------------
# Nothing here is money
# ----------------------------------------------------------------------


def test_declaring_a_bill_writes_no_ledger_row_and_moves_no_balance(
    manage: ManageBillsUseCase,
    accounts: DynamoDBAccountRepository,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    """The rule the whole feature rests on, checked where it would actually
    break: against the real ledger, not against a fake that was never asked."""
    ledger = DynamoDBTransactionLedger(client=dynamodb_client, table_name=table)
    account = _open_account(accounts, ledger)

    manage.declare(_declare(account_id=account.id))

    after = accounts.find(user_id=OWNER, account_id=account.id)
    assert after is not None
    assert after.balance.signed_amount == Decimal(0)
    assert list(ledger.list_all(OWNER)) == []


def test_reading_the_month_writes_nothing_either(
    manage: ManageBillsUseCase,
    listing: ListBillsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    bill = manage.declare(_declare()).bill

    listing.execute(ListBillsQuery(user_id=OWNER, timezone=TIMEZONE))

    rows = dynamodb_client.query(
        TableName=table,
        KeyConditionExpression=f"{PARTITION_KEY} = :u",
        ExpressionAttributeValues={":u": {"S": str(OWNER.value)}},
    )["Items"]

    # The bill and nothing else: no occurrence was persisted on the way past.
    assert [row[SORT_KEY].get("S") for row in rows] == [f"{BILL_PREFIX}{bill.id.value}"]


def test_an_unknown_bill_id_is_refused_rather_than_creating_one(
    manage: ManageBillsUseCase,
) -> None:
    with pytest.raises(NoSuchBillError):
        manage.amend(AmendBillCommand(user_id=OWNER, bill_id=BillId.new(), name="X"))
