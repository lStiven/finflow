"""Spending budgets against a real table.

Five things a fake cannot answer, and each one decides whether the traffic
light on somebody's screen is about their own money.

**Where the row lands.** The sort key is `BUDGET#<id>` now, and a key that
collided with another record type would overwrite it silently. `BILL#` and
`BUDGET#` share a letter, and `_query_prefix` is a raw `begins_with` with no
type attribute to fall back on.

**That the scope survives storage.** DynamoDB has no empty string set, so «every
category» is stored as an *absent attribute* rather than as `SS: []`. That is
the one encoding a unit test would never catch: an in-memory double happily
holds an empty set, and the real client refuses to write one.

**Whose it is.** The scoping is the partition key, and a key is only real once
something writes it.

**That amending moves nothing.** With a generated id, restating a budget has to
land on the same row — the failure it replaced was writing a second row under a
new identity and leaving the first unreachable.

**And the round trip.** A ceiling stored as a DynamoDB number and read back as a
`Decimal` has to come back the same money, cents included — a ceiling compared
to the cent is the whole point of never letting a float near it.
"""

from __future__ import annotations

from decimal import Decimal

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.financial.application.budgets import (
    AmendBudgetCommand,
    DeclareBudgetCommand,
    ManageBudgetsUseCase,
)
from personal_finance.contexts.financial.domain.budgets import (
    Budget,
    BudgetId,
    BudgetScope,
)
from personal_finance.contexts.financial.domain.value_objects import AccountId
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    BILL_PREFIX,
    BUDGET_PREFIX,
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBBudgetRepository,
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
) -> DynamoDBBudgetRepository:
    return DynamoDBBudgetRepository(client=dynamodb_client, table_name=table)


@pytest.fixture
def manage(budgets: DynamoDBBudgetRepository) -> ManageBudgetsUseCase:
    return ManageBudgetsUseCase(budgets=budgets)


def money(amount: str, currency: Currency = Currency.COP) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


def declare(
    manage: ManageBudgetsUseCase,
    *,
    user_id: UserId = OWNER,
    name: str = "Mercado",
    categories: frozenset[str] | None = frozenset({"groceries"}),
    accounts: frozenset[AccountId] | None = None,
    limit: str = "600000",
    currency: Currency = Currency.COP,
    icon: str = "",
    month: str | None = None,
    warn_at: int = 80,
) -> Budget:
    return manage.declare(
        DeclareBudgetCommand(
            user_id=user_id,
            name=name,
            limit=money(limit, currency),
            scope=BudgetScope.of(categories=categories, accounts=accounts),
            icon=icon,
            month=month,
            warn_at=warn_at,
        ),
    )


def test_a_budget_survives_the_round_trip_to_the_cent(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, limit="600000.45", warn_at=65, icon="shopping-bag")

    stored = budgets.list_for_user(user_id=OWNER)

    assert len(stored) == 1
    assert stored[0].limit == money("600000.45")
    assert stored[0].warn_at == 65
    assert stored[0].icon == "shopping-bag"
    assert stored[0].name == "Mercado"


def test_it_lands_under_the_budget_prefix_and_its_id(
    manage: ManageBudgetsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    budget = declare(manage)

    item = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": f"{BUDGET_PREFIX}{budget.id}"},
        },
    )

    assert "Item" in item


def test_the_budget_prefix_cannot_be_reached_by_the_bills_one() -> None:
    """`BILL#` and `BUDGET#` share a letter, and the query is a raw
    `begins_with` with no type attribute to fall back on."""
    key = budget_sort_value(BudgetId.new())

    assert not key.startswith(BILL_PREFIX)
    assert key.startswith(BUDGET_PREFIX)


def test_every_category_is_stored_as_an_absent_attribute(
    manage: ManageBudgetsUseCase,
    dynamodb_client: DynamoDBClient,
    table: str,
) -> None:
    """The encoding a unit test cannot catch: DynamoDB refuses an empty string
    set, so «todo el mes» cannot be written as `SS: []`."""
    budget = declare(manage, name="Todo el mes", categories=None, limit="3000000")

    response = dynamodb_client.get_item(
        TableName=table,
        Key={
            PARTITION_KEY: {"S": str(OWNER.value)},
            SORT_KEY: {"S": f"{BUDGET_PREFIX}{budget.id}"},
        },
    )

    assert "Item" in response
    assert "categories" not in response["Item"]
    assert "accounts" not in response["Item"]


def test_a_budget_over_everything_reads_back_as_such(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, categories=None)

    stored = budgets.list_for_user(user_id=OWNER)

    assert stored[0].scope.total is True
    assert stored[0].scope.every_account is True


def test_a_scope_of_several_categories_round_trips(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, name="Salidas", categories=frozenset({"restaurants", "bars"}))

    stored = budgets.list_for_user(user_id=OWNER)

    assert stored[0].scope.categories == frozenset({"restaurants", "bars"})


def test_an_account_scope_round_trips(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    card = AccountId.new()
    declare(manage, accounts=frozenset({card}))

    stored = budgets.list_for_user(user_id=OWNER)

    assert stored[0].scope.accounts == frozenset({card})
    assert stored[0].scope.every_account is False


def test_a_recurring_budget_and_a_months_own_are_two_rows(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, name="Restaurantes", limit="600000")
    declare(manage, name="Diciembre", limit="900000", month="2026-12")

    stored = budgets.list_for_user(user_id=OWNER)

    assert len(stored) == 2
    assert {budget.month for budget in stored} == {None, "2026-12"}


def test_declaring_twice_leaves_two_rows(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    """The reversal, against the table. Two budgets over the same category
    used to be one row overwriting itself."""
    declare(manage, name="Uno")
    declare(manage, name="Otro")

    assert len(budgets.list_for_user(user_id=OWNER)) == 2


def test_amending_lands_on_the_same_row(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    """The failure this replaced: writing a second row under a new identity
    and leaving the first one unreachable."""
    budget = declare(manage, name="Mercado", limit="600000")

    manage.amend(
        AmendBudgetCommand(
            user_id=OWNER,
            budget_id=budget.id,
            name="Mercado y aseo",
            limit=money("900000"),
            scope=BudgetScope.of(categories=frozenset({"groceries"})),
        ),
    )

    stored = budgets.list_for_user(user_id=OWNER)
    assert len(stored) == 1
    assert stored[0].name == "Mercado y aseo"
    assert stored[0].limit == money("900000")


def test_amending_can_widen_a_scope_to_everything(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    """Going from a named category to none has to *remove* the stored
    attribute, not leave the old one behind — a whole-item put is what makes
    that true, and only the table can prove it."""
    budget = declare(manage, categories=frozenset({"groceries"}))

    manage.amend(
        AmendBudgetCommand(
            user_id=OWNER,
            budget_id=budget.id,
            name="Todo el mes",
            limit=money("3000000"),
            scope=BudgetScope.everything(),
        ),
    )

    assert budgets.list_for_user(user_id=OWNER)[0].scope.total is True


def test_another_currency_round_trips_as_itself(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, limit="500", currency=Currency.USD)

    stored = budgets.list_for_user(user_id=OWNER)

    assert stored[0].currency is Currency.USD


def test_a_users_own_category_survives_its_colon(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, categories=frozenset({"custom:gatos"}))

    stored = budgets.list_for_user(user_id=OWNER)

    assert stored[0].scope.categories == frozenset({"custom:gatos"})


def test_one_persons_budgets_are_unreachable_from_another(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    declare(manage, user_id=OWNER)

    assert budgets.list_for_user(user_id=STRANGER) == []


def test_one_persons_budget_is_not_anothers_to_read(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    """A budget id is a uuid somebody could paste. Both halves of the key,
    always."""
    budget = declare(manage, user_id=OWNER)

    assert budgets.get(user_id=STRANGER, budget_id=budget.id) is None
    assert budgets.get(user_id=OWNER, budget_id=budget.id) is not None


def test_one_persons_budget_is_not_anothers_to_drop(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    budget = declare(manage, user_id=OWNER)

    assert budgets.remove(user_id=STRANGER, budget_id=budget.id) is False
    assert len(budgets.list_for_user(user_id=OWNER)) == 1


def test_forgetting_says_whether_there_was_anything_there(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    budget = declare(manage)

    assert manage.forget(user_id=OWNER, budget_id=budget.id) is True
    assert manage.forget(user_id=OWNER, budget_id=budget.id) is False


def test_dropping_one_leaves_the_others_on_the_table(
    manage: ManageBudgetsUseCase,
    budgets: DynamoDBBudgetRepository,
) -> None:
    usual = declare(manage, name="Restaurantes", limit="600000")
    december = declare(manage, name="Diciembre", limit="900000", month="2026-12")

    manage.forget(user_id=OWNER, budget_id=december.id)

    stored = budgets.list_for_user(user_id=OWNER)
    assert [budget.id for budget in stored] == [usual.id]
