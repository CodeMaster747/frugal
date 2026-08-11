"""Financial health — the module's public interface.

Gathers aggregates from analytics, measures the six sub-metrics, runs the
rubric, and persists the result. The interesting logic is all in `measure.py`
and `scoring.py`, which are pure; this file is the plumbing that gets numbers
into them and a row out.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now, utc_today
from app.core.logging import get_logger
from app.modules.analytics.service import AnalyticsService, Period
from app.modules.health import measure
from app.modules.health.models import HealthSnapshot
from app.modules.health.rubric import MetricKey
from app.modules.health.scoring import HealthInputs, HealthResult, score, subscore_of

logger = get_logger(__name__)

#: Months of history the score is computed over. Twelve so an annual premium or
#: a festival month is inside the window rather than a shock outside it.
LOOKBACK_MONTHS = 12

ZERO = Decimal("0")


class HealthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)

    async def compute(self, user_id: uuid.UUID) -> HealthResult:
        """Score this user's financial health, without persisting.

        Every aggregate is fetched through `AnalyticsService`, never by querying
        finance's tables here: health is a rubric over numbers, and keeping the
        SQL in one place is what lets `measure` and `scoring` stay pure.
        """
        window = await self.analytics.observation_window(user_id)
        cashflow = await self.analytics.cashflow(user_id, months=LOOKBACK_MONTHS)
        # Reserves, not raw liquidity: see `emergency_reserves`.
        reserves = await self.analytics.emergency_reserves(user_id)
        net_worth_trend = await self.analytics.net_worth_trend(user_id, months=LOOKBACK_MONTHS)
        debt_paid = await self.analytics.debt_service(
            user_id, Period.trailing_months(LOOKBACK_MONTHS)
        )
        budget_outcomes = await self.analytics.budget_outcomes(user_id, months=3)

        today = utc_today()
        metrics = measure.measure_all(
            cashflow=cashflow,
            liquid=reserves,
            debt_paid=debt_paid,
            budget_outcomes=budget_outcomes,
            net_worth_trend=net_worth_trend,
        )

        return score(
            HealthInputs(
                window_start=window.first_transaction_on or today,
                window_end=window.last_transaction_on or today,
                observation_days=window.observation_days,
                transaction_count=window.transaction_count,
                computed_at=utc_now(),
                metrics=metrics,
            )
        )

    async def compute_and_store(self, user_id: uuid.UUID) -> HealthResult:
        """Score, and record today's snapshot.

        Unscoreable results are computed but not stored: a row with a null score
        would break the NOT NULL contract the trend query depends on, and "no
        score yet" is not a data point on a trend line.
        """
        result = await self.compute(user_id)
        if result.score is not None and result.risk is not None:
            await self._upsert(user_id, result)
        return result

    async def _upsert(self, user_id: uuid.UUID, result: HealthResult) -> None:
        """Store today's snapshot, replacing any earlier one from today.

        An upsert rather than a check-then-insert: two concurrent requests on
        the same day would both see no row and both insert, and the unique
        constraint would turn the second into a 500 for something that is not
        an error.
        """
        stmt = insert(HealthSnapshot).values(
            id=uuid.uuid4(),
            user_id=user_id,
            snapshot_on=utc_today(),
            overall_score=result.score,
            risk_level=result.risk.value if result.risk else "",
            confidence=result.explanation.confidence,
            rubric_version=result.rubric_version,
            savings_rate_score=subscore_of(result, MetricKey.SAVINGS_RATE),
            emergency_fund_score=subscore_of(result, MetricKey.EMERGENCY_FUND),
            debt_to_income_score=subscore_of(result, MetricKey.DEBT_TO_INCOME),
            budget_discipline_score=subscore_of(result, MetricKey.BUDGET_DISCIPLINE),
            cashflow_stability_score=subscore_of(result, MetricKey.CASHFLOW_STABILITY),
            growth_score=subscore_of(result, MetricKey.GROWTH),
            explanation=result.explanation.model_dump(mode="json"),
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_health_snapshots",
                set_={
                    "overall_score": stmt.excluded.overall_score,
                    "risk_level": stmt.excluded.risk_level,
                    "confidence": stmt.excluded.confidence,
                    "rubric_version": stmt.excluded.rubric_version,
                    "savings_rate_score": stmt.excluded.savings_rate_score,
                    "emergency_fund_score": stmt.excluded.emergency_fund_score,
                    "debt_to_income_score": stmt.excluded.debt_to_income_score,
                    "budget_discipline_score": stmt.excluded.budget_discipline_score,
                    "cashflow_stability_score": stmt.excluded.cashflow_stability_score,
                    "growth_score": stmt.excluded.growth_score,
                    "explanation": stmt.excluded.explanation,
                },
            )
        )
        await self.session.flush()

    async def history(self, user_id: uuid.UUID, *, months: int = 12) -> Sequence[HealthSnapshot]:
        """Past snapshots, oldest first so the client can plot directly.

        Snapshots from an older rubric are returned rather than filtered: each
        carries its `rubric_version`, and hiding history because the weights
        changed would be a worse lie than showing it labelled.
        """
        cutoff = utc_today() - timedelta(days=int(months * 30.44))
        stmt = (
            select(HealthSnapshot)
            .where(HealthSnapshot.user_id == user_id, HealthSnapshot.snapshot_on >= cutoff)
            .order_by(HealthSnapshot.snapshot_on)
        )
        return (await self.session.execute(stmt)).scalars().all()
