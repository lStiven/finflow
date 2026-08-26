"""Which messages a batch invocation reports back, and which it lets go.

Lambda's rule is the inverse of the polling loop's: everything the response
does not name is deleted. So the interesting cases are all about what happens
to the messages *beside* the one that went wrong.
"""

from personal_finance.shared.infrastructure.messaging.lambda_batch import (
    SQSEvent,
    drain,
)


def _event(*bodies: str) -> SQSEvent:
    return {
        "Records": [
            {"messageId": f"message-{index}", "body": body}
            for index, body in enumerate(bodies)
        ],
    }


def test_nothing_is_reported_when_every_message_is_handled() -> None:
    response = drain(_event("a", "b", "c"), must_retry=lambda _body: False)

    # An empty list is what tells Lambda to delete all three.
    assert response == {"batchItemFailures": []}


def test_only_the_message_that_must_come_back_is_reported() -> None:
    response = drain(_event("keep", "retry", "keep"), must_retry=lambda b: b == "retry")

    assert response == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}


def test_a_raising_message_is_reported_rather_than_escaping() -> None:
    """The whole reason `drain` catches: an exception reaching Lambda fails
    the entire batch, so the two messages handled beside this one would be
    redelivered along with it.
    """

    def must_retry(body: str) -> bool:
        if body == "boom":
            raise RuntimeError("the ledger write failed")

        return False

    response = drain(_event("fine", "boom", "fine"), must_retry=must_retry)

    assert response == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}


def test_every_message_is_offered_even_after_one_raises() -> None:
    seen: list[str] = []

    def must_retry(body: str) -> bool:
        seen.append(body)

        if body == "boom":
            raise RuntimeError("transient")

        return False

    drain(_event("first", "boom", "last"), must_retry=must_retry)

    assert seen == ["first", "boom", "last"]


def test_an_empty_batch_is_not_an_error() -> None:
    assert drain({"Records": []}, must_retry=lambda _body: True) == {
        "batchItemFailures": [],
    }
