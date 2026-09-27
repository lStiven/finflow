"""Declaring a movement a transfer after the fact, over HTTP, over fakes.

What the use case does is covered against a real table in
`tests/integration/financial/test_declared_transfer_integration.py`. This is
the surface: what the three endpoints accept, what they answer, and which
refusal becomes which status.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
    MerchantAttribution,
)
from personal_finance.contexts.financial.application.transfers import (
    DeclareTransferUseCase,
)
from personal_finance.contexts.financial.domain.bills import BillId, ScheduledBill
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    InstrumentKind,
    MovementDirection,
    TransferId,
)
from personal_finance.contexts.financial.presentation.http.router import (
    get_declare_transfer_use_case,
    get_merchant_directory,
    router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
PAID_AT = PosixTime.from_epoch_seconds(1_767_111_420)


class Accounts:
    def __init__(self, *held: Account) -> None:
        self.by_id = {account.id: account for account in held}

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        account = self.by_id.get(account_id)

        return account if account is not None and account.user_id == user_id else None

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [a for a in self.by_id.values() if a.user_id == user_id]

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        del user_id, fingerprint

        return None

    def save(self, account: Account) -> None:
        self.by_id[account.id] = account

    def add(self, account: Account) -> bool:
        self.save(account)

        return True

    def unlink_fingerprint(
        self,
        account: Account,
        fingerprint: AccountFingerprint,
    ) -> None:
        del fingerprint
        self.save(account)

    def overwrite_balance(self, account: Account) -> None:
        self.save(account)

    def restate_balance(self, account: Account) -> None:
        self.save(account)


class Ledger:
    """A ledger that keeps copies, so a refused write changes nothing here."""

    def __init__(self) -> None:
        self.rows: dict[str, Transaction] = {}

    def put(self, transaction: Transaction) -> None:
        transaction.pull_events()
        self.rows[transaction.id.value] = replace(transaction)

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        row = self.rows.get(transaction_id)

        return replace(row) if row is not None and row.user_id == user_id else None

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [replace(row) for row in self.rows.values() if row.user_id == user_id]

    def declare(
        self,
        *,
        reclassified: Sequence[Transaction],
        written: Transaction | None,
        balance_delta: Decimal | None,
    ) -> bool:
        del balance_delta

        if any(
            self.rows[movement.id.value].transfer is not None
            for movement in reclassified
        ) or (written is not None and written.id.value in self.rows):
            return False

        for movement in reclassified:
            self.rows[movement.id.value] = replace(
                self.rows[movement.id.value],
                transfer=movement.transfer,
            )

        if written is not None:
            self.rows[written.id.value] = replace(written)

        return True

    def undeclare(
        self,
        *,
        restored: Sequence[Transaction],
        transfer_id: TransferId,
        erased: Transaction | None,
        reversal: BalanceReversal | None,
    ) -> bool:
        del reversal

        for movement in restored:
            leg = self.rows[movement.id.value].transfer

            if leg is None or leg.transfer_id != transfer_id:
                return False

        for movement in restored:
            self.rows[movement.id.value] = replace(
                self.rows[movement.id.value],
                transfer=None,
            )

        if erased is not None:
            self.rows.pop(erased.id.value, None)

        return True

    # What `TransactionLedger` asks for and these endpoints never call.
    def record(
        self, *, transaction: Transaction, balance_delta: Decimal | None
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

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
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


class NoBills:
    def find(self, *, user_id: UserId, bill_id: BillId) -> ScheduledBill | None:
        del user_id, bill_id

        return None

    def list_by_user(self, user_id: UserId) -> Sequence[ScheduledBill]:
        del user_id

        return []

    def save(self, bill: ScheduledBill) -> None:
        del bill

    def remove(self, *, user_id: UserId, bill_id: BillId) -> bool:
        del user_id, bill_id

        return False


class NoMerchants:
    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        del user_id, counterparties

        return {}


class NullPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


def _cop(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency=Currency.COP)


def _account(name: str, kind: AccountKind, bank: str, holds: str) -> Account:
    account = Account.open(
        user_id=USER_ID,
        name=name,
        kind=kind,
        currency=Currency.COP,
        bank=bank,
        instrument_kind=InstrumentKind.ACCOUNT if kind is AccountKind.SAVINGS else None,
        last_four="5261" if kind is AccountKind.SAVINGS else None,
        opening_balance=_cop(holds),
        opened_at=PAID_AT,
    )
    account.pull_events()

    return account


class World:
    def __init__(self) -> None:
        self.savings = _account(
            "Ahorros Bancolombia",
            AccountKind.SAVINGS,
            "Bancolombia",
            "1374267",
        )
        self.card = _account(
            "Tarjeta AV Villas",
            AccountKind.CREDIT_CARD,
            "AV Villas",
            "3625733",
        )
        self.ledger = Ledger()
        alert = Transaction.from_alert(
            user_id=USER_ID,
            bank="bancolombia",
            direction=MovementDirection.OUTGOING,
            amount=_cop("3625733.00"),
            occurred_at=PAID_AT,
            counterparty="BANCO COMERCIAL AV VILLAS",
            instrument_kind="account",
            last_four="5261",
        )
        alert.assign_to(self.savings.id)
        self.ledger.put(alert)
        self.alert_id = alert.id.value
        accounts = Accounts(self.savings, self.card)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user_id] = lambda: USER_ID
        app.dependency_overrides[get_merchant_directory] = NoMerchants
        app.dependency_overrides[get_declare_transfer_use_case] = lambda: (
            DeclareTransferUseCase(
                accounts=accounts,
                ledger=self.ledger,
                declarations=self.ledger,
                bills=NoBills(),
                event_publisher=NullPublisher(),
            )
        )
        self.client = TestClient(app)

    def declare(self, **body: object) -> dict[str, Any]:
        response = self.client.post(
            f"/financial/transactions/{self.alert_id}/transfer",
            json=body,
        )
        assert response.status_code == 200, response.text

        return response.json()


@pytest.fixture
def world() -> World:
    return World()


# ------------------------------------------------------------------ options


def test_the_options_offer_the_card_the_alert_names_first(world: World) -> None:
    response = world.client.get(
        f"/financial/transactions/{world.alert_id}/transfer-options",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "source"
    assert body["refusal"] is None
    assert body["accounts"] == [
        {
            "id": str(world.card.id.value),
            "name": "Tarjeta AV Villas",
            "kind": "credit_card",
            "suggested": True,
        },
    ]
    assert body["counterparts"] == []


def test_the_options_say_why_when_nothing_can_be_offered(world: World) -> None:
    world.declare()

    body = world.client.get(
        f"/financial/transactions/{world.alert_id}/transfer-options",
    ).json()

    assert body["refusal"] == "already_transfer"
    assert body["accounts"] == []


def test_the_options_for_a_movement_nobody_has_are_404(world: World) -> None:
    response = world.client.get("/financial/transactions/nope/transfer-options")

    assert response.status_code == 404


# ------------------------------------------------------------------ declare


def test_declaring_with_an_account_answers_both_sides_and_the_balance(
    world: World,
) -> None:
    body = world.declare(counterpart_account_id=str(world.card.id.value))

    declared, written = body["transactions"]
    assert declared["id"] == world.alert_id
    assert declared["transfer"]["basis"] == "reclassified"
    assert declared["transfer"]["external"] is False
    assert declared["transfer"]["counterpart_movement_id"] == written["id"]
    assert declared["transfer"]["counterpart_instrument_kind"] is None
    assert written["transfer"]["basis"] == "counterpart"
    assert written["direction"] == "incoming"
    assert written["counterparty"] == "Ahorros Bancolombia"
    assert written["account_id"] == str(world.card.id.value)
    assert [account["balance"] for account in body["accounts"]] == ["0.00"]


def test_declaring_with_nothing_else_is_a_lone_side(world: World) -> None:
    body = world.declare()

    (declared,) = body["transactions"]
    assert declared["transfer"]["external"] is True
    assert declared["transfer"]["role"] == "source"
    assert body["accounts"] == []


def test_declaring_the_same_thing_twice_answers_it_again(world: World) -> None:
    world.declare(counterpart_account_id=str(world.card.id.value))

    again = world.declare(counterpart_account_id=str(world.card.id.value))

    assert again["transactions"][0]["transfer"]["basis"] == "reclassified"
    assert len(world.ledger.rows) == 2


def test_declaring_something_else_afterwards_is_409(world: World) -> None:
    world.declare()

    response = world.client.post(
        f"/financial/transactions/{world.alert_id}/transfer",
        json={"counterpart_account_id": str(world.card.id.value)},
    )

    assert response.status_code == 409


def test_naming_both_an_account_and_a_movement_is_422(world: World) -> None:
    response = world.client.post(
        f"/financial/transactions/{world.alert_id}/transfer",
        json={
            "counterpart_account_id": str(world.card.id.value),
            "counterpart_movement_id": "abc",
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"counterpart_account_id": "not-an-id"},
        {"counterpart_account_id": "22222222-2222-2222-2222-222222222222"},
        {"counterpart_movement_id": "nope"},
    ],
)
def test_an_other_side_nobody_has_is_404(world: World, body: dict[str, str]) -> None:
    response = world.client.post(
        f"/financial/transactions/{world.alert_id}/transfer",
        json=body,
    )

    assert response.status_code == 404
    assert world.ledger.rows[world.alert_id].transfer is None


def test_the_other_side_on_its_own_account_is_409(world: World) -> None:
    response = world.client.post(
        f"/financial/transactions/{world.alert_id}/transfer",
        json={"counterpart_account_id": str(world.savings.id.value)},
    )

    assert response.status_code == 409
    assert world.ledger.rows[world.alert_id].transfer is None


# --------------------------------------------------------------------- undo


def test_undoing_answers_what_came_back_and_what_went(world: World) -> None:
    declared = world.declare(counterpart_account_id=str(world.card.id.value))
    written_id = declared["transactions"][1]["id"]

    response = world.client.delete(f"/financial/transactions/{world.alert_id}/transfer")

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["transactions"]] == [world.alert_id]
    assert body["transactions"][0]["transfer"] is None
    assert body["erased"] == [written_id]
    assert [account["balance"] for account in body["accounts"]] == ["3625733.00"]
    assert written_id not in world.ledger.rows


def test_undoing_an_ordinary_movement_is_409(world: World) -> None:
    response = world.client.delete(f"/financial/transactions/{world.alert_id}/transfer")

    assert response.status_code == 409
