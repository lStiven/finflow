"""`GET /alerts/inbox`: the owner's alerts, as facts, newest first."""

from __future__ import annotations

from collections.abc import Sequence
import datetime as dt
from decimal import Decimal
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.alerts.application.inbox import (
    InboxEntry,
    InboxKind,
    ListInboxUseCase,
    ManageInboxUseCase,
)
from personal_finance.contexts.alerts.application.messages import (
    BudgetStanding,
    BudgetState,
    CategoryRise,
    MovementAlert,
    MovementDirection,
    MovementOrigin,
    WeeklySummary,
)
from personal_finance.contexts.alerts.presentation.http.inbox import (
    get_list_inbox_use_case,
    get_manage_inbox_use_case,
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


USER = UserId.new()
OTHER = UserId.new()


class Inbox:
    def __init__(self, entries: Sequence[InboxEntry]) -> None:
        self.entries = list(entries)
        self.asked: list[tuple[UserId, int]] = []

    def record(self, entry: InboxEntry) -> None:
        self.entries.append(entry)

    def dismiss(self, *, user_id: UserId, entry_id: uuid.UUID) -> bool:
        before = len(self.entries)
        self.entries = [
            e
            for e in self.entries
            if not (e.user_id == user_id and e.entry_id == entry_id)
        ]
        return len(self.entries) < before

    def dismiss_all(self, *, user_id: UserId) -> int:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.user_id != user_id]
        return before - len(self.entries)

    def recent(self, *, user_id: UserId, limit: int) -> Sequence[InboxEntry]:
        self.asked.append((user_id, limit))
        mine = [entry for entry in self.entries if entry.user_id == user_id]
        return sorted(
            mine, key=lambda e: e.created_at.as_epoch_seconds(), reverse=True
        )[:limit]


def _movement(user: UserId = USER, at: int = 1_790_000_000) -> InboxEntry:
    return InboxEntry(
        user_id=user,
        entry_id=uuid.uuid4(),
        created_at=PosixTime.from_epoch_seconds(at),
        kind=InboxKind.MOVEMENT,
        movement=MovementAlert(
            amount=Money(amount=Decimal("84300.50"), currency=Currency.COP),
            direction=MovementDirection.OUTGOING,
            counterparty="<b>COMPRA</b> EN EXITO",
            bank="Bancolombia",
            occurred_at=PosixTime.from_epoch_seconds(at - 30),
            origin=MovementOrigin.BANK_ALERT,
            unassigned=False,
            movement_id="4f2a9c",
            budgets=(
                BudgetStanding(
                    name="Mercado",
                    currency=Currency.COP,
                    limit=Decimal("600000"),
                    spent=Decimal("630000"),
                    remaining=Decimal("-30000"),
                    state=BudgetState.OVER,
                ),
            ),
        ),
    )


def _summary(at: int = 1_790_100_000) -> InboxEntry:
    return InboxEntry(
        user_id=USER,
        entry_id=uuid.uuid4(),
        created_at=PosixTime.from_epoch_seconds(at),
        kind=InboxKind.WEEKLY_SUMMARY,
        summary=WeeklySummary(
            week_start=dt.date(2026, 9, 21),
            week_end=dt.date(2026, 9, 27),
            currency=Currency.COP,
            spent=Decimal("820000"),
            movements=12,
            typical=None,
            rise=CategoryRise(
                category="restaurants",
                label="Restaurants",
                spent=Decimal("240000"),
                typical=Decimal("155000"),
            ),
        ),
    )


@pytest.fixture
def inbox() -> Inbox:
    return Inbox([_movement(), _summary(), _movement(OTHER)])


@pytest.fixture
def client(inbox: Inbox) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[get_list_inbox_use_case] = lambda: ListInboxUseCase(
        inbox=inbox,
    )
    app.dependency_overrides[get_manage_inbox_use_case] = lambda: ManageInboxUseCase(
        inbox=inbox,
    )

    return TestClient(app)


def test_the_owners_alerts_come_back_newest_first_as_facts(client: TestClient) -> None:
    response = client.get("/alerts/inbox")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    summary, movement = response.json()["entries"]

    assert summary["kind"] == "weekly_summary"
    assert summary["movement"] is None
    assert summary["summary"]["spent"] == "820000"
    assert summary["summary"]["typical"] is None
    assert summary["summary"]["rise"]["category"] == "restaurants"

    assert movement["kind"] == "movement"
    assert movement["movement"]["amount"] == "84300.50"
    assert movement["movement"]["movement_id"] == "4f2a9c"
    # The bank's text is handed over as it came; the screen draws it as text.
    assert movement["movement"]["counterparty"] == "<b>COMPRA</b> EN EXITO"
    assert movement["movement"]["budgets"] == [
        {
            "name": "Mercado",
            "currency": "COP",
            "limit": "600000",
            "spent": "630000",
            "remaining": "-30000",
            "state": "over",
        },
    ]


def test_nobody_elses_alerts_are_listed(client: TestClient, inbox: Inbox) -> None:
    client.get("/alerts/inbox")

    assert [user for user, _ in inbox.asked] == [USER]


def test_the_page_size_is_bounded(client: TestClient) -> None:
    assert client.get("/alerts/inbox", params={"limit": 0}).status_code == 422
    assert client.get("/alerts/inbox", params={"limit": 51}).status_code == 422
    assert len(client.get("/alerts/inbox", params={"limit": 1}).json()["entries"]) == 1


def test_an_alert_can_be_dismissed(client: TestClient, inbox: Inbox) -> None:
    [first, *_] = client.get("/alerts/inbox").json()["entries"]

    response = client.delete(f"/alerts/inbox/{first['id']}")

    assert response.status_code == 204
    assert first["id"] not in {
        e["id"] for e in client.get("/alerts/inbox").json()["entries"]
    }


def test_dismissing_an_alert_that_is_not_yours_is_not_found(
    client: TestClient,
    inbox: Inbox,
) -> None:
    theirs = next(entry for entry in inbox.entries if entry.user_id == OTHER)

    response = client.delete(f"/alerts/inbox/{theirs.entry_id}")

    assert response.status_code == 404
    assert theirs in inbox.entries


def test_a_malformed_id_is_refused(client: TestClient) -> None:
    assert client.delete("/alerts/inbox/not-an-id").status_code == 422


def test_all_alerts_can_be_dismissed_and_only_the_owners(
    client: TestClient,
    inbox: Inbox,
) -> None:
    assert client.delete("/alerts/inbox").status_code == 204
    assert client.get("/alerts/inbox").json()["entries"] == []
    assert any(entry.user_id == OTHER for entry in inbox.entries)
