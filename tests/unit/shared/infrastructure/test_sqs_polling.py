"""What the shared polling loop deletes, and what it leaves behind.

The mirror of `test_lambda_batch`: there the response names what must come
back, here the queue keeps whatever is not deleted. Both answer the same
question for the same three contexts, and the reason this one exists at all
is that the loop used to be copied per context and the copies disagreed.
"""

from typing import Any

import pytest

from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
    SQSPollingWorker,
)


class FakeSQSClient:
    """Answers one batch, then nothing, and records every delete."""

    def __init__(self, *bodies: str, with_receipts: bool = True) -> None:
        self._messages = [
            {
                "Body": body,
                **({"ReceiptHandle": f"receipt-{index}"} if with_receipts else {}),
            }
            for index, body in enumerate(bodies)
        ]
        self.deleted: list[str] = []
        self.polls = 0

    def receive_message(self, **_kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.polls += 1

        return {"Messages": self._messages} if self.polls == 1 else {}

    def delete_message(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.deleted.append(kwargs["ReceiptHandle"])


class _Worker(SQSPollingWorker):
    """A worker whose contract is spelled out by the message body itself."""

    def __init__(self, client: FakeSQSClient) -> None:
        super().__init__(client=client, queue_url="https://queue.test/q")  # type: ignore[arg-type]
        self.seen: list[str] = []

    def handle(self, body: str) -> MessageOutcome:
        self.seen.append(body)

        if body == "boom":
            raise RuntimeError("the ledger write failed")

        return MessageOutcome[body.upper()]


def test_a_handled_message_is_deleted() -> None:
    client = FakeSQSClient("handled")
    result = _Worker(client).poll_once(wait_seconds=0)

    assert result.received == 1
    assert result.handled == 1
    assert client.deleted == ["receipt-0"]


def test_a_discarded_message_is_deleted_too() -> None:
    """Understood and useless is still finished: retrying a payload that will
    never become readable only delays everything behind it.
    """
    client = FakeSQSClient("discarded")
    result = _Worker(client).poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert client.deleted == ["receipt-0"]


def test_a_retried_message_stays_on_the_queue() -> None:
    client = FakeSQSClient("retry")
    result = _Worker(client).poll_once(wait_seconds=0)

    assert result.rejected == 1
    assert client.deleted == []


def test_a_raising_message_stays_on_the_queue_rather_than_escaping() -> None:
    """The behaviour the three copies disagreed about.

    Two of them caught this and one did not; the one that did not let the
    exception out of `poll_once`, which stopped a process meant to run for
    weeks and abandoned every message behind this one in the same batch.
    """
    client = FakeSQSClient("handled", "boom", "handled")
    worker = _Worker(client)

    result = worker.poll_once(wait_seconds=0)

    assert worker.seen == ["handled", "boom", "handled"]
    assert client.deleted == ["receipt-0", "receipt-2"]
    assert result == type(result)(received=3, handled=2, rejected=1)


def test_a_message_without_a_receipt_is_left_alone() -> None:
    """Nothing can be deleted without one, so it is not handled either: acting
    on it would apply the movement and leave the message to be redelivered.
    """
    client = FakeSQSClient("handled", with_receipts=False)
    worker = _Worker(client)

    result = worker.poll_once(wait_seconds=0)

    assert worker.seen == []
    assert result.received == 1
    assert result.handled == 0


def test_an_empty_poll_reports_nothing() -> None:
    client = FakeSQSClient()

    assert _Worker(client).poll_once(wait_seconds=0).received == 0


def test_a_worker_must_say_what_a_message_means() -> None:
    """`handle` is the whole point of the split: the transport refuses to be
    instantiated without a context's own answer to it.
    """
    with pytest.raises(TypeError):
        SQSPollingWorker(client=FakeSQSClient(), queue_url="q")  # type: ignore[abstract, arg-type]
