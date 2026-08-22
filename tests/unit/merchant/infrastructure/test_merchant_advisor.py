"""What the advisor is allowed to come back with.

Two rules live in the adapter rather than the prompt, because a prompt is a
request and these are guarantees: a parent must be one of the merchants that
were offered, and a category must be a member of the enum.
"""

from collections.abc import Sequence
from typing import cast

from pydantic import BaseModel

from personal_finance.contexts.merchant.application.ports import (
    MerchantAdvice,
    MerchantCandidate,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    CounterpartyKind,
    MerchantCategory,
    MerchantId,
)
from personal_finance.contexts.merchant.infrastructure.llm.merchant_advisor import (
    GeminiMerchantAdvisor,
    MerchantAdviceSchema,
)
from personal_finance.contexts.merchant.infrastructure.llm.prompt import (
    CATEGORY_DESCRIPTIONS,
    build_prompt,
    build_system_instruction,
)
from personal_finance.shared.infrastructure.llm.errors import (
    LLMTemporarilyUnavailableError,
)
from personal_finance.shared.infrastructure.llm.gemini import StructuredModel


MERCHANT_ID = MerchantId.from_string("33333333-3333-3333-3333-333333333333")
OTHER_ID = MerchantId.from_string("44444444-4444-4444-4444-444444444444")


class StubModel:
    def __init__(
        self,
        *,
        answer: MerchantAdviceSchema | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.error = error

    def complete[SchemaT: BaseModel](
        self,
        *,
        system_instruction: str,
        prompt: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        del system_instruction, prompt, schema

        if self.error is not None:
            raise self.error

        return cast("SchemaT", self.answer)


def _candidates() -> Sequence[MerchantCandidate]:
    return [
        MerchantCandidate(
            merchant_id=MERCHANT_ID,
            display_name="Nequi",
            aliases=("NEQUI",),
        ),
    ]


def _advise(
    answer: MerchantAdviceSchema | None = None,
    *,
    error: Exception | None = None,
) -> MerchantAdvice | None:
    advisor = GeminiMerchantAdvisor(
        model=cast("StructuredModel", StubModel(answer=answer, error=error)),
    )

    return advisor.advise(
        counterparty="BANCOLOMBIA NEQUI",
        kind=CounterpartyKind.UNKNOWN,
        candidates=_candidates(),
    )


def test_a_merchant_that_was_offered_is_accepted() -> None:
    advice = _advise(
        MerchantAdviceSchema(
            reason="same company",
            parent_merchant_id=str(MERCHANT_ID.value),
            category="transfers",
        ),
    )

    assert advice is not None
    assert advice.parent == MERCHANT_ID
    assert advice.category is MerchantCategory.TRANSFERS


def test_a_merchant_that_was_never_offered_is_no_decision_at_all() -> None:
    # A hallucinated id, or another user's merchant. Either way it is not a
    # grouping, so it becomes none.
    advice = _advise(
        MerchantAdviceSchema(
            parent_merchant_id=str(OTHER_ID.value),
            category="transfers",
        ),
    )

    assert advice is not None
    assert advice.parent is None


def test_text_that_is_not_an_id_is_ignored() -> None:
    advice = _advise(
        MerchantAdviceSchema(parent_merchant_id="Nequi", category="transfers"),
    )

    assert advice is not None
    assert advice.parent is None


def test_no_parent_is_the_ordinary_answer() -> None:
    advice = _advise(MerchantAdviceSchema(parent_merchant_id="", category="groceries"))

    assert advice is not None
    assert advice.parent is None
    assert advice.category is MerchantCategory.GROCERIES


def test_a_category_outside_the_vocabulary_becomes_no_opinion() -> None:
    advice = _advise(
        MerchantAdviceSchema(parent_merchant_id="", category="crypto-jets"),
    )

    assert advice is not None
    assert advice.category is MerchantCategory.UNCATEGORIZED


def test_a_model_outage_costs_the_suggestion_and_nothing_else() -> None:
    # The sighting was already claimed as handled by the time this runs, so
    # raising would lose it. A merchant with no suggested parent is simply one
    # the user sorts out themselves.
    assert _advise(error=LLMTemporarilyUnavailableError("overloaded")) is None


def test_every_category_the_application_accepts_is_explained_to_the_model() -> None:
    assert set(CATEGORY_DESCRIPTIONS) == set(MerchantCategory)


def test_the_instruction_lists_the_whole_vocabulary() -> None:
    instruction = build_system_instruction()

    for category in MerchantCategory:
        assert category.value in instruction


def test_the_instruction_teaches_the_parent_and_child_model() -> None:
    instruction = build_system_instruction()

    assert "PARENT" in instruction
    assert "CHILDREN" in instruction
    # The bias that decides every close call has to be in there, or the model
    # will merge whatever looks similar.
    assert "answer with no parent" in instruction


def test_the_prompt_offers_each_candidate_by_id_and_spelling() -> None:
    prompt = build_prompt(
        counterparty="BANCOLOMBIA NEQUI",
        kind=CounterpartyKind.UNKNOWN,
        candidates=_candidates(),
    )

    assert str(MERCHANT_ID.value) in prompt
    assert "NEQUI" in prompt
    assert "BANCOLOMBIA NEQUI" in prompt


def test_a_user_with_no_merchants_yet_is_told_so() -> None:
    prompt = build_prompt(
        counterparty="TIENDAS ARA",
        kind=CounterpartyKind.BUSINESS,
        candidates=[],
    )

    # Left implicit, a model invents an id to fill the field.
    assert "no merchants yet" in prompt
