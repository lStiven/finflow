"""The one place that talks to Gemini.

Every caller goes through `StructuredModel.complete`, which demands a Pydantic
schema and returns a validated instance of it — never free text. That is not a
convenience: model output is untrusted input, exactly like the email that
produced it, and the only shape allowed to cross into a use case is one the
schema already proved.

Nothing here knows anything about banks, merchants or transactions. The
prompts that do live in each context's own `infrastructure/llm/`, because what
a merchant is is that context's business and not shared vocabulary.
"""

from __future__ import annotations

import functools
import logging

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

from personal_finance.shared.infrastructure.config.settings import (
    LLMSettings,
    get_llm_settings,
)
from personal_finance.shared.infrastructure.llm.errors import (
    LLMNotConfiguredError,
    LLMRequestRejectedError,
    LLMTemporarilyUnavailableError,
    LLMUnusableResponseError,
)


_logger = logging.getLogger(__name__)

# The model is asked for JSON matching a schema, so there is nothing to gain
# from sampling: the same email should extract the same transaction every
# time, and a run that differs is a bug we want to be able to reproduce.
TEMPERATURE = 0.0

# HTTP 429. The SDK raises it as a plain `ClientError`, so the code is what
# separates "come back later" from "this request was wrong".
TOO_MANY_REQUESTS = 429


@functools.lru_cache(maxsize=1)
def build_client() -> genai.Client:
    """The shared Gemini client.

    Raises rather than returning None: only `llm_configured` decides whether a
    deployment has a model at all, and reaching here without a key means
    something asked for one anyway.
    """
    settings = get_llm_settings()
    api_key = settings.api_key.get_secret_value()

    if not api_key:
        raise LLMNotConfiguredError(
            "LLM_API_KEY is not set: there is no model to fall back to. Set it "
            "to a Gemini API key, or leave it empty to run without the "
            "fallback.",
        )

    return genai.Client(api_key=api_key)


class StructuredModel:
    """Asks a model one question and insists on a validated answer."""

    def __init__(
        self,
        *,
        client: genai.Client,
        settings: LLMSettings,
    ) -> None:
        self._client = client
        self._settings = settings

    @property
    def model(self) -> str:
        return self._settings.model

    def complete[SchemaT: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        response = self._generate(
            system_instruction=system_instruction,
            prompt=prompt,
            schema=schema,
        )
        parsed = response.parsed

        # `parsed` is whatever the SDK could make of the response: the schema
        # instance when it matched, otherwise a dict, an enum, or nothing.
        # Only the first is allowed through.
        if isinstance(parsed, schema):
            return parsed

        raise LLMUnusableResponseError(
            f"{self.model} did not answer with a valid {schema.__name__}",
        )

    def _generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[BaseModel],
    ) -> types.GenerateContentResponse:
        try:
            # `contents` accepts a PIL image among its overloads, and PIL is an
            # optional dependency with no stubs installed, so the whole
            # signature reads as partially unknown. Only the `str` branch is
            # used here.
            return self._client.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                model=self._settings.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=TEMPERATURE,
                    max_output_tokens=self._settings.max_output_tokens,
                    # Nothing here declares tools, and nothing should ever
                    # execute one on our behalf: the content being examined is
                    # an untrusted email. Disabling it explicitly also silences
                    # the SDK's advisory warning on every single call.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True,
                    ),
                    http_options=types.HttpOptions(
                        timeout=self._settings.timeout_seconds * 1000,
                    ),
                ),
            )
        except errors.ServerError as error:
            raise LLMTemporarilyUnavailableError(str(error)) from error
        except errors.ClientError as error:
            if error.code == TOO_MANY_REQUESTS:
                raise LLMTemporarilyUnavailableError(str(error)) from error

            # A rejected key, an unknown model, a schema the API will not
            # accept. Identical for every message, so it is raised rather than
            # turned into a per-email outcome.
            raise LLMRequestRejectedError(str(error)) from error
        except ValidationError as error:
            raise LLMUnusableResponseError(str(error)) from error


@functools.lru_cache(maxsize=1)
def build_structured_model() -> StructuredModel:
    return StructuredModel(client=build_client(), settings=get_llm_settings())


def truncate_for_prompt(text: str, *, limit: int) -> str:
    """Keep a document inside the budget the settings allow.

    A bank alert is a few kilobytes; anything near the limit is a newsletter
    that slipped through a sender filter, and cutting it is better than
    refusing the whole email. It is logged because a silent truncation would
    look exactly like a model that failed to read the message.
    """
    if len(text) <= limit:
        return text

    _logger.warning(
        "truncating content before sending it to the model",
        extra={"length": len(text), "limit": limit},
    )

    return text[:limit]
