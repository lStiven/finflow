"""`GET /ingestion/model-access`: whether the model still reads the caller's
unrecognised mail.

What the suspension notice on screen is drawn from. It reads the record the
parse worker enforces, not the alert that announced it, so the notice is
right even if that alert never arrived.
"""

from __future__ import annotations

import functools
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.model_access import (
    GetModelAccessUseCase,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.model_standing_dynamodb import (  # noqa: E501
    DynamoDBModelStandings,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


class ModelAccessResponse(BaseModel):
    # `open`: the model reads what no template does. `paused`: it does not
    # until `paused_until`. `revoked`: it does not until support reinstates
    # it. Templates read in all three.
    access: Literal["open", "paused", "revoked"]
    # Epoch seconds; only while paused.
    paused_until: int | None
    # Strikes given within the last 90 days.
    strikes: int


@functools.lru_cache(maxsize=1)
def _build_use_case() -> GetModelAccessUseCase:
    return GetModelAccessUseCase(
        standings=DynamoDBModelStandings(
            client=get_dynamodb_client(),
            table_name=get_ingestion_settings().notifications_table,
        ),
    )


def get_model_access_use_case() -> GetModelAccessUseCase:
    return _build_use_case()


@router.get("/model-access", response_model=ModelAccessResponse)
def get_model_access(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[GetModelAccessUseCase, Depends(get_model_access_use_case)],
    response: Response,
) -> ModelAccessResponse:
    """Whether the model reads the caller's unrecognised mail, and until when not."""
    # One person's standing: nothing in between keeps a copy.
    response.headers["Cache-Control"] = "no-store"
    view = use_case.execute(user_id=user_id)

    return ModelAccessResponse(
        access=view.access.value,
        paused_until=(
            None if view.paused_until is None else view.paused_until.as_epoch_seconds()
        ),
        strikes=view.strikes,
    )
