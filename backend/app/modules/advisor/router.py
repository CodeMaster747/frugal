"""Purchase advisor HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import NotFoundError
from app.core.explanation import Explanation
from app.modules.advisor import rubric as rubric_module
from app.modules.advisor.service import AdviceResult, AdvisorService

router = APIRouter(prefix="/advisor", tags=["advisor"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AdvisorService:
    return AdvisorService(db)


ServiceDep = Annotated[AdvisorService, Depends(get_service)]


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class OfferOut(BaseModel):
    external_id: str
    name: str
    category: str
    price: Decimal
    currency: str
    brand: str | None = None
    seller: str = ""

    @field_serializer("price", when_used="json")
    def _price(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/products/search", response_model=list[OfferOut])
async def search_products(
    current: CurrentUserDep,
    service: ServiceDep,
    q: Annotated[str, Query(min_length=1, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> list[OfferOut]:
    """Search the catalogue.

    An empty list is a normal outcome, not an error — the client falls back to
    manual entry. A user who knows the price should never be blocked by a
    catalogue that has not heard of the thing they are buying.
    """
    del current
    offers = await service.search(q, limit=limit)
    return [
        OfferOut(
            external_id=o.external_id,
            name=o.name,
            category=o.category,
            price=o.price,
            currency=o.currency,
            brand=o.brand,
            seller=o.seller,
        )
        for o in offers
    ]


class SnapshotOut(BaseModel):
    liquid_savings: Decimal
    emergency_fund_months: Decimal
    health_score: Decimal | None
    savings_rate: Decimal | None

    @field_serializer(
        "liquid_savings",
        "emergency_fund_months",
        "health_score",
        "savings_rate",
        when_used="json",
    )
    def _decimals(self, value: Decimal | None) -> str | None:
        return _money(value)


class SimulationOut(BaseModel):
    before: SnapshotOut
    after: SnapshotOut
    goal_impact: list[dict[str, Any]]
    forecast_trough_after: Decimal | None
    #: `health_score` after is an estimate, not a re-run of the health engine.
    #: Saying so is cheaper than the user discovering it.
    health_score_after_is_estimated: bool = True

    @field_serializer("forecast_trough_after", when_used="json")
    def _trough(self, value: Decimal | None) -> str | None:
        return _money(value)


class EmiOptionOut(BaseModel):
    tenure_months: int
    monthly: Decimal
    total_payable: Decimal
    total_interest: Decimal
    annual_rate: Decimal
    new_debt_ratio: Decimal
    #: Interest as a share of the cash price. The number a monthly instalment is
    #: designed to keep out of view.
    interest_share: Decimal
    is_serviceable: bool

    @field_serializer(
        "monthly",
        "total_payable",
        "total_interest",
        "annual_rate",
        "new_debt_ratio",
        "interest_share",
        when_used="json",
    )
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class AlternativeOut(BaseModel):
    external_id: str
    name: str
    price: Decimal
    affordability_score: Decimal
    verdict_if_chosen: str

    @field_serializer("price", "affordability_score", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class ConstraintOut(BaseModel):
    code: str
    caps_at: str
    message: str


class AdviceOut(BaseModel):
    id: uuid.UUID | None = None
    product_query: str
    price: Decimal
    currency: str
    verdict: str
    affordability_score: Decimal
    confidence: Decimal
    rubric_version: str
    affordable_from: date | None
    #: What the score alone would have said. Present so a capped verdict can be
    #: shown as a downgrade with a reason rather than an unexplained refusal.
    score_verdict: str
    constraints: list[ConstraintOut]
    simulation: SimulationOut
    emi_options: list[EmiOptionOut]
    alternatives: list[AlternativeOut]
    explanation: Explanation

    @field_serializer("price", "affordability_score", "confidence", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class EvaluateIn(BaseModel):
    product_query: Annotated[str, Field(min_length=1, max_length=255)]
    price: Annotated[Decimal, Field(gt=0, le=Decimal("100000000"))]
    currency: str = "INR"
    external_id: str | None = Field(default=None, max_length=160)
    consider_emi: bool = True


def _to_out(result: AdviceResult, stored_id: uuid.UUID | None = None) -> AdviceOut:
    evaluation = result.evaluation
    return AdviceOut(
        id=stored_id,
        product_query=result.offer.name,
        price=result.offer.price,
        currency=result.offer.currency,
        verdict=evaluation.verdict.value,
        affordability_score=evaluation.score,
        confidence=evaluation.confidence,
        rubric_version=evaluation.rubric_version,
        affordable_from=evaluation.affordable_from,
        score_verdict=evaluation.score_verdict.value,
        constraints=[
            ConstraintOut(code=c.code.value, caps_at=c.caps_at.value, message=c.message)
            for c in evaluation.constraints
        ],
        simulation=SimulationOut(
            before=SnapshotOut(**result.before),
            after=SnapshotOut(**result.after),
            goal_impact=result.goal_impact,
            forecast_trough_after=result.forecast_trough_after,
        ),
        emi_options=[
            EmiOptionOut(
                tenure_months=o.tenure_months,
                monthly=o.monthly,
                total_payable=o.total_payable,
                total_interest=o.total_interest,
                annual_rate=o.annual_rate,
                new_debt_ratio=o.new_debt_ratio,
                interest_share=o.interest_share,
                is_serviceable=o.is_serviceable,
            )
            for o in result.emi_options
        ],
        alternatives=[
            AlternativeOut(
                external_id=a.offer.external_id,
                name=a.offer.name,
                price=a.offer.price,
                affordability_score=a.score,
                verdict_if_chosen=a.verdict.value,
            )
            for a in result.alternatives
        ],
        explanation=evaluation.explanation,
    )


@router.post("/evaluate", response_model=AdviceOut)
async def evaluate_purchase(
    payload: EvaluateIn, current: CurrentUserDep, service: ServiceDep
) -> AdviceOut:
    """Should I buy this?

    The flagship. Every claim in the response traces to a factor, a `wait`
    always carries a date, the EMI path is priced with its total interest, and
    the caveats state what the model does not know.
    """
    result = await service.advise(
        current.id,
        query=payload.product_query,
        price=payload.price,
        currency=payload.currency,
        external_id=payload.external_id,
        consider_emi=payload.consider_emi,
    )
    stored = await service.store(current.id, result)
    return _to_out(result, stored.id)


class EvaluationSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_query: str
    price: Decimal
    currency: str
    verdict: str
    affordability_score: Decimal
    affordable_from: date | None
    created_at: datetime

    @field_serializer("price", "affordability_score", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/evaluations", response_model=list[EvaluationSummaryOut])
async def list_evaluations(
    current: CurrentUserDep,
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[EvaluationSummaryOut]:
    """Past decisions, so one can be revisited rather than re-argued."""
    rows = await service.history(current.id, limit=limit)
    return [EvaluationSummaryOut.model_validate(row) for row in rows]


@router.get("/evaluations/{evaluation_id}")
async def get_evaluation(
    evaluation_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> dict[str, Any]:
    """A stored evaluation, exactly as it was answered.

    Returned from the persisted row rather than recomputed: the point of storing
    it is that revisiting a decision shows what was actually said at the time,
    not what today's numbers would say.
    """
    row = await service.get(current.id, evaluation_id)
    if row is None:
        raise NotFoundError("Evaluation")

    return {
        "id": str(row.id),
        "product_query": row.product_query,
        "price": format(row.price, "f"),
        "currency": row.currency,
        "verdict": row.verdict,
        "affordability_score": format(row.affordability_score, "f"),
        "confidence": format(row.confidence, "f"),
        "rubric_version": row.rubric_version,
        "affordable_from": row.affordable_from.isoformat() if row.affordable_from else None,
        "simulation": row.simulation,
        "emi_options": row.emi_options or [],
        "alternatives": row.alternatives or [],
        "explanation": row.explanation,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/rubric")
async def published_rubric(current: CurrentUserDep) -> dict[str, object]:
    """The weights, bands, and hard constraints, published.

    A recommendation the user cannot interrogate is an instruction, and this
    product is not in the business of issuing those (ADR-005).
    """
    del current
    return rubric_module.published()
