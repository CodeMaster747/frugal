"""Forecasting HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.explanation import Explanation
from app.core.queue import GENERATE_FORECAST, dispatch
from app.modules.forecasting.service import ForecastService, InsufficientHistoryError

router = APIRouter(prefix="/forecast", tags=["forecasting"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> ForecastService:
    return ForecastService(db)


ServiceDep = Annotated[ForecastService, Depends(get_service)]


class SeriesPointOut(BaseModel):
    date: date
    p10: Decimal
    p50: Decimal
    p90: Decimal

    @field_serializer("p10", "p50", "p90", when_used="json")
    def _money(self, value: Decimal) -> str:
        return format(value, "f")


class MoneyOut(BaseModel):
    amount: Decimal
    currency: str = "INR"

    @field_serializer("amount", when_used="json")
    def _amount(self, value: Decimal) -> str:
        return format(value, "f")


class TroughOut(BaseModel):
    amount: Decimal
    on: date

    @field_serializer("amount", when_used="json")
    def _amount(self, value: Decimal) -> str:
        return format(value, "f")


class ForecastOut(BaseModel):
    horizon_days: int
    method: str
    observation_days: int
    confidence: Decimal
    projected_balance_end: MoneyOut
    trough: TroughOut | None
    shortfall_dates: list[date]
    series: list[SeriesPointOut]
    explanation: Explanation
    #: True when a better tier exists but needs the worker. The UI can say
    #: "refining…" rather than presenting tier 2 as final.
    refining: bool = False

    @field_serializer("confidence", when_used="json")
    def _confidence(self, value: Decimal) -> str:
        return format(value, "f")


def _insufficient(exc: InsufficientHistoryError) -> JSONResponse:
    """503 with the reason attached.

    Declining is a real answer, so it gets a real body. A bare status code would
    leave the client to invent an explanation, and the one it invents will be
    less accurate than the one we have.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "code": "INSUFFICIENT_DATA",
                "message": "Not enough transaction history to forecast.",
                "details": [{"field": "observation_days", "issue": exc.observation_days}],
                "caveats": exc.caveats,
            }
        },
    )


async def _build(
    service: ForecastService,
    user_id: uuid.UUID,
    *,
    horizon_days: int,
    extra_events: list[tuple[date, Decimal]] | None = None,
) -> ForecastOut:
    result, explanation, _ = await service.forecast(
        user_id,
        horizon_days=horizon_days,
        # The web process cannot import Prophet -- the API image does not
        # install it. Tier 3 is the worker's job.
        allow_prophet=False,
        extra_events=extra_events,
    )
    trough = result.trough

    refining = False
    if extra_events is None and service.wants_better_tier(result):
        # Queue by name so this module never imports the worker (ADR-001).
        dispatch(GENERATE_FORECAST, user_id=str(user_id), horizon_days=horizon_days)
        refining = True

    return ForecastOut(
        horizon_days=horizon_days,
        method=result.method,
        observation_days=result.observation_days,
        confidence=result.confidence,
        projected_balance_end=MoneyOut(amount=result.ending_balance),
        trough=TroughOut(amount=trough.p50, on=trough.on) if trough else None,
        shortfall_dates=result.shortfall_dates(),
        series=[SeriesPointOut(date=p.on, p10=p.p10, p50=p.p50, p90=p.p90) for p in result.series],
        explanation=explanation,
        refining=refining,
    )


@router.get("", response_model=ForecastOut)
async def get_forecast(
    current: CurrentUserDep,
    service: ServiceDep,
    horizon_days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> ForecastOut | JSONResponse:
    """Project cash flow over the horizon.

    Returns 503 `INSUFFICIENT_DATA` below two weeks of history rather than
    inventing a series.
    """
    try:
        return await _build(service, current.id, horizon_days=horizon_days)
    except InsufficientHistoryError as exc:
        return _insufficient(exc)


class ShortfallsOut(BaseModel):
    horizon_days: int
    method: str
    confidence: Decimal
    dates: list[date]
    trough: TroughOut | None

    @field_serializer("confidence", when_used="json")
    def _confidence(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/shortfalls", response_model=ShortfallsOut)
async def get_shortfalls(
    current: CurrentUserDep,
    service: ServiceDep,
    horizon_days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> ShortfallsOut | JSONResponse:
    """Days the balance could go negative.

    Computed from the *pessimistic* p10 path, not the median: the useful warning
    is "this could happen", and a shortfall shown only when it is more likely
    than not is a warning that arrives too late to act on.
    """
    try:
        result, _, _ = await service.forecast(
            current.id, horizon_days=horizon_days, allow_prophet=False
        )
    except InsufficientHistoryError as exc:
        return _insufficient(exc)

    trough = result.trough
    return ShortfallsOut(
        horizon_days=horizon_days,
        method=result.method,
        confidence=result.confidence,
        dates=result.shortfall_dates(),
        trough=TroughOut(amount=trough.p50, on=trough.on) if trough else None,
    )


class ScenarioEvent(BaseModel):
    """One hypothetical cash movement."""

    on: date
    amount: Decimal = Field(description="Positive for income, negative for spending")
    label: str = Field(default="", max_length=120)


class ScenarioIn(BaseModel):
    horizon_days: int = Field(default=90, ge=7, le=365)
    events: list[ScenarioEvent] = Field(default_factory=list, max_length=24)


@router.post("/scenario", response_model=ForecastOut)
async def run_scenario(
    payload: ScenarioIn, current: CurrentUserDep, service: ServiceDep
) -> ForecastOut | JSONResponse:
    """Forecast with hypothetical events laid over the projection.

    Never cached and never persisted: a scenario is a question, not the user's
    forecast, and storing it would make "what is my forecast" answer with
    someone's what-if about buying a car.
    """
    try:
        return await _build(
            service,
            current.id,
            horizon_days=payload.horizon_days,
            extra_events=[(event.on, event.amount) for event in payload.events],
        )
    except InsufficientHistoryError as exc:
        return _insufficient(exc)


class PatternOut(BaseModel):
    merchant: str
    kind: str
    cadence: str
    amount: Decimal
    monthly_equivalent: Decimal
    occurrences: int
    next_due_on: date
    confidence: Decimal
    amount_variance: Decimal

    @field_serializer(
        "amount", "monthly_equivalent", "confidence", "amount_variance", when_used="json"
    )
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/recurring", response_model=list[PatternOut])
async def get_recurring(current: CurrentUserDep, service: ServiceDep) -> list[PatternOut]:
    """Recurring commitments detected in the ledger.

    Exposed on its own because it is useful independently of the forecast: it is
    the answer to "what am I actually committed to every month", which most
    people cannot list from memory.
    """
    patterns = await service.detect_patterns(current.id)
    return [
        PatternOut(
            merchant=p.merchant,
            kind=p.kind,
            cadence=p.cadence,
            amount=p.amount,
            monthly_equivalent=p.monthly_equivalent,
            occurrences=p.occurrences,
            next_due_on=p.next_due_on,
            confidence=p.confidence,
            amount_variance=p.amount_variance,
        )
        for p in patterns
    ]
