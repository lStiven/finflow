"""Caps over HTTP, and the three status codes that carry meaning.

* **200 with empty lists** when nothing is capped. Not the 404 the plan
  endpoints answer: no plan means «you have not told me what your month looks
  like», while no caps is an ordinary state that reads as exactly what it is.
* **422 on a category that names nothing** when a cap is being set. An unknown
  value would store a ceiling on a category no spending is ever attributed to —
  a cap that stays green forever, which looks the same as one nobody has spent
  against.
* **204 and silence** on every delete, *including* a category that no longer
  exists. That is the one somebody most needs to be able to remove, and
  refusing it because the vocabulary no longer has it would leave a row nothing
  could reach.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.budgets import (
    ManageBudgetsUseCase,
    ReadBudgetsUseCase,
)
from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.queries import (
    SummarizeSpendingUseCase,
)
from personal_finance.contexts.financial.domain.budgets import (
    BudgetId,
    CategoryBudget,
    month_containing,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    MovementDirection,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_manage_budgets_use_case,
    get_merchant_directory,
    get_read_budgets_use_case,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
MIDDAY = dt.time(hour=17)

ARA = MerchantAttribution(
    merchant_id="aaaaaaaa-0000-0000-0000-000000000001",
    display_name="Ara",
    category="groceries",
    needs_review=False,
)

VOCABULARY = frozenset({"groceries", "restaurants", "transport", "custom:gatos"})


class InMemoryBudgets:
    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, BudgetId], CategoryBudget] = {}

    def list_for_month(
        self,
        *,
        user_id: UserId,
        month: str,
    ) -> Sequence[CategoryBudget]:
        return [
            budget
            for (owner, key), budget in self.rows.items()
            if owner == user_id and key.month in (None, month)
        ]

    def save(self, budget: CategoryBudget) -> None:
        self.rows[(budget.user_id, budget.id)] = budget

    def remove(self, *, user_id: UserId, budget_id: BudgetId) -> bool:
        return self.rows.pop((user_id, budget_id), None) is not None


class InMemoryDirectory:
    def __init__(self, *, vocabulary: frozenset[str] = VOCABULARY) -> None:
        self.vocabulary = vocabulary

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id

        return {
            counterparty: ARA
            for counterparty in counterparties
            if counterparty == "ARA"
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        del user_id

        return self.vocabulary

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        raise NotImplementedError


class InMemoryLedger:
    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def keep(self, movement: Transaction) -> Transaction:
        self.rows[movement.id.value] = movement

        return movement

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [row for row in self.rows.values() if row.user_id == user_id]

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        raise NotImplementedError

    def save(self, transaction: Transaction) -> None:
        raise NotImplementedError

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        raise NotImplementedError

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        raise NotImplementedError

    def find_many(
        self,
        *,
        user_id: UserId,
        movement_ids: Sequence[str],
    ) -> Mapping[str, Transaction]:
        raise NotImplementedError

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        raise NotImplementedError

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        raise NotImplementedError


class InMemoryAccounts:
    def list_by_user(self, user_id: UserId) -> list[Account]:
        del user_id

        return []

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        raise NotImplementedError

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        raise NotImplementedError

    def save(self, account: Account) -> None:
        raise NotImplementedError

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        raise NotImplementedError

    def overwrite_balance(self, account: Account) -> None:
        raise NotImplementedError

    def restate_balance(self, account: Account) -> None:
        raise NotImplementedError

    def add(self, account: Account) -> bool:
        raise NotImplementedError


@dataclasses.dataclass(frozen=True, slots=True)
class Wiring:
    client: TestClient
    budgets: InMemoryBudgets
    ledger: InMemoryLedger
    directory: InMemoryDirectory


@pytest.fixture
def wired() -> Wiring:
    budgets = InMemoryBudgets()
    ledger = InMemoryLedger()
    accounts = InMemoryAccounts()
    directory = InMemoryDirectory()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_merchant_directory] = lambda: directory
    app.dependency_overrides[get_manage_budgets_use_case] = lambda: (
        ManageBudgetsUseCase(budgets=budgets)
    )
    app.dependency_overrides[get_read_budgets_use_case] = lambda: ReadBudgetsUseCase(
        budgets=budgets,
        spending=SummarizeSpendingUseCase(
            ledger=ledger,
            accounts=accounts,
            merchants=directory,
        ),
        merchants=directory,
    )

    return Wiring(
        client=TestClient(app),
        budgets=budgets,
        ledger=ledger,
        directory=directory,
    )


def _set(client: TestClient, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    payload = {"category": "groceries", "limit": "600000", **overrides}
    response = client.put("/financial/budgets", json=payload)

    assert response.status_code == 200, response.text

    return response.json()


def _spend(wired: Wiring, amount: str, *, day: int = 5) -> None:
    now = dt.datetime.now(tz=dt.UTC).date()
    wired.ledger.keep(
        Transaction.enter_manually(
            user_id=USER_ID,
            direction=MovementDirection.OUTGOING,
            amount=Money(amount=Decimal(amount), currency=Currency.COP),
            occurred_at=PosixTime.from_datetime(
                dt.datetime.combine(now.replace(day=day), MIDDAY, tzinfo=dt.UTC),
            ),
            counterparty="ARA",
        ),
    )


class TestSetting:
    def test_a_cap_is_stated_and_read_back(self, wired: Wiring) -> None:
        body = _set(wired.client, warn_at=70)

        assert body["category"] == "groceries"
        assert body["limit"] == "600000"
        assert body["currency"] == "COP"
        assert body["month"] is None
        assert body["recurring"] is True
        assert body["warn_at"] == 70
        assert body["updated_at"] > 0

    def test_it_warns_at_eighty_unless_told_otherwise(self, wired: Wiring) -> None:
        assert _set(wired.client)["warn_at"] == 80

    def test_a_cap_for_one_month_says_so(self, wired: Wiring) -> None:
        body = _set(wired.client, month="2026-12", limit="900000")

        assert body["month"] == "2026-12"
        assert body["recurring"] is False

    def test_restating_replaces_rather_than_merges(self, wired: Wiring) -> None:
        _set(wired.client, warn_at=50)
        body = _set(wired.client, limit="900000")

        assert body["warn_at"] == 80
        assert len(wired.budgets.rows) == 1

    def test_a_cap_of_nothing_is_refused_by_the_payload(self, wired: Wiring) -> None:
        response = wired.client.put(
            "/financial/budgets",
            json={"category": "groceries", "limit": "0"},
        )

        assert response.status_code == 422

    def test_a_magnitude_the_table_cannot_hold_is_refused(self, wired: Wiring) -> None:
        """Unbounded, it reaches boto3 and comes back as a 500 with a stack
        trace instead of the refusal it is."""
        response = wired.client.put(
            "/financial/budgets",
            json={"category": "groceries", "limit": "1" + "0" * 30},
        )

        assert response.status_code == 422

    def test_a_warning_point_outside_the_cap_is_refused(self, wired: Wiring) -> None:
        for warn_at in (0, 100, -5, 101):
            response = wired.client.put(
                "/financial/budgets",
                json={"category": "groceries", "limit": "600000", "warn_at": warn_at},
            )

            assert response.status_code == 422, warn_at

    def test_a_month_written_any_other_way_is_refused(self, wired: Wiring) -> None:
        response = wired.client.put(
            "/financial/budgets",
            json={"category": "groceries", "limit": "600000", "month": "2026-9"},
        )

        assert response.status_code == 422

    def test_a_category_that_names_nothing_is_refused(self, wired: Wiring) -> None:
        """Half the vocabulary is whatever this person wrote for themselves, so
        a value that is real for one names nothing for another."""
        response = wired.client.put(
            "/financial/budgets",
            json={"category": "cryptocurrency", "limit": "600000"},
        )

        assert response.status_code == 422
        assert "cryptocurrency" in response.json()["detail"]
        assert wired.budgets.rows == {}

    def test_a_users_own_category_is_capped_like_any_other(
        self,
        wired: Wiring,
    ) -> None:
        assert _set(wired.client, category="custom:gatos")["category"] == "custom:gatos"

    def test_setting_a_cap_moves_no_money(self, wired: Wiring) -> None:
        """The ledger double raises on every write, so a cap that wrote one
        would fail here loudly rather than pass quietly."""
        _set(wired.client)

        assert wired.ledger.rows == {}


class TestReading:
    def test_nothing_capped_is_an_empty_answer_and_not_a_404(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.get("/financial/budgets")

        assert response.status_code == 200
        body = response.json()
        assert body["budgets"] == []
        assert body["totals"] == []
        assert body["month"] == month_containing(dt.datetime.now(tz=dt.UTC).date())

    def test_a_cap_comes_back_with_what_the_month_did_to_it(
        self,
        wired: Wiring,
    ) -> None:
        _set(wired.client, limit="600000")
        _spend(wired, "480000")

        body = wired.client.get("/financial/budgets").json()

        assert body["budgets"][0]["spent"] == "480000"
        assert body["budgets"][0]["remaining"] == "120000"
        assert body["budgets"][0]["state"] == "warning"
        assert body["budgets"][0]["retired"] is False

    def test_going_over_is_reported_negative_rather_than_floored(
        self,
        wired: Wiring,
    ) -> None:
        _set(wired.client, limit="100000")
        _spend(wired, "160000")

        body = wired.client.get("/financial/budgets").json()

        assert body["budgets"][0]["remaining"] == "-60000"
        assert body["budgets"][0]["state"] == "over"

    def test_the_totals_tally_the_three_states(self, wired: Wiring) -> None:
        _set(wired.client, limit="600000")
        _set(wired.client, category="restaurants", limit="400000")
        _spend(wired, "100000")

        totals = wired.client.get("/financial/budgets").json()["totals"]

        assert totals[0]["currency"] == "COP"
        assert totals[0]["limit"] == "1000000"
        assert totals[0]["spent"] == "100000"
        assert (totals[0]["ok"], totals[0]["warning"], totals[0]["over"]) == (2, 0, 0)

    def test_a_named_month_is_read_instead_of_todays(self, wired: Wiring) -> None:
        _set(wired.client, month="2026-12", limit="900000")

        body = wired.client.get(
            "/financial/budgets", params={"month": "2026-12"}
        ).json()

        assert body["month"] == "2026-12"
        assert body["since"] == "2026-12-01"
        assert body["until"] == "2026-12-31"
        assert body["budgets"][0]["limit"] == "900000"

    def test_a_month_written_any_other_way_is_refused(self, wired: Wiring) -> None:
        response = wired.client.get("/financial/budgets", params={"month": "dic-2026"})

        assert response.status_code == 422

    def test_an_unknown_timezone_is_refused_rather_than_falling_back(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.get(
            "/financial/budgets",
            params={"timezone": "Mars/Olympus"},
        )

        assert response.status_code == 400

    def test_a_cap_on_a_deleted_category_is_marked_and_the_screen_survives(
        self,
        wired: Wiring,
    ) -> None:
        _set(wired.client, category="custom:gatos", limit="200000")
        wired.directory.vocabulary = frozenset({"groceries"})

        response = wired.client.get("/financial/budgets")

        assert response.status_code == 200
        assert response.json()["budgets"][0]["retired"] is True

    def test_where_money_goes_uncapped_is_offered(self, wired: Wiring) -> None:
        _spend(wired, "500000")

        suggestions = wired.client.get("/financial/budgets").json()["suggestions"]

        assert suggestions == [
            {"category": "groceries", "currency": "COP", "spent": "500000"},
        ]

    def test_a_capped_category_is_not_offered(self, wired: Wiring) -> None:
        _set(wired.client, limit="600000")
        _spend(wired, "500000")

        assert wired.client.get("/financial/budgets").json()["suggestions"] == []


class TestForgetting:
    def test_dropping_a_cap_answers_nothing_at_all(self, wired: Wiring) -> None:
        _set(wired.client)

        response = wired.client.delete("/financial/budgets/groceries")

        assert response.status_code == 204
        assert wired.budgets.rows == {}

    def test_dropping_nothing_is_silent(self, wired: Wiring) -> None:
        """A 404 on the second press of a button somebody is unsure about is a
        worse answer than nothing."""
        assert wired.client.delete("/financial/budgets/groceries").status_code == 204

    def test_dropping_a_months_exception_leaves_the_recurring_cap(
        self,
        wired: Wiring,
    ) -> None:
        _set(wired.client, limit="600000")
        _set(wired.client, limit="900000", month="2026-12")

        response = wired.client.delete(
            "/financial/budgets/groceries",
            params={"month": "2026-12"},
        )

        assert response.status_code == 204
        assert [budget.limit.amount for budget in wired.budgets.rows.values()] == [
            Decimal("600000"),
        ]

    def test_dropping_the_recurring_cap_leaves_the_exception(
        self,
        wired: Wiring,
    ) -> None:
        _set(wired.client, limit="600000")
        _set(wired.client, limit="900000", month="2026-12")

        assert wired.client.delete("/financial/budgets/groceries").status_code == 204
        assert [budget.limit.amount for budget in wired.budgets.rows.values()] == [
            Decimal("900000"),
        ]

    def test_a_cap_on_a_deleted_category_can_still_be_dropped(
        self,
        wired: Wiring,
    ) -> None:
        """The whole reason this endpoint does not check the vocabulary: it is
        the cap somebody most needs to be able to remove."""
        _set(wired.client, category="custom:gatos", limit="200000")
        wired.directory.vocabulary = frozenset({"groceries"})

        response = wired.client.delete("/financial/budgets/custom:gatos")

        assert response.status_code == 204
        assert wired.budgets.rows == {}

    def test_a_value_that_could_not_be_a_caps_key_is_refused(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.delete(f"/financial/budgets/{'x' * 65}")

        assert response.status_code == 422

    def test_dropping_a_cap_moves_no_money(self, wired: Wiring) -> None:
        _set(wired.client)
        wired.client.delete("/financial/budgets/groceries")

        assert wired.ledger.rows == {}
