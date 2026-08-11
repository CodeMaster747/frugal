"""Categorisation HTTP layer."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.modules.categorization.service import CategorizationService

router = APIRouter(prefix="/categorization", tags=["categorization"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> CategorizationService:
    return CategorizationService(db)


ServiceDep = Annotated[CategorizationService, Depends(get_service)]


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    slug: str
    confidence: Decimal
    #: `user_rule`, `seed_exact`, `seed_substring`, or `model` -- so the UI can
    #: say *why*, which is the difference between a suggestion and a black box.
    source: str
    version: str
    matched_on: str | None = None

    @field_serializer("confidence", when_used="json")
    def _confidence(self, value: Decimal) -> str:
        return format(value, "f")


class SuggestResponse(BaseModel):
    merchant: str
    suggestion: SuggestionOut | None
    #: Null suggestion is a real answer, not an error: nothing was confident
    #: enough, and an empty category beats a wrong one (FR-5.4).
    reason: str


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(
    current: CurrentUserDep,
    service: ServiceDep,
    merchant: Annotated[str, Query(min_length=1, max_length=255)],
) -> SuggestResponse:
    """Suggest a category for a merchant string."""
    from app.modules.finance.service import normalize_merchant

    normalized = normalize_merchant(merchant) or merchant.lower()
    suggestion = await service.suggest(current.id, normalized)

    return SuggestResponse(
        merchant=normalized,
        suggestion=SuggestionOut.model_validate(suggestion) if suggestion else None,
        reason=(
            f"matched by {suggestion.source}"
            if suggestion
            else "no rule matched and the model was not confident enough"
        ),
    )


class RetrainResponse(BaseModel):
    version: str
    examples: int
    categories: int
    feature_version: str


@router.post("/retrain", response_model=RetrainResponse)
async def retrain(current: CurrentUserDep, service: ServiceDep) -> RetrainResponse:
    """Refit on the seed corpus plus every accumulated correction.

    Exposed so the loop is demonstrable end to end: correct a few transactions,
    retrain, and see the model pick them up. In production this is a scheduled
    task rather than a request.
    """
    del current  # authentication only; retraining is global
    return RetrainResponse.model_validate(await service.retrain())
