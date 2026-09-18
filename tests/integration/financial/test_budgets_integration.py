"""Spending caps against a real table.

Four things a fake cannot answer, and each one decides whether the traffic
light on somebody's screen is about their own money.

**Where the row lands.** A cap has no generated id: its sort key is built from
the month and the category, and a key that collided with another record type
would overwrite it silently. `BILL#` and `BUDGET#` share a letter, and
`_query_prefix` is a raw `begins_with` with no type attribute to fall back on.

**That the month comes first.** Reading one month is two bounded `begins_with`
queries, and that only works because the month is the segment before the
category. Keyed the other way round, "every cap of September" would be a read
of every cap ever declared.

**Whose it is.** The scoping is the partition key, and a key is only real once
something writes it.

**And the round trip.** A cap stored as a DynamoDB number and read back as a
`Decimal` has to come back the same money, cents included — a cap compared to
the cent is the whole point of never letting a float near it.
"""

from __future__ import annotations

from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.budgets import (
    ManageBudgetsUseCase,
    SetBudgetCommand,
)
from personal_finance.contexts.financial.domain.budgets import BudgetId
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    BILL_PREFIX,
    BUDGET_PREFIX,
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBCategoryBudgetRepository,
    budget_sort_value,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "financial-budgets-test"

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
def budgets(
    dynamodb_client: DynamoDBClient,
    table: str,
) -> DynamoDBCategoryBudgetRepository:
    return DynamoDBCategoryBudgetRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def manage(budgets: DynamoDBCategoryBudgetRepository) -> ManageBudgetsUseCase:
    return ManageBudgetsUseCase(budgets=budgets)


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def declare(
    manage: ManageBudgetsUseCase,
    *,
    user_id: UserId = OWNER,
    category: str = "groceries",
    limit: str = "600000",
    currency: Currency = Currency.COP,
    month: str | None = None,
    warn_at: int = 80,
) -> None:
    manage.declare(
        SetBudgetCommand(
            user_id=user_id,
            category=category,
            limit=money(limit, currency),
            month=month,
            warn_at=warn_at,
        ),
    )


def test_a_cap_survives_the_round_trip_to_the_cent(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, limit="600000.55", warn_at=65)

    stored = budgets.list_for_month(user_id=OWNER, month="2026-09")

    assert len(stored) == 1
    assert stored[0].limit.amount == Decimal("600000.55")
    assert stored[0].warn_at == 65
    assert stored[0].currency is Currency.COP
    assert stored[0].category == "groceries"
    assert stored[0].month is None
    assert stored[0].recurring is True
    assert stored[0].updated_at.as_epoch_seconds() > 0


def test_it_lands_under_the_month_and_then_the_category(
    manage: ManageBudgetsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    """The order of the two segments is what makes reading one month cheap."""
    declare(manage, category="restaurants", month="2026-12", limit="900000")

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": "BUDGET#2026-12#restaurants"},
        },
    ).get("Item")

    assert item is not None
    assert item.get("limit", {}).get("N") == "900000"
    # Written as attributes as well as into the key, so reading a cap back never
    # means splitting a string on a separator.
    assert item.get("category", {}).get("S") == "restaurants"
    assert item.get("month", {}).get("S") == "2026-12"


def test_a_recurring_cap_lands_under_every_rather_than_a_month(
    manage: ManageBudgetsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    declare(manage, category="groceries")

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": "BUDGET#EVERY#groceries"},
        },
    ).get("Item")

    assert item is not None
    assert item.get("month", {}).get("S") == "EVERY"


def test_the_budget_prefix_cannot_be_reached_by_the_bills_one(
    manage: ManageBudgetsUseCase,
) -> None:
    """`BILL#` and `BUDGET#` share a letter and the listing is a raw
    `begins_with` with no record type to filter on."""
    assert not budget_sort_value(BudgetId(category="groceries")).startswith(BILL_PREFIX)
    assert budget_sort_value(BudgetId(category="groceries")).startswith(BUDGET_PREFIX)


def test_reading_a_month_brings_the_recurring_caps_and_that_months_own(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="groceries", limit="600000")
    declare(manage, category="groceries", limit="900000", month="2026-12")
    declare(manage, category="travel", limit="2000000", month="2026-07")

    december = budgets.list_for_month(user_id=OWNER, month="2026-12")

    assert sorted((cap.category, cap.month or "") for cap in december) == [
        ("groceries", ""),
        ("groceries", "2026-12"),
    ]


def test_another_months_exception_stays_out_of_this_one(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="travel", limit="2000000", month="2026-07")

    assert budgets.list_for_month(user_id=OWNER, month="2026-08") == []


def test_restating_a_cap_leaves_one_row_and_not_two(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="groceries", limit="600000", warn_at=50)
    declare(manage, category="groceries", limit="900000")

    stored = budgets.list_for_month(user_id=OWNER, month="2026-09")

    assert len(stored) == 1
    assert stored[0].limit.amount == Decimal("900000")
    # The whole-item put replaced the row rather than merging into it, so the
    # old warning point is gone rather than standing against a ceiling it was
    # never set against.
    assert stored[0].warn_at == 80


def test_a_recurring_cap_and_an_exception_are_two_rows(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="groceries", limit="600000")
    declare(manage, category="groceries", limit="900000", month="2026-12")

    assert len(budgets.list_for_month(user_id=OWNER, month="2026-12")) == 2


def test_another_currency_round_trips_as_itself(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="travel", limit="400", currency=Currency.USD)

    stored = budgets.list_for_month(user_id=OWNER, month="2026-09")

    assert stored[0].currency is Currency.USD
    assert stored[0].limit.amount == Decimal("400")


def test_a_users_own_category_survives_its_colon(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    """A `custom:` key is Merchant's spelling and Financial stores it whole."""
    key = "custom:9f1e4b2c8a7d6e5f4a3b2c1d0e9f8a7b"
    declare(manage, category=key, limit="200000")

    stored = budgets.list_for_month(user_id=OWNER, month="2026-09")

    assert stored[0].category == key


def test_one_persons_caps_are_unreachable_from_another(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, user_id=OWNER, category="groceries", limit="600000")

    assert budgets.list_for_month(user_id=STRANGER, month="2026-09") == []


def test_one_persons_cap_is_not_anothers_to_drop(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, user_id=OWNER, category="groceries", limit="600000")

    assert manage.forget(user_id=STRANGER, category="groceries") is False
    assert len(budgets.list_for_month(user_id=OWNER, month="2026-09")) == 1


def test_forgetting_says_whether_there_was_anything_there(
    manage: ManageBudgetsUseCase,
) -> None:
    declare(manage, category="groceries")

    assert manage.forget(user_id=OWNER, category="groceries") is True
    assert manage.forget(user_id=OWNER, category="groceries") is False


def test_dropping_an_exception_leaves_the_recurring_cap_on_the_table(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBCategoryBudgetRepository,
) -> None:
    declare(manage, category="groceries", limit="600000")
    declare(manage, category="groceries", limit="900000", month="2026-12")

    assert manage.forget(user_id=OWNER, category="groceries", month="2026-12") is True

    stored = budgets.list_for_month(user_id=OWNER, month="2026-12")
    assert [(cap.month, cap.limit.amount) for cap in stored] == [
        (None, Decimal("600000")),
    ]
