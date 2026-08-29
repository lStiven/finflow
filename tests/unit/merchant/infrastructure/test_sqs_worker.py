"""What the worker does with messages it cannot act on.

The happy path is covered end to end against a real bus in
`tests/integration/merchant`; what matters here is that a message which will
never become valid leaves the queue, and one that merely arrived too early
does not.
"""

import json
from typing import Any, cast
import uuid

from mypy_boto3_sqs.client import SQSClient
import pytest

from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    Resolution,
    ResolveMerchantResult,
    ResolveMerchantUseCase,
)
from personal_finance.contexts.merchant.domain.value_objects import CounterpartyKind
from personal_finance.contexts.merchant.infrastructure.messaging.sqs_worker import (
    INGESTION_SOURCE,
    TRANSACTION_EXTRACTED,
    SQSMerchantWorker,
)


QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/000000000000/merchant-events"
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


class RecordingUseCase:
    def __init__(self) -> None:
        self.commands: list[RecordSightingCommand] = []

    def execute(self, command: RecordSightingCommand) -> ResolveMerchantResult:
        self.commands.append(command)

        return ResolveMerchantResult(resolution=Resolution.CREATED)


def _envelope(
    *,
    source: str = INGESTION_SOURCE,
    detail_type: str = TRANSACTION_EXTRACTED,
    version: int = 1,
    kind: str = "card_purchase",
    occurred_at: int = 1_700_000_000,
    counterparty: str = "TIENDAS ARA 123",
) -> str:
    return json.dumps(
        {
            "source": source,
            "detail-type": detail_type,
            "detail": {
                "version": version,
                "event_id": str(uuid.uuid4()),
                "notification_id": str(uuid.uuid4()),
                "user_id": USER_ID,
                "transaction": {
                    "kind": kind,
                    "direction": "outgoing",
                    "amount": "29259.00",
                    "currency": "COP",
                    "occurred_at": occurred_at,
                    "counterparty": counterparty,
                },
            },
        },
    )


def _worker(
    *bodies: str,
    use_case: object | None = None,
) -> tuple[SQSMerchantWorker, FakeSQSClient, Any]:
    client = FakeSQSClient(*bodies)
    use_case = RecordingUseCase() if use_case is None else use_case
    worker = SQSMerchantWorker(
        client=cast(SQSClient, client),
        queue_url=QUEUE_URL,
        use_case=cast(ResolveMerchantUseCase, use_case),
    )

    return worker, client, use_case


def test_a_card_purchase_is_a_business() -> None:
    worker, _, use_case = _worker(_envelope(kind="card_purchase"))

    worker.poll_once(wait_seconds=0)

    assert use_case.commands[0].kind is CounterpartyKind.BUSINESS


def test_a_transfer_is_not_assumed_to_be_a_business() -> None:
    # A transfer names a person as often as a shop, and the sub-brand guess
    # must not be offered for people.
    worker, _, use_case = _worker(_envelope(kind="transfer"))

    worker.poll_once(wait_seconds=0)

    assert use_case.commands[0].kind is CounterpartyKind.UNKNOWN


def test_a_malformed_message_is_dropped_rather_than_retried() -> None:
    worker, client, use_case = _worker("not json at all")

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    # Unparseable payloads never become parseable.
    assert client.deleted == ["receipt-0"]


def test_an_event_this_worker_does_not_subscribe_to_is_dropped() -> None:
    worker, client, use_case = _worker(_envelope(detail_type="SomethingElse"))

    worker.poll_once(wait_seconds=0)

    assert not use_case.commands
    # This queue is merchant's alone, so a foreign event is a misrouted rule,
    # not a message somebody else is still waiting for.
    assert client.deleted == ["receipt-0"]


def test_a_payload_version_this_worker_cannot_read_stays_on_the_queue() -> None:
    worker, client, use_case = _worker(_envelope(version=99))

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    # A newer deploy wrote it, and a newer worker may still pick it up.
    assert client.deleted == []


class FailingUseCase:
    """Whatever a repository does on a bad day."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, command: RecordSightingCommand) -> ResolveMerchantResult:
        del command
        self.calls += 1

        raise RuntimeError("DynamoDB said no")


def test_a_use_case_failure_leaves_the_message_and_does_not_stop_the_batch() -> None:
    worker, client, use_case = _worker(
        _envelope(),
        _envelope(),
        use_case=FailingUseCase(),
    )

    result = worker.poll_once(wait_seconds=0)

    # Both were attempted: a failure on the first must not abandon the rest of
    # the receive, whose messages would otherwise go unprocessed while the
    # ones before them are already deleted.
    assert use_case.calls == 2
    assert result.rejected == 2
    # Left on the queue, so they are retried and eventually dead-lettered.
    assert client.deleted == []


@pytest.mark.parametrize(
    "occurred_at",
    [
        # Year 33658 — `datetime` raises `ValueError` here.
        1_000_000_000_000,
        # Past `time_t` — raises `OverflowError`, which is not a `ValueError`
        # and would escape a guard written for one.
        10_000_000_000_000_000_000,
        -1_000_000_000_000,
    ],
)
def test_a_timestamp_outside_epoch_range_is_dropped_not_raised(
    occurred_at: int,
) -> None:
    worker, client, use_case = _worker(_envelope(occurred_at=occurred_at))

    result = worker.poll_once(wait_seconds=0)

    # Refused at the boundary, so the conversion is never reached. Before the
    # bound existed this escaped the worker entirely and took the batch down.
    assert result.rejected == 1
    assert not use_case.commands
    # Malformed, not early: no later deploy makes year 33658 readable.
    assert client.deleted == ["receipt-0"]


@pytest.mark.parametrize(
    "counterparty",
    [
        # `min_length` counts spaces.
        "   ",
        # Punctuation survives `min_length` and normalizes to nothing.
        "***",
        # So does a script the normalizer does not keep.
        "ПЯТЁРОЧКА",
    ],
)
def test_a_counterparty_with_no_recognisable_text_is_refused_at_the_boundary(
    counterparty: str,
) -> None:
    # Left to the domain each of these raises inside the use case, after the
    # event id is claimed — spending the claim on work that never happened.
    worker, client, use_case = _worker(_envelope(counterparty=counterparty))

    result = worker.poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert not use_case.commands
    assert client.deleted == ["receipt-0"]


def test_an_accented_counterparty_is_not_mistaken_for_blank() -> None:
    # The guard asks the normalizer, which folds accents rather than dropping
    # them. A Colombian merchant list is full of these.
    worker, _, use_case = _worker(_envelope(counterparty="ÉXITO"))

    worker.poll_once(wait_seconds=0)

    assert use_case.commands[0].counterparty == "ÉXITO"
