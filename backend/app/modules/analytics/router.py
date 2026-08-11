"""Analytics HTTP layer.

Responses carry `data_version` so a client can tell whether what it is looking
at already reflects its own most recent write.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.clock import utc_today
from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.modules.analytics.service import AnalyticsService, Period

router = APIRouter(prefix="/analytics", tags=["analytics"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> AnalyticsService:
    return AnalyticsService(db)


ServiceDep = Annotated[AnalyticsService, Depends(get_service)]


class _Money(BaseModel):
    """Serialises every Decimal as a string (ADR-003)."""

    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _decimals(self, value: object) -> object:
        return format(value, "f") if isinstance(value, Decimal) else value


class PeriodOut(_Money):
    start: date
    end: date


class TotalsOut(_Money):
    income: Decimal
    expense: Decimal
    net: Decimal
    savings_rate: Decimal | None


class CategorySliceOut(_Money):
    category_id: str | None
    name: str
    slug: str
    amount: Decimal
    share_pct: Decimal
    previous_amount: Decimal
    change_pct: Decimal | None


class SeriesPointOut(_Money):
    period: str
    income: Decimal
    expense: Decimal
    net: Decimal


class TrendPointOut(_Money):
    period: str
    value: Decimal | None


class DashboardOut(_Money):
    period: PeriodOut
    net_worth: Decimal
    liquid: Decimal
    totals: TotalsOut
    previous_totals: TotalsOut
    top_categories: list[CategorySliceOut]
    cashflow: list[SeriesPointOut]
    net_worth_trend: list[TrendPointOut]
    account_count: int
    transaction_count: int
    data_version: int


def _totals_out(totals: Any) -> TotalsOut:
    return TotalsOut(
        income=totals.income,
        expense=totals.expense,
        net=totals.net,
        savings_rate=totals.savings_rate,
    )


def _resolve_period(month: str | None) -> Period:
    if not month:
        return Period.month_of(utc_today())
    year, mon = (int(part) for part in month.split("-", 1))
    return Period.month_of(date(year, mon, 1))


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    current: CurrentUserDep,
    service: ServiceDep,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> DashboardOut:
    """Every dashboard widget in one response.

    Composite on purpose: six parallel requests on a cold mobile connection is
    six round trips for one screen. Cached under the user's `data_version`, so
    any write invalidates it rather than a TTL leaving a window where the
    dashboard disagrees with the ledger.
    """
    period = _resolve_period(month)
    scope = f"dashboard:{period.start}:{period.end}"

    payload, version = await cache.get_or_compute(
        current.id,
        scope,
        lambda: _dashboard_payload(service, current.id, period),
    )
    return DashboardOut(**payload, data_version=version)


async def _dashboard_payload(
    service: AnalyticsService, user_id: Any, period: Period
) -> dict[str, Any]:
    """Shaped here rather than in the service so the cached value is plain JSON
    -- caching ORM-adjacent dataclasses would couple the cache format to the
    internal model."""
    data = await service.dashboard(user_id, period)
    return {
        "period": {"start": data.period.start, "end": data.period.end},
        "net_worth": data.net_worth,
        "liquid": data.liquid,
        "totals": asdict(data.totals)
        | {"net": data.totals.net, "savings_rate": data.totals.savings_rate},
        "previous_totals": asdict(data.previous_totals)
        | {"net": data.previous_totals.net, "savings_rate": data.previous_totals.savings_rate},
        "top_categories": [
            asdict(s) | {"category_id": str(s.category_id) if s.category_id else None}
            for s in data.top_categories
        ],
        "cashflow": [asdict(p) for p in data.cashflow],
        "net_worth_trend": [
            {"period": label, "value": value} for label, value in data.net_worth_trend
        ],
        "account_count": data.account_count,
        "transaction_count": data.transaction_count,
    }


@router.get("/categories", response_model=list[CategorySliceOut])
async def categories(
    current: CurrentUserDep,
    service: ServiceDep,
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> list[CategorySliceOut]:
    """Expense breakdown with period-over-period change."""
    slices = await service.categories(current.id, _resolve_period(month))
    return [
        CategorySliceOut(
            category_id=str(s.category_id) if s.category_id else None,
            name=s.name,
            slug=s.slug,
            amount=s.amount,
            share_pct=s.share_pct,
            previous_amount=s.previous_amount,
            change_pct=s.change_pct,
        )
        for s in slices
    ]


@router.get("/cashflow", response_model=list[SeriesPointOut])
async def cashflow(
    current: CurrentUserDep,
    service: ServiceDep,
    months: int = Query(default=6, ge=1, le=24),
) -> list[SeriesPointOut]:
    points = await service.cashflow(current.id, months)
    return [SeriesPointOut.model_validate(p) for p in points]


@router.get("/net-worth", response_model=list[TrendPointOut])
async def net_worth_trend(
    current: CurrentUserDep,
    service: ServiceDep,
    months: int = Query(default=12, ge=1, le=36),
) -> list[TrendPointOut]:
    trend = await service.net_worth_trend(current.id, months)
    return [TrendPointOut(period=label, value=value) for label, value in trend]


@router.get("/savings-rate", response_model=list[TrendPointOut])
async def savings_rate_trend(
    current: CurrentUserDep,
    service: ServiceDep,
    months: int = Query(default=12, ge=1, le=36),
) -> list[TrendPointOut]:
    """Monthly savings rate.

    `value` is null in a month with no income -- the rate is undefined, and
    plotting zero would draw a cliff the user never experienced.
    """
    trend = await service.savings_rate_trend(current.id, months)
    return [TrendPointOut(period=label, value=value) for label, value in trend]
