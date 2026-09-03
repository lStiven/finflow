"""That the Lambda entry point wires the right outcome to "send it back".

`drain` is tested on its own; what cannot be caught there is this handler
naming the wrong enum member. Reporting `HANDLED` instead of `RETRY` would
redeliver every sighting forever and delete the ones that genuinely failed —
both silently, because either way the invocation succeeds.
"""

import pytest

from personal_finance.contexts.merchant.presentation.awslambda import (
    merchant_handler,
)
from personal_finance.shared.infrastructure.messaging.sqs_polling import (
    MessageOutcome,
)


class FakeWorker:
    def __init__(self, *outcomes: MessageOutcome) -> None:
        self._outcomes = list(outcomes)
        self.bodies: list[str] = []

    def handle(self, body: str) -> MessageOutcome:
        self.bodies.append(body)

        return self._outcomes.pop(0)


def _install(monkeypatch: pytest.MonkeyPatch, worker: FakeWorker) -> list[int]:
    """Swap in the fake and drop whatever the previous test left cached.

    The reset lives here rather than in an autouse fixture so that installing
    a double and clearing the cache cannot drift apart: a test that does the
    first without the second would silently run against its predecessor's
    worker.
    """
    builds: list[int] = []

    def build() -> FakeWorker:
        builds.append(1)

        return worker

    monkeypatch.setattr(merchant_handler, "build_worker", build)
    merchant_handler.get_worker.cache_clear()

    return builds


def _event(*ids: str) -> dict[str, object]:
    return {"Records": [{"messageId": i, "body": f"body-{i}"} for i in ids]}


def test_only_retry_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = FakeWorker(
        MessageOutcome.HANDLED,
        MessageOutcome.RETRY,
        MessageOutcome.DISCARDED,
    )
    _install(monkeypatch, worker)

    response = merchant_handler.handler(_event("a", "b", "c"), None)  # type: ignore[arg-type]

    # `DISCARDED` is deliberately absent: understood and not worth keeping is
    # a message that must leave the queue, same as a handled one.
    assert response == {"batchItemFailures": [{"itemIdentifier": "b"}]}


def test_the_worker_is_built_once_across_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the `lru_cache` is for: Lambda reuses the process, so rebuilding
    the client and the whole use-case graph per batch is pure waste.
    """
    worker = FakeWorker(MessageOutcome.HANDLED, MessageOutcome.HANDLED)
    builds = _install(monkeypatch, worker)

    merchant_handler.handler(_event("a"), None)  # type: ignore[arg-type]
    merchant_handler.handler(_event("b"), None)  # type: ignore[arg-type]

    assert len(builds) == 1
    assert worker.bodies == ["body-a", "body-b"]
