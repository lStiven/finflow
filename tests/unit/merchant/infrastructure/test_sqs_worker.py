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
                    "occurred_at": 1_700_000_000,
                    "counterparty": "TIENDAS ARA 123",
                },
            },
        },
    )


def _worker(
    *bodies: str,
) -> tuple[SQSMerchantWorker, FakeSQSClient, RecordingUseCase]:
    client = FakeSQSClient(*bodies)
    use_case = RecordingUseCase()
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
