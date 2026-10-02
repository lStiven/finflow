"""The declared month against a real table.

Two things a fake cannot answer, and both of them decide whether the number on
the dashboard is somebody's own. **Where the row lands:** a plan has no id, so
its sort key is a constant, and a constant that turned out to collide with
another record type would overwrite it silently. **Whose it is:** the scoping
is the partition key, and a key is only real once something writes it.

The third is the round trip itself — money stored as a DynamoDB number and
read back as a `Decimal` has to come back the same money, cents included.
"""

from __future__ import annotations

from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.allowance import (
    DeclarePlanCommand,
    ManageMonthlyPlanUseCase,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    PLAN_KEY,
    SORT_KEY,
    DynamoDBMonthlyPlanRepository,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "financial-plan-test"

OWNER = UserId.from_string("22222222-2222-2222-2222-222222222222")
STRANGER = UserId.from_string("33333333-3333-3333-3333-333333333333")


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
def plans(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBMonthlyPlanRepository:
    return DynamoDBMonthlyPlanRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def manage(plans: DynamoDBMonthlyPlanRepository) -> ManageMonthlyPlanUseCase:
    return ManageMonthlyPlanUseCase(plans=plans)


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def declare(
    manage: ManageMonthlyPlanUseCase,
    *,
    user_id: UserId = OWNER,
    income: str = "5000000",
    savings: str = "1200000",
    currency: Currency = Currency.COP,
) -> None:
    manage.declare(
        DeclarePlanCommand(
            user_id=user_id,
            expected_income=money(income, currency),
            savings_target=money(savings, currency),
        ),
    )


def test_a_plan_survives_the_round_trip_to_the_cent(
    manage: ManageMonthlyPlanUseCase,
) -> None:
    declare(manage, income="5000000.55", savings="1200000.05")

    stored = manage.read(OWNER)

    assert stored is not None
    assert stored.expected_income.amount == Decimal("5000000.55")
    assert stored.savings_target.amount == Decimal("1200000.05")
    assert stored.currency is Currency.COP
    assert stored.spendable == Decimal("3800000.50")


def test_it_lands_at_the_one_place_a_plan_can_be(
    manage: ManageMonthlyPlanUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    """A constant sort key inside the owner's partition: nothing to list,
    nothing to page, and no key to guess."""
    declare(manage)

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": PLAN_KEY},
        },
    ).get("Item")

    assert item is not None
    assert item.get("expected_income", {}).get("N") == "5000000"


def test_restating_it_leaves_one_row_and_not_two(
    manage: ManageMonthlyPlanUseCase,
) -> None:
    declare(manage, income="5000000")
    declare(manage, income="3000000", savings="0")

    stored = manage.read(OWNER)

    assert stored is not None
    assert stored.expected_income.amount == Decimal("3000000")
    # The whole-item put replaced the row rather than merging into it, so the
    # old target is gone rather than standing against an income it was never
    # set against.
    assert stored.savings_target.amount == Decimal(0)


def test_another_currency_round_trips_as_itself(
    manage: ManageMonthlyPlanUseCase,
) -> None:
    declare(manage, income="1200", savings="200", currency=Currency.USD)

    stored = manage.read(OWNER)

    assert stored is not None
    assert stored.currency is Currency.USD


def test_one_persons_plan_is_unreachable_from_another(
    manage: ManageMonthlyPlanUseCase,
) -> None:
    declare(manage, user_id=OWNER, income="5000000")

    assert manage.read(STRANGER) is None


def test_forgetting_says_whether_there_was_anything_there(
    manage: ManageMonthlyPlanUseCase,
) -> None:
    declare(manage)

    assert manage.forget(OWNER) is True
    assert manage.forget(OWNER) is False
    assert manage.read(OWNER) is None
