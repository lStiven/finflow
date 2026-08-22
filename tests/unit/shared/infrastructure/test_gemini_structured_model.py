"""Which model failures are worth another attempt, and which are ours.

The split matters more than it looks: a rate limit treated as permanent
silently discards a real bank alert, while a rejected API key treated as
transient hides a broken deployment behind a growing backlog.
"""

from typing import Any, cast

from google import genai
from google.genai import errors, types
from pydantic import BaseModel
import pytest

from personal_finance.shared.infrastructure.config.settings import LLMSettings
from personal_finance.shared.infrastructure.llm.errors import (
    LLMRequestRejectedError,
    LLMTemporarilyUnavailableError,
    LLMUnusableResponseError,
)
from personal_finance.shared.infrastructure.llm.gemini import (
    StructuredModel,
    truncate_for_prompt,
)


class Answer(BaseModel):
    verdict: str


class StubModels:
    def __init__(
        self, *, parsed: object = None, error: Exception | None = None
    ) -> None:
        self.parsed = parsed
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> types.GenerateContentResponse:  # noqa: ANN401
        self.calls.append(kwargs)

        if self.error is not None:
            raise self.error

        response = types.GenerateContentResponse()
        response.parsed = cast("Any", self.parsed)

        return response


class StubClient:
    def __init__(self, models: StubModels) -> None:
        self.models = models


def _model(
    *,
    parsed: object = None,
    error: Exception | None = None,
) -> tuple[StructuredModel, StubModels]:
    models = StubModels(parsed=parsed, error=error)

    return (
        StructuredModel(
            client=cast("genai.Client", StubClient(models)),
            settings=LLMSettings(),
        ),
        models,
    )


def _complete(model: StructuredModel) -> Answer:
    return model.complete(
        system_instruction="be precise",
        prompt="a question",
        schema=Answer,
    )


def test_a_validated_answer_comes_back_as_the_schema() -> None:
    model, _ = _model(parsed=Answer(verdict="yes"))

    assert _complete(model).verdict == "yes"


def test_a_server_error_is_worth_another_attempt() -> None:
    model, _ = _model(error=errors.ServerError(503, {"error": {"message": "busy"}}))

    with pytest.raises(LLMTemporarilyUnavailableError):
        _complete(model)


def test_a_rate_limit_is_worth_another_attempt() -> None:
    model, _ = _model(error=errors.ClientError(429, {"error": {"message": "quota"}}))

    with pytest.raises(LLMTemporarilyUnavailableError):
        _complete(model)


def test_a_rejected_request_is_our_misconfiguration() -> None:
    # A bad key or an unknown model fails identically for every message, so it
    # is never turned into a per-email outcome.
    model, _ = _model(error=errors.ClientError(403, {"error": {"message": "denied"}}))

    with pytest.raises(LLMRequestRejectedError):
        _complete(model)


def test_an_answer_that_is_not_the_schema_is_refused() -> None:
    # The SDK hands back a plain dict when it could not fit the response to
    # the schema. Nothing but the schema instance is allowed through.
    model, _ = _model(parsed={"verdict": "yes"})

    with pytest.raises(LLMUnusableResponseError):
        _complete(model)


def test_an_empty_answer_is_refused() -> None:
    model, _ = _model(parsed=None)

    with pytest.raises(LLMUnusableResponseError):
        _complete(model)


def test_the_request_demands_json_matching_the_schema() -> None:
    model, models = _model(parsed=Answer(verdict="yes"))

    _complete(model)

    config = models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is Answer
    # Deterministic on purpose: the same email should extract the same
    # transaction every time.
    assert config.temperature == 0.0


def test_an_oversized_document_is_cut_rather_than_refused() -> None:
    assert truncate_for_prompt("x" * 100, limit=10) == "x" * 10


def test_a_document_within_budget_is_untouched() -> None:
    assert truncate_for_prompt("short", limit=10) == "short"
