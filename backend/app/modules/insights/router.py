"""Insights HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import NotFoundError
from app.core.explanation import Explanation
from app.modules.insights.models import Severity
from app.modules.insights.service import InsightService

router = APIRouter(prefix="/insights", tags=["insights"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> InsightService:
    return InsightService(db)


ServiceDep = Annotated[InsightService, Depends(get_service)]


class InsightOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    insight_type: str
    severity: str
    title: str
    body: str
    impact_amount: Decimal | None
    materiality: Decimal
    confidence: Decimal
    period_start: date
    period_end: date
    explanation: Explanation
    read_at: datetime | None
    dismissed_at: datetime | None
    subject_id: uuid.UUID | None
    created_at: datetime

    @field_serializer("impact_amount", "materiality", "confidence", when_used="json")
    def _decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class FeedOut(BaseModel):
    data: list[InsightOut]
    unread_count: int


@router.get("", response_model=FeedOut)
async def feed(
    current: CurrentUserDep,
    service: ServiceDep,
    severity: Severity | None = None,
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> FeedOut:
    """Active insights, ranked by materiality.

    Not paginated: the feed is capped at a size a person will actually read, and
    a cursor over a list of eight would be ceremony. If it ever needs paging,
    that is a signal the ranking is not doing its job.
    """
    rows = await service.feed(current.id, severity=severity, unread_only=unread, limit=limit)
    return FeedOut(
        data=[InsightOut.model_validate(row) for row in rows],
        unread_count=await service.unread_count(current.id),
    )


class RefreshOut(BaseModel):
    detected: int
    suppressed: int
    created: int


@router.post("/refresh", response_model=RefreshOut, status_code=status.HTTP_200_OK)
async def refresh(current: CurrentUserDep, service: ServiceDep) -> RefreshOut:
    """Regenerate insights now.

    Synchronous and 200 rather than 202: the detectors run over aggregates the
    database computes in milliseconds, so queueing this would add a job, a poll,
    and a spinner to something already faster than the round trip. It moves to
    the scheduler when it stops being cheap, and the API shape survives that
    because the client already treats the feed as the source of truth.
    """
    return RefreshOut.model_validate(await service.refresh(current.id))


@router.post("/{insight_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(insight_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep) -> None:
    if not await service.mark_read(current.id, insight_id):
        raise NotFoundError("Insight")


@router.post("/{insight_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(insight_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep) -> None:
    """Dismiss, and suppress this finding for a cooling period."""
    if not await service.dismiss(current.id, insight_id):
        raise NotFoundError("Insight")
