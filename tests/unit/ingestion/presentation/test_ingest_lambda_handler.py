"""The scheduled entry point: one pass per tick, and failures made visible.

The polling loop swallows a bad pass on purpose — it has to outlive one. This
one must not, and that difference is the only behaviour worth pinning here.
"""

import pytest

from personal_finance.contexts.ingestion.application.ingest_handlers import PollResult
from personal_finance.contexts.ingestion.presentation.awslambda import ingest_handler


class FakeUseCase:
    def __init__(self, *results: PollResult | Exception) -> None:
        self._results = list(results)
        self.calls = 0

    def execute(self) -> PollResult:
        self.calls += 1
        result = self._results.pop(0)

        if isinstance(result, Exception):
            raise result

        return result


def _install(monkeypatch: pytest.MonkeyPatch, use_case: FakeUseCase) -> list[int]:
    """Swap in the fake and drop whatever the previous test left cached."""
    builds: list[int] = []

    def build() -> FakeUseCase:
        builds.append(1)

        return use_case

    monkeypatch.setattr(ingest_handler, "build_use_case", build)
    ingest_handler.get_use_case.cache_clear()

    return builds


def test_one_tick_polls_once_and_reports_the_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_case = FakeUseCase(
        PollResult(
            fetched=3,
            accepted=2,
            duplicates=1,
            unknown_recipient=0,
            failed=0,
            confirmations=1,
        ),
    )
    _install(monkeypatch, use_case)

    summary = ingest_handler.handler(None, None)

    assert use_case.calls == 1
    assert summary == {
        "fetched": 3,
        "accepted": 2,
        "duplicates": 1,
        "unknown_recipient": 0,
        "failed": 0,
        # Reported apart from `accepted`: a confirmed forwarding request is
        # somebody's setup finishing, not a movement.
        "confirmations": 1,
        "refused_confirmations": 0,
        "unclaimed_confirmations": 0,
    }


def test_a_failing_poll_fails_the_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Swallowed, a mailbox that stopped answering would be indistinguishable
    from a mailbox with no new mail — and nothing would ever say so.
    """
    _install(monkeypatch, FakeUseCase(OSError("imap refused the connection")))

    with pytest.raises(OSError, match="imap refused"):
        ingest_handler.handler(None, None)


def test_the_use_case_is_built_once_across_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_case = FakeUseCase(PollResult(), PollResult())
    builds = _install(monkeypatch, use_case)

    ingest_handler.handler(None, None)
    ingest_handler.handler(None, None)

    assert len(builds) == 1
    assert use_case.calls == 2
