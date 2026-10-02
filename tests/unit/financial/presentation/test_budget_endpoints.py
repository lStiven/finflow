"""Budgets over HTTP, and the status codes that carry meaning.

* **201 on every declare**, and two declares of the same scope are two
  budgets. This used to be a `PUT` that answered 200 and left one row, because
  a cap *was* its category; a budget has an id now, and overlapping on purpose
  is the feature.
* **200 with empty lists** when nothing is capped. Not the 404 the plan
  endpoints answer: no plan means «you have not told me what your month looks
  like», while no budgets is an ordinary state that reads as exactly what it
  is.
* **422 on a category that names nothing.** An unknown value would store a
  ceiling no spending is ever attributed to — a budget that stays green
  forever, which looks the same as one nobody has spent against.
* **404 on somebody else's budget**, the same answer as one that does not
  exist: an id is a uuid somebody could paste, and a different code would
  confirm that another person's budget is real.
* **204 and silence** on every delete, including one whose categories no
  longer exist. That is the one somebody most needs to be able to remove.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any
import uuid

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
    Budget,
    BudgetId,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    MovementDirection,
)
from personal_finance.contexts.financial.presentation.http.router import (
    build_accounts,
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
        self.rows: dict[tuple[UserId, BudgetId], Budget] = {}

    def list_for_user(self, *, user_id: UserId) -> Sequence[Budget]:
        return [budget for (owner, _), budget in self.rows.items() if owner == user_id]

    def get(self, *, user_id: UserId, budget_id: BudgetId) -> Budget | None:
        return self.rows.get((user_id, budget_id))

    def save(self, budget: Budget) -> None:
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
    """Only what a budget scope asks of it: does this account exist."""

    def __init__(self) -> None:
        self.known: dict[AccountId, Account] = {}

    def list_by_user(self, user_id: UserId) -> list[Account]:
        del user_id

        return []

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        """Real, because the budgets router now asks: an account in a scope is
        checked against its owner, so a uuid somebody pasted is a 422 rather
        than a budget quietly scoped to nothing."""
        del user_id

        return self.known.get(account_id)

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
    accounts: InMemoryAccounts


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
    app.dependency_overrides[build_accounts] = lambda: accounts
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
        accounts=accounts,
    )


def _declare(client: TestClient, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    """Declare one budget and hand back what the API answered.

    The default is a ceiling on groceries, which is what most of these tests
    need; `categories=[]` is the one over everything.
    """
    payload = {
        "name": "Mercado",
        "limit": "600000",
        "categories": ["groceries"],
        **overrides,
    }
    response = client.post("/financial/budgets", json=payload)

    assert response.status_code == 201, response.text

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


class TestDeclaring:
    def test_a_budget_is_stated_and_read_back(self, wired: Wiring) -> None:
        body = _declare(wired.client, name="Mercado", limit="600000")

        assert body["name"] == "Mercado"
        assert body["limit"] == "600000"
        assert body["scope"]["categories"] == ["groceries"]
        assert body["recurring"] is True
        assert uuid.UUID(body["id"])

    def test_declaring_twice_leaves_two_budgets(self, wired: Wiring) -> None:
        """The reversal, over HTTP. A `PUT` with no id could not say this."""
        first = _declare(wired.client, name="Restaurantes", categories=["restaurants"])
        second = _declare(wired.client, name="Salidas", categories=["restaurants"])

        assert first["id"] != second["id"]
        assert len(wired.budgets.rows) == 2

    def test_a_budget_over_everything_needs_no_category(self, wired: Wiring) -> None:
        body = _declare(
            wired.client, name="Todo el mes", categories=[], limit="3000000"
        )

        assert body["scope"]["categories"] == []
        assert body["scope"]["total"] is True

    def test_a_scope_can_gather_several_categories(self, wired: Wiring) -> None:
        body = _declare(
            wired.client,
            name="Salidas",
            categories=["restaurants", "transport"],
        )

        assert body["scope"]["categories"] == ["restaurants", "transport"]
        assert body["scope"]["total"] is False

    def test_a_scope_can_be_narrowed_to_an_account(self, wired: Wiring) -> None:
        # `card.value`, never `str(card)`: `AccountId` defines no `__str__`,
        # so the latter is the dataclass repr. The same trap the integration
        # test caught in `budget_to_item`.
        card = str(_an_account(wired).value)

        body = _declare(wired.client, categories=["groceries"], accounts=[card])

        assert body["scope"]["accounts"] == [card]
        assert body["scope"]["every_account"] is False

    def test_an_account_that_is_not_yours_is_refused(self, wired: Wiring) -> None:
        """A uuid is something somebody could paste. Unchecked, it would store
        a budget nothing is ever attributed to — a ceiling that reads «no has
        gastado nada aquí» forever."""
        response = wired.client.post(
            "/financial/budgets",
            json={
                "name": "Mercado",
                "limit": "600000",
                "accounts": [str(uuid.uuid4())],
            },
        )

        assert response.status_code == 422
        assert wired.budgets.rows == {}

    def test_it_warns_at_eighty_unless_told_otherwise(self, wired: Wiring) -> None:
        assert _declare(wired.client)["warn_at"] == 80

    def test_a_budget_for_one_month_says_so(self, wired: Wiring) -> None:
        body = _declare(wired.client, month="2026-12")

        assert body["month"] == "2026-12"
        assert body["recurring"] is False

    def test_an_icon_is_carried_through(self, wired: Wiring) -> None:
        assert _declare(wired.client, icon="shopping-bag")["icon"] == "shopping-bag"

    def test_an_icon_that_is_not_a_slug_is_refused(self, wired: Wiring) -> None:
        """400 and not 422: the shape of an icon is the domain's rule, and the
        payload deliberately does not carry a second copy of it."""
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "Mercado", "limit": "600000", "icon": "Shopping Bag"},
        )

        assert response.status_code == 400

    def test_a_budget_with_no_name_is_refused(self, wired: Wiring) -> None:
        """New, and forced by the scope: «Salidas» is not derivable from
        restaurants, bars and delivery."""
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "", "limit": "600000"},
        )

        assert response.status_code == 422

    def test_a_budget_of_nothing_is_refused_by_the_payload(self, wired: Wiring) -> None:
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "Mercado", "limit": "0"},
        )

        assert response.status_code == 422

    def test_a_magnitude_the_table_cannot_hold_is_refused(self, wired: Wiring) -> None:
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "Mercado", "limit": "1" + "0" * 30},
        )

        assert response.status_code == 422

    def test_a_warning_point_outside_the_cap_is_refused(self, wired: Wiring) -> None:
        for warn_at in (0, 100):
            response = wired.client.post(
                "/financial/budgets",
                json={"name": "Mercado", "limit": "600000", "warn_at": warn_at},
            )

            assert response.status_code == 422

    def test_a_month_written_any_other_way_is_refused(self, wired: Wiring) -> None:
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "Mercado", "limit": "600000", "month": "2026-9"},
        )

        assert response.status_code == 422

    def test_a_category_that_names_nothing_is_refused(self, wired: Wiring) -> None:
        """A ceiling on a category no spending is attributed to stays green
        forever, which looks the same as one nobody has spent against."""
        response = wired.client.post(
            "/financial/budgets",
            json={"name": "Mercado", "limit": "600000", "categories": ["invented"]},
        )

        assert response.status_code == 422
        assert wired.budgets.rows == {}

    def test_one_unknown_category_refuses_the_whole_scope(self, wired: Wiring) -> None:
        response = wired.client.post(
            "/financial/budgets",
            json={
                "name": "Salidas",
                "limit": "600000",
                "categories": ["restaurants", "invented"],
            },
        )

        assert response.status_code == 422
        assert wired.budgets.rows == {}

    def test_a_users_own_category_is_watched_like_any_other(
        self,
        wired: Wiring,
    ) -> None:
        body = _declare(wired.client, categories=["custom:gatos"])

        assert body["scope"]["categories"] == ["custom:gatos"]

    def test_declaring_a_budget_moves_no_money(self, wired: Wiring) -> None:
        """A ceiling is a statement, not a transaction."""
        _declare(wired.client)

        assert wired.ledger.rows == {}


class TestAmending:
    def test_amending_keeps_the_id_and_replaces_the_rest(self, wired: Wiring) -> None:
        declared = _declare(wired.client, name="Mercado", limit="600000")

        response = wired.client.put(
            f"/financial/budgets/{declared['id']}",
            json={
                "name": "Mercado y aseo",
                "limit": "900000",
                "categories": ["groceries"],
                "warn_at": 70,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["id"] == declared["id"]
        assert response.json()["name"] == "Mercado y aseo"
        assert response.json()["warn_at"] == 70
        assert len(wired.budgets.rows) == 1

    def test_amending_replaces_rather_than_merges(self, wired: Wiring) -> None:
        """A `PUT`: a ceiling and the point it warns at are one statement, and
        half an update leaves a warning standing against a ceiling it was never
        set against."""
        declared = _declare(wired.client, limit="600000", warn_at=50)

        wired.client.put(
            f"/financial/budgets/{declared['id']}",
            json={"name": "Mercado", "limit": "900000", "categories": ["groceries"]},
        )

        assert (
            wired.budgets.rows[(USER_ID, BudgetId.from_string(declared["id"]))].warn_at
            == 80
        )

    def test_amending_something_that_is_not_there_answers_404(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.put(
            f"/financial/budgets/{uuid.uuid4()}",
            json={"name": "Mercado", "limit": "600000"},
        )

        assert response.status_code == 404

    def test_an_id_that_is_not_a_uuid_is_refused(self, wired: Wiring) -> None:
        response = wired.client.put(
            "/financial/budgets/not-a-uuid",
            json={"name": "Mercado", "limit": "600000"},
        )

        assert response.status_code == 422


class TestReading:
    def test_nothing_capped_is_an_empty_answer_and_not_a_404(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.get("/financial/budgets")

        assert response.status_code == 200
        assert response.json()["budgets"] == []
        assert response.json()["totals"] == []

    def test_a_budget_comes_back_with_what_the_month_did_to_it(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, limit="600000")
        _spend(wired, "150000")

        line = wired.client.get("/financial/budgets").json()["budgets"][0]

        assert line["spent"] == "150000"
        assert line["remaining"] == "450000"
        assert line["state"] == "ok"

    def test_a_budget_over_everything_counts_every_category(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, name="Todo el mes", categories=[], limit="600000")
        _spend(wired, "150000")

        line = wired.client.get("/financial/budgets").json()["budgets"][0]

        assert line["spent"] == "150000"
        assert line["scope"]["total"] is True

    def test_going_over_is_reported_negative_rather_than_floored(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, limit="100000")
        _spend(wired, "150000")

        line = wired.client.get("/financial/budgets").json()["budgets"][0]

        assert line["state"] == "over"
        assert line["remaining"] == "-50000"

    def test_the_totals_tally_the_three_states(self, wired: Wiring) -> None:
        _declare(wired.client, name="Mercado", categories=["groceries"], limit="100000")
        _declare(
            wired.client,
            name="Restaurantes",
            categories=["restaurants"],
            limit="500000",
        )
        _spend(wired, "150000")

        totals = wired.client.get("/financial/budgets").json()["totals"][0]

        assert (totals["ok"], totals["warning"], totals["over"]) == (1, 0, 1)
        assert totals["limit"] == "600000"

    def test_a_named_month_is_read_instead_of_todays(self, wired: Wiring) -> None:
        _declare(wired.client, month="2026-12")

        body = wired.client.get("/financial/budgets?month=2026-12").json()

        assert body["month"] == "2026-12"
        assert len(body["budgets"]) == 1

    def test_a_months_budget_is_read_beside_the_recurring_one(
        self,
        wired: Wiring,
    ) -> None:
        """No shadowing any more: both apply and both are shown."""
        _declare(wired.client, name="Restaurantes", limit="600000")
        _declare(wired.client, name="Diciembre", limit="900000", month="2026-12")

        body = wired.client.get("/financial/budgets?month=2026-12").json()

        assert sorted(line["limit"] for line in body["budgets"]) == [
            "600000",
            "900000",
        ]

    def test_a_month_written_any_other_way_is_refused(self, wired: Wiring) -> None:
        assert wired.client.get("/financial/budgets?month=2026-9").status_code == 422

    def test_an_unknown_timezone_is_refused_rather_than_falling_back(
        self,
        wired: Wiring,
    ) -> None:
        response = wired.client.get("/financial/budgets?timezone=Mars/Olympus")

        assert response.status_code == 400

    def test_a_budget_on_a_deleted_category_is_marked_and_the_screen_survives(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, categories=["groceries"])
        wired.directory.vocabulary = frozenset({"restaurants"})

        line = wired.client.get("/financial/budgets").json()["budgets"][0]

        assert line["retired"] is True
        assert line["missing"] == ["groceries"]

    def test_losing_one_of_several_categories_marks_it_not_the_budget(
        self,
        wired: Wiring,
    ) -> None:
        _declare(wired.client, name="Salidas", categories=["groceries", "restaurants"])
        wired.directory.vocabulary = frozenset({"restaurants"})

        line = wired.client.get("/financial/budgets").json()["budgets"][0]

        assert line["retired"] is False
        assert line["missing"] == ["groceries"]

    def test_where_money_goes_uncapped_is_offered(self, wired: Wiring) -> None:
        _spend(wired, "300000")

        body = wired.client.get("/financial/budgets").json()

        assert [offer["category"] for offer in body["suggestions"]] == ["groceries"]

    def test_a_capped_category_is_not_offered(self, wired: Wiring) -> None:
        _declare(wired.client, categories=["groceries"])
        _spend(wired, "300000")

        assert wired.client.get("/financial/budgets").json()["suggestions"] == []


class TestForgetting:
    def test_dropping_a_budget_answers_nothing_at_all(self, wired: Wiring) -> None:
        declared = _declare(wired.client)

        response = wired.client.delete(f"/financial/budgets/{declared['id']}")

        assert response.status_code == 204
        assert wired.budgets.rows == {}

    def test_dropping_nothing_is_silent(self, wired: Wiring) -> None:
        """A 404 on the second press of a button somebody is unsure about is a
        worse answer than nothing."""
        response = wired.client.delete(f"/financial/budgets/{uuid.uuid4()}")

        assert response.status_code == 204

    def test_dropping_one_leaves_the_others(self, wired: Wiring) -> None:
        usual = _declare(wired.client, name="Restaurantes", limit="600000")
        december = _declare(
            wired.client,
            name="Diciembre",
            limit="900000",
            month="2026-12",
        )

        wired.client.delete(f"/financial/budgets/{december['id']}")

        assert list(wired.budgets.rows) == [
            (USER_ID, BudgetId.from_string(usual["id"])),
        ]

    def test_a_budget_on_a_deleted_category_can_still_be_dropped(
        self,
        wired: Wiring,
    ) -> None:
        """The one somebody most needs to be able to remove. Nothing about the
        vocabulary is consulted on the way out."""
        declared = _declare(wired.client, categories=["groceries"])
        wired.directory.vocabulary = frozenset()

        response = wired.client.delete(f"/financial/budgets/{declared['id']}")

        assert response.status_code == 204
        assert wired.budgets.rows == {}

    def test_an_id_that_is_not_a_uuid_is_refused(self, wired: Wiring) -> None:
        assert wired.client.delete("/financial/budgets/not-a-uuid").status_code == 422

    def test_dropping_a_budget_moves_no_money(self, wired: Wiring) -> None:
        declared = _declare(wired.client)
        _spend(wired, "150000")
        before = len(wired.ledger.rows)

        wired.client.delete(f"/financial/budgets/{declared['id']}")

        assert len(wired.ledger.rows) == before


def _an_account(wired: Wiring) -> AccountId:
    """One account this person really has, for a scope to name."""
    account = Account.open(
        user_id=USER_ID,
        name="Tarjeta",
        kind=AccountKind.CREDIT_CARD,
        currency=Currency.COP,
        opened_at=PosixTime.now(),
    )
    wired.accounts.known[account.id] = account

    return account.id
