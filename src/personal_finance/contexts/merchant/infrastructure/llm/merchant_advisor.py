"""The advisor, as merchant sees it.

Two guarantees live here rather than in the prompt, because a prompt is a
request and this is a rule: the parent must be one of the candidates that were
offered, and the category one of the categories that were. A model that
answers with anything else is answering about a merchant, or a bucket, that
does not exist, and that half of its opinion is dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging

from pydantic import BaseModel, Field

from personal_finance.contexts.merchant.application.ports import (
    CategoryChoice,
    MerchantAdvice,
    MerchantCandidate,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    CategoryKey,
    CounterpartyKind,
    MerchantId,
)
from personal_finance.contexts.merchant.infrastructure.llm.prompt import (
    build_prompt,
    build_system_instruction,
)
from personal_finance.shared.infrastructure.config.settings import get_llm_settings
from personal_finance.shared.infrastructure.llm.errors import LLMError
from personal_finance.shared.infrastructure.llm.gemini import (
    StructuredModel,
    build_structured_model,
)


_logger = logging.getLogger(__name__)


class MerchantAdviceSchema(BaseModel):
    """The only shape the model is allowed to answer in.

    `reason` is first on purpose: the fields are generated in order, so making
    the model state its case before it commits to an id is what stops it from
    picking a merchant and justifying it afterwards. Nothing reads it except
    the log.
    """

    reason: str = Field(default="", max_length=400)
    # Empty means "none of them", which is the common and entirely good answer.
    parent_merchant_id: str = Field(default="", max_length=64)
    category: str = Field(default="", max_length=64)


class GeminiMerchantAdvisor:
    """`MerchantAdvisor` backed by one structured call per new spelling."""

    def __init__(self, *, model: StructuredModel) -> None:
        self._model = model

    def advise(
        self,
        *,
        counterparty: str,
        kind: CounterpartyKind,
        candidates: Sequence[MerchantCandidate],
        categories: Sequence[CategoryChoice],
    ) -> MerchantAdvice | None:
        try:
            answer = self._model.complete(
                system_instruction=build_system_instruction(categories),
                prompt=build_prompt(
                    counterparty=counterparty,
                    kind=kind,
                    candidates=candidates,
                ),
                schema=MerchantAdviceSchema,
            )
        except LLMError:
            # Never raised onward: the sighting that triggered this was
            # already claimed as handled, so failing here would lose it. A
            # merchant with no suggested parent and no category is simply one
            # the user sorts out themselves.
            _logger.exception("merchant advice unavailable")

            return None

        _logger.info(
            "merchant advice",
            extra={
                "counterparty": counterparty,
                "parent": answer.parent_merchant_id or None,
                "category": answer.category,
                "reason": answer.reason,
            },
        )

        return MerchantAdvice(
            parent=_verified_parent(answer.parent_merchant_id, candidates),
            category=_verified_category(answer.category, categories),
        )


def _verified_parent(
    raw: str,
    candidates: Sequence[MerchantCandidate],
) -> MerchantId | None:
    """Accept an id only if it is one we offered.

    Anything else — a hallucinated id, another user's merchant, a reworded
    name — is not a grouping decision, so it becomes no decision at all.
    """
    value = raw.strip()

    if not value:
        return None

    for candidate in candidates:
        if str(candidate.merchant_id.value) == value:
            return candidate.merchant_id

    _logger.warning("advice named a merchant that was not offered")

    return None


def _verified_category(
    raw: str,
    categories: Sequence[CategoryChoice],
) -> CategoryKey:
    """Accept a category only if it is one we offered.

    An unknown one is the same as no opinion: the field stays empty and the
    user picks. Checked against what was actually sent rather than against the
    shipped enum, so a category this user wrote is a valid answer and one
    another user wrote is not.
    """
    value = raw.strip()

    for choice in categories:
        if choice.key.value == value:
            return choice.key

    return CategoryKey.uncategorized()


def build_merchant_advisor() -> GeminiMerchantAdvisor | None:
    """The advisor, or None when this deployment has no model.

    Returning None is what keeps the model optional: the worker wires whatever
    it gets, and without a key the deterministic tiers still do their work.
    """
    if not get_llm_settings().configured:
        return None

    return GeminiMerchantAdvisor(model=build_structured_model())
