"""Reading a handful of named movements back, and what a throttled table does.

The happy path is covered against a real table in the integration suite. What
cannot be produced there is the answer this file is about: `BatchGetItem`
handing back `UnprocessedKeys` because the table is throttling. Treating those
as "not there" would read a charge somebody paid as still owing, and
re-queueing them forever would turn a screen into a request that never
returns. Both failures are silent, so both are pinned here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import MovementDirection
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    MOVEMENT_PREFIX,
    PARTITION_KEY,
    SORT_KEY,
    CorruptFinancialItemError,
    DynamoDBTransactionLedger,
    movement_to_item,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


TABLE = "financial-test"
USER = UserId.from_string("44444444-4444-4444-4444-444444444444")


def _movement() -> Transaction:
    return Transaction.enter_manually(
        user_id=USER,
        direction=MovementDirection.OUTGOING,
        amount=Money(amount=Decimal("120000"), currency=Currency.COP),
        occurred_at=PosixTime.now(),
        counterparty="Gimnasio",
    )


class FakeDynamoDB:
    """Answers `batch_get_item`, and remembers what it was asked.

    `defer` is how many of the first responses hand every key back as
    unprocessed — which is exactly what a throttled table does.
    """

    def __init__(
        self,
        rows: list[Transaction] | None = None,
        *,
        defer: int = 0,
    ) -> None:
        self.rows = {row.id.value: movement_to_item(row) for row in rows or []}
        self.defer = defer
        self.batches: list[list[dict[str, Any]]] = []

    @property
    def calls(self) -> int:
        return len(self.batches)

    def batch_get_item(self, **request: Any) -> dict[str, Any]:  # noqa: ANN401
        keys = request["RequestItems"][TABLE]["Keys"]
        self.batches.append(keys)

        if self.defer > 0:
            self.defer -= 1

            return {
                "Responses": {TABLE: []},
                "UnprocessedKeys": {TABLE: {"Keys": keys}},
            }

        found = [
            self.rows[wanted]
            for key in keys
            if (wanted := key[SORT_KEY]["S"].removeprefix(MOVEMENT_PREFIX)) in self.rows
        ]

        return {"Responses": {TABLE: found}}


def _ledger(client: FakeDynamoDB) -> DynamoDBTransactionLedger:
    return DynamoDBTransactionLedger(client=client, table_name=TABLE)  # type: ignore[arg-type]


def test_only_the_rows_that_exist_come_back() -> None:
    movement = _movement()
    ledger = _ledger(FakeDynamoDB([movement]))

    found = ledger.find_many(
        user_id=USER,
        movement_ids=[movement.id.value, "nunca-escrito"],
    )

    assert set(found) == {movement.id.value}


def test_asking_for_nothing_asks_the_table_nothing() -> None:
    client = FakeDynamoDB()

    assert _ledger(client).find_many(user_id=USER, movement_ids=[]) == {}
    assert client.calls == 0


def test_the_same_id_twice_is_one_key() -> None:
    """DynamoDB refuses a batch holding a duplicate key outright."""
    movement = _movement()
    client = FakeDynamoDB([movement])

    _ledger(client).find_many(
        user_id=USER,
        movement_ids=[movement.id.value, movement.id.value],
    )

    assert [len(batch) for batch in client.batches] == [1]


def test_a_hundred_and_one_ids_go_in_two_requests() -> None:
    """100 is DynamoDB's own ceiling, and a batch over it is rejected whole."""
    client = FakeDynamoDB()

    _ledger(client).find_many(
        user_id=USER,
        movement_ids=[f"id-{n}" for n in range(101)],
    )

    assert [len(batch) for batch in client.batches] == [100, 1]


def test_the_user_is_part_of_every_key_asked_for() -> None:
    """Not a filter applied after the read: one person's charges must be
    unreachable from another's session, not merely hidden from it."""
    client = FakeDynamoDB()

    _ledger(client).find_many(user_id=USER, movement_ids=["a", "b"])

    [batch] = client.batches
    assert {key[PARTITION_KEY]["S"] for key in batch} == {str(USER.value)}


def test_a_deferred_key_is_asked_for_again_rather_than_read_as_absent() -> None:
    """The one wrong answer this lookup can give: a charge somebody paid,
    reported as still owing."""
    movement = _movement()
    client = FakeDynamoDB([movement], defer=2)

    found = _ledger(client).find_many(user_id=USER, movement_ids=[movement.id.value])

    assert set(found) == {movement.id.value}
    assert client.calls == 3


def test_a_table_that_never_stops_deferring_is_given_up_on() -> None:
    """Rather than retried forever. This sits behind a screen somebody is
    looking at, and a spin is a request that never returns."""
    client = FakeDynamoDB([_movement()], defer=99)

    with pytest.raises(CorruptFinancialItemError, match="deferring"):
        _ledger(client).find_many(user_id=USER, movement_ids=["whatever"])

    assert client.calls < 10
