"""Decision simulator HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import ValidationError
from app.core.explanation import Explanation
from app.modules.simulator import scenarios as scenario_lib
from app.modules.simulator.engine import Result
from app.modules.simulator.service import SimulatorService

router = APIRouter(prefix="/simulator", tags=["simulator"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SimulatorService:
    return SimulatorService(db)


ServiceDep = Annotated[SimulatorService, Depends(get_service)]


@router.get("/templates")
async def list_templates(current: CurrentUserDep) -> dict[str, Any]:
    """The named scenarios, and what they ask for.

    Templates are convenience, not capability — each one builds the same
    `Change` list a user can assemble by hand, and the engine cannot tell the
    difference. Published so a client can render the form without hard-coding
    the fields.
    """
    del current
    return {"templates": scenario_lib.published_templates()}


class ChangeIn(BaseModel):
    kind: scenario_lib.ChangeKind
    label: Annotated[str, Field(min_length=1, max_length=120)]
    amount: Annotated[Decimal, Field(ge=0, le=Decimal("100000000"))]
    starts_in_months: Annotated[int, Field(ge=0, le=120)] = 0
    lasts_months: Annotated[int, Field(ge=1, le=600)] | None = None
    is_reduction: bool = False


class ScenarioIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] = "Scenario"
    #: Either a template key with values, or an explicit list of changes.
    template: str | None = None
    values: dict[str, Decimal] = Field(default_factory=dict)
    changes: list[ChangeIn] = Field(default_factory=list, max_length=12)
    horizon_months: Annotated[int, Field(ge=1, le=120)] = 24


class SnapshotOut(BaseModel):
    liquid_reserves: Decimal
    monthly_surplus: Decimal
    emergency_fund_months: Decimal

    @field_serializer(
        "liquid_reserves", "monthly_surplus", "emergency_fund_months", when_used="json"
    )
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class PointOut(BaseModel):
    month: int
    on: date
    reserves: Decimal
    monthly_surplus: Decimal

    @field_serializer("reserves", "monthly_surplus", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class ScenarioOut(BaseModel):
    name: str
    outlook: str
    before: SnapshotOut
    after: SnapshotOut
    months_until_shortfall: int | None
    trough_months_of_cover: Decimal
    series: list[PointOut]
    explanation: Explanation

    @field_serializer("trough_months_of_cover", when_used="json")
    def _cover(self, value: Decimal) -> str:
        return format(value, "f")


def _to_out(result: Result) -> ScenarioOut:
    return ScenarioOut(
        name=result.scenario_name,
        outlook=result.outlook,
        # Explicit rather than `**dataclasses.asdict`: `Snapshot` is slotted, so
        # it has no `__dict__`, and naming the fields keeps a renamed one a type
        # error rather than a silent omission.
        before=SnapshotOut(
            liquid_reserves=result.before.liquid_reserves,
            monthly_surplus=result.before.monthly_surplus,
            emergency_fund_months=result.before.emergency_fund_months,
        ),
        after=SnapshotOut(
            liquid_reserves=result.after.liquid_reserves,
            monthly_surplus=result.after.monthly_surplus,
            emergency_fund_months=result.after.emergency_fund_months,
        ),
        months_until_shortfall=result.months_until_shortfall,
        trough_months_of_cover=result.trough_months_of_cover,
        series=[
            PointOut(month=p.month, on=p.on, reserves=p.reserves, monthly_surplus=p.monthly_surplus)
            for p in result.projection.points
        ],
        explanation=result.explanation,
    )


async def _build(
    payload: ScenarioIn, service: SimulatorService, user_id: uuid.UUID
) -> scenario_lib.Scenario:
    if payload.template:
        position = await service.position(user_id)
        try:
            return scenario_lib.from_template(
                payload.template,
                payload.values,
                position=position,
                horizon_months=payload.horizon_months,
            )
        except KeyError as exc:
            raise ValidationError(f"Unknown scenario template: {payload.template}") from exc

    if not payload.changes:
        raise ValidationError("A scenario needs either a template or at least one change")

    return scenario_lib.Scenario(
        name=payload.name,
        changes=tuple(
            scenario_lib.Change(
                kind=c.kind,
                label=c.label,
                amount=c.amount,
                starts_in_months=c.starts_in_months,
                lasts_months=c.lasts_months,
                is_reduction=c.is_reduction,
            )
            for c in payload.changes
        ),
        horizon_months=payload.horizon_months,
    )


@router.post("/run", response_model=ScenarioOut)
async def run_scenario(
    payload: ScenarioIn, current: CurrentUserDep, service: ServiceDep
) -> ScenarioOut:
    """Project one scenario, with an `Explanation`.

    Nothing is stored: a scenario is a question, and the answer changes with the
    ledger. Serving a saved one later would answer "what if" with yesterday's
    numbers.
    """
    scenario = await _build(payload, service, current.id)
    return _to_out(await service.run(current.id, scenario))


class CompareIn(BaseModel):
    scenarios: list[ScenarioIn] = Field(min_length=2, max_length=4)


class ComparisonOut(BaseModel):
    results: list[ScenarioOut]
    #: The one leaving the most room if things go wrong — not "the best", which
    #: depends on what the user wants and the software does not know.
    safest: str | None


@router.post("/compare", response_model=ComparisonOut)
async def compare_scenarios(
    payload: CompareIn, current: CurrentUserDep, service: ServiceDep
) -> ComparisonOut:
    """Several scenarios against one measured position."""
    built = [await _build(item, service, current.id) for item in payload.scenarios]
    comparison = await service.run_many(current.id, built)

    return ComparisonOut(
        results=[_to_out(r) for r in comparison.results],
        safest=comparison.safest.scenario_name if comparison.safest else None,
    )
