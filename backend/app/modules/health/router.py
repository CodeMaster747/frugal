"""Financial health HTTP layer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.explanation import Explanation
from app.modules.health import rubric as rubric_module
from app.modules.health.service import HealthService

router = APIRouter(prefix="/health-score", tags=["health"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> HealthService:
    return HealthService(db)


ServiceDep = Annotated[HealthService, Depends(get_service)]


class HealthScoreOut(BaseModel):
    """The current score.

    `score` and `risk_level` are nullable because "not enough history" is a real
    answer. The `explanation` is always present and always carries the caveats
    that say why -- a client that renders it generically needs no special case.
    """

    score: Decimal | None
    risk_level: str | None
    confidence: Decimal
    rubric_version: str
    explanation: Explanation

    @field_serializer("score", "confidence", when_used="json")
    def _decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


@router.get("", response_model=HealthScoreOut)
async def current(current_user: CurrentUserDep, service: ServiceDep) -> HealthScoreOut:
    """Today's health score, with its full decomposition.

    Computing on read rather than serving a cached row: the score reflects the
    ledger as it stands, and a user who has just categorised twenty transactions
    expects the number to move. The snapshot written as a side effect is for the
    trend line, not for this response.
    """
    result = await service.compute_and_store(current_user.id)
    return HealthScoreOut(
        score=result.score,
        risk_level=result.risk.value if result.risk else None,
        confidence=result.explanation.confidence,
        rubric_version=result.rubric_version,
        explanation=result.explanation,
    )


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshot_on: date
    overall_score: Decimal
    risk_level: str
    confidence: Decimal
    rubric_version: str
    savings_rate_score: Decimal
    emergency_fund_score: Decimal
    debt_to_income_score: Decimal
    budget_discipline_score: Decimal
    cashflow_stability_score: Decimal
    growth_score: Decimal

    @field_serializer(
        "overall_score",
        "confidence",
        "savings_rate_score",
        "emergency_fund_score",
        "debt_to_income_score",
        "budget_discipline_score",
        "cashflow_stability_score",
        "growth_score",
        when_used="json",
    )
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/history", response_model=list[SnapshotOut])
async def history(
    current_user: CurrentUserDep,
    service: ServiceDep,
    months: Annotated[int, Query(ge=1, le=60)] = 12,
) -> list[SnapshotOut]:
    """Score history, oldest first.

    Each row carries the `rubric_version` that produced it. A client plotting a
    trend across a rubric change should say so rather than draw one line.
    """
    rows = await service.history(current_user.id, months=months)
    return [SnapshotOut.model_validate(row) for row in rows]


@router.get("/rubric")
async def published_rubric(current_user: CurrentUserDep) -> dict[str, object]:
    """The weights and bands, published.

    Exists so the scoring model is inspectable without reverse-engineering it
    from outputs. A user who disagrees with their score can read exactly what
    produced it -- which is the difference between a tool and an oracle
    (ADR-005).
    """
    del current_user  # authentication only; the rubric is the same for everyone
    return rubric_module.published()
