"""What the worker does with messages it cannot act on.

The happy path is covered end to end against a real bus in
`tests/integration/financial`; what matters here is the split this worker has
to get right and nothing else exercises directly: a message that will never
become valid must leave the queue, and one that merely arrived too early — or
failed for a reason that may not repeat — must not.

Deleting the wrong one loses a real movement, and a lost movement is a balance
that is quietly wrong rather than visibly missing.
"""

from decimal import Decimal
import json
from typing import Any, cast
import uuid

from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.financial.application.commands import (
    RecordMovementCommand,
)
from personal_finance.contexts.financial.application.handlers import (
    Outcome,
    RecordMovementResult,
    RecordMovementUseCase,
)
from personal_finance.contexts.financial.domain.entities import Transaction
from personal_finance.contexts.financial.domain.value_objects import (
    MovementDirection,
)
from personal_finance.contexts.financial.infrastructure.messaging.inbound import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
)
from personal_finance.contexts.financial.infrastructure.messaging.sqs_worker import (
    SQSFinancialWorker,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/financial-events"
USER_ID = "11111111-1111-1111-1111-111111111111"


class FakeSQSClient:
    def __init__(self, *bodies: str) -> None:
        self.bodies = bodies
        self.deleted: list[str] = []

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        del kwargs

        return {
            "Messages": [
                {"ReceiptHandle": f"receipt-{index}", "Body": body}
                for index, body in enumerate(self.bodies)
            ],
        }

    def delete_message(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.deleted.append(str(kwargs["ReceiptHandle"]))

        return {}


def _result(outcome: Outcome = Outcome.UNASSIGNED) -> RecordMovementResult:
    return RecordMovementResult(
        outcome=outcome,
        transaction=Transaction.from_alert(
            user_id=UserId(value=uuid.UUID(USER_ID)),
            bank="Bancolombia",
            counterparty="TIENDAS ARA 123",
            amount=Money(amount=Decimal("29259.00"), currency=Currency.COP),
            direction=MovementDirection.OUTGOING,
            occurred_at=PosixTime.from_epoch_seconds(1_700_000_000),
        ),
    )


class RecordingUseCase:
    def __init__(self, outcome: Outcome = Outcome.UNASSIGNED) -> None:
        self.commands: list[RecordMovementCommand] = []
        self._outcome = outcome

    def execute(self, command: RecordMovementCommand) -> RecordMovementResult:
        self.commands.append(command)

        return _result(self._outcome)


class FailingUseCase:
    """Whatever a repository does on a bad day: a throttle, an unreadable row."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command: RecordMovementCommand) -> RecordMovementResult:
        del command
        self.calls += 1

        raise RuntimeError("DynamoDB said no")


def _envelope(
    *,
    source: str = INGESTION_SOURCE,
    detail_type: str = TRANSACTION_EXTRACTED,
    version: int = 1,
    direction: str = "outgoing",
    currency: str = "COP",
    amount: str = "29259.00",
    occurred_at: int = 1_700_000_000,
) -> str:
    return json.dumps(
        {
            "source": source,
            "detail-type": detail_type,
            "detail": {
                "version": version,
                "event_id": str(uuid.uuid4()),
                "user_id": USER_ID,
                "transaction": {
                    "kind": "card_purchase",
                    "direction": direction,
                    "amount": amount,
                    "currency": currency,
                    "occurred_at": occurred_at,
                    "counterparty": "TIENDAS ARA 123",
                    "bank": "Bancolombia",
                },
            },
        },
    )


def _worker(
    *bodies: str,
    use_case: object | None = None,
) -> tuple[SQSFinancialWorker, FakeSQSClient, Any]:
    client = FakeSQSClient(*bodies)
    resolved = RecordingUseCase() if use_case is None else use_case
    worker = SQSFinancialWorker(
        client=cast(SQSClient, client),
        queue_url=QUEUE_URL,
        use_case=cast(RecordMovementUseCase, resolved),
    )

    return worker, client, resolved


# ------------------------------------------------- never becomes valid: drop


def test_a_malformed_message_is_dropped_rather_than_retried() -> None:
    worker, client, use_case = _worker("not json at all")

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    assert client.deleted == ["receipt-0"]


def test_an_event_this_worker_does_not_subscribe_to_is_dropped() -> None:
    worker, client, use_case = _worker(_envelope(detail_type="SomethingElse"))

    worker.poll_once(wait_seconds=0)

    assert not use_case.commands
    assert client.deleted == ["receipt-0"]


def test_a_payload_the_model_refuses_is_dropped() -> None:
    # Which values the model refuses is `test_inbound_mapping.py`'s subject and
    # is not restated here. What belongs to the worker is the consequence: a
    # ValidationError leaves the queue rather than circling it.
    worker, client, use_case = _worker(_envelope(amount="1E+1000000"))

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    assert client.deleted == ["receipt-0"]


# ------------------------------------------- may yet become valid: keep it


def test_a_payload_version_this_worker_cannot_read_stays_on_the_queue() -> None:
    worker, client, use_case = _worker(_envelope(version=99))

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    # A newer deploy wrote it; deleting it would book a v2 amount under v1
    # meaning, or lose the movement outright.
    assert client.deleted == []


@pytest.mark.parametrize(
    ("direction", "currency"),
    [("sideways", "COP"), ("outgoing", "XYZ")],
)
def test_a_direction_or_currency_this_worker_cannot_read_stays(
    direction: str,
    currency: str,
) -> None:
    worker, client, use_case = _worker(
        _envelope(direction=direction, currency=currency),
    )

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    # `Currency` knows two members today and an alert in a third would be
    # readable by the very next deploy. Deleting destroys a real movement.
    assert client.deleted == []


def test_a_use_case_failure_leaves_the_message_and_does_not_stop_the_batch() -> None:
    worker, client, use_case = _worker(
        _envelope(),
        _envelope(),
        use_case=FailingUseCase(),
    )

    result = worker.poll_once(wait_seconds=0)

    # Both attempted: one throttled write must not abandon the rest of the
    # receive, whose messages would go unprocessed while earlier ones are
    # already deleted.
    assert use_case.calls == 2
    assert result.rejected == 2
    assert client.deleted == []


# ---------------------------------------------------------- the happy paths


def test_a_readable_movement_is_handled_and_deleted() -> None:
    worker, client, use_case = _worker(_envelope())

    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    assert len(use_case.commands) == 1
    assert client.deleted == ["receipt-0"]


def test_a_redelivered_movement_is_deleted_rather_than_retried() -> None:
    # At-least-once delivery makes this the ordinary case, not an edge one:
    # the ledger refused the duplicate write, so there is nothing left to do.
    worker, client, _ = _worker(
        _envelope(),
        use_case=RecordingUseCase(outcome=Outcome.DUPLICATE),
    )

    result = worker.poll_once(wait_seconds=0)

    assert result.handled == 1
    assert client.deleted == ["receipt-0"]
