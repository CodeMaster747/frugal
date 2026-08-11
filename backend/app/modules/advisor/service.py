"""The purchase advisor — where every prior milestone converges.

One question, answered from six engines: the ledger (M2), analytics (M3),
categorisation (M5), financial health (M6), the forecast (M7), and the price
catalogue. The advisor itself adds a rubric and an EMI model; almost everything
else it says is assembled from work already done.

**The independent engines run concurrently, each on its own session.** Health
and the forecast take tens of milliseconds apiece and neither depends on the
other's result, so running them in sequence would spend two round trips to
answer one question.

The subtlety is that an `AsyncSession` is **not** safe to share across
concurrent tasks, even for reads. An earlier version of this file gathered
seven coroutines on the request's session with a comment asserting that reads
made it safe; they do not. Concurrent `execute()` calls interleave on the same
connection and leave the session's transaction state inconsistent, which
surfaces as `IllegalStateChangeError` from an unrelated line during teardown --
a failure whose stack trace points nowhere near the cause.

So each concurrent branch gets its own session and returns plain data. The
request's own session is used only for the sequential work and the final write.

**This costs connections.** At its peak an evaluation holds three: the request's
session and one per gathered engine. The pool is sized for that in
`core/config.py`, and it is the reason the setting carries a comment rather than
a default.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import PriceProvider, ProductOffer
from app.adapters.pricing import get_price_provider
from app.adapters.pricing.seed_catalog import ManualEntryProvider
from app.core.clock import utc_now, utc_today
from app.core.explanation import Explanation
from app.core.logging import get_logger
from app.modules.advisor import emi as emi_model
from app.modules.advisor.models import PurchaseEvaluation
from app.modules.advisor.rubric import DEBT_CEILING, Verdict
from app.modules.advisor.scoring import Evaluation, FinancialPicture, evaluate
from app.modules.analytics.service import AnalyticsService, Period

logger = get_logger(__name__)

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Horizon used when checking what a purchase does to the projected trough.
IMPACT_HORIZON_DAYS = 90


@dataclass(frozen=True, slots=True)
class Alternative:
    offer: ProductOffer
    score: Decimal
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class AdviceResult:
    """Everything the response needs, assembled once."""

    evaluation: Evaluation
    offer: ProductOffer
    picture: FinancialPicture
    before: dict[str, Any]
    after: dict[str, Any]
    goal_impact: list[dict[str, Any]]
    emi_options: list[emi_model.EmiOption]
    alternatives: list[Alternative]
    forecast_trough_after: Decimal | None

    @property
    def explanation(self) -> Explanation:
        return self.evaluation.explanation


class AdvisorService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: PriceProvider | None = None,
    ) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)
        # Injected so a test can substitute a fake catalogue; otherwise the
        # configured one, shared with the market module so both quote the same
        # price for the same product (ADR-004).
        self.provider: PriceProvider = provider or get_price_provider()

    # -- catalogue ---------------------------------------------------------

    async def search(self, query: str, *, limit: int = 10) -> list[ProductOffer]:
        return await self.provider.search(query, limit=limit)

    # -- the financial picture ---------------------------------------------

    async def picture_for(self, user_id: uuid.UUID) -> FinancialPicture:
        """Measure the user's position.

        The two heavy engines run concurrently on their own sessions; the cheap
        analytics queries run in sequence on the request's. See the module
        docstring for why the session cannot simply be shared.

        Imports of `health` and `forecasting` are local: both are heavier than
        the advisor needs at module scope, and keeping them here means importing
        `advisor` does not drag two more engines into memory.
        """
        from app.core.database import get_async_session_factory
        from app.modules.forecasting.service import ForecastService, InsufficientHistoryError
        from app.modules.health.service import HealthService

        today = utc_today()
        window = Period.trailing_months(12)
        factory = get_async_session_factory()

        async def forecast_trough() -> tuple[Decimal | None, tuple[str, ...]]:
            async with factory() as session:
                try:
                    result, _, _ = await ForecastService(session).forecast(
                        user_id, horizon_days=IMPACT_HORIZON_DAYS, allow_prophet=False
                    )
                except InsufficientHistoryError:
                    # A new user gets advice without a forecast rather than no
                    # advice. The missing factor costs confidence, and the
                    # caveat says so.
                    return None, ()
                trough = result.trough
                return (trough.p50 if trough else None), tuple(result.caveats)

        async def health_score() -> Decimal | None:
            async with factory() as session:
                # `compute`, not `compute_and_store`: pricing a laptop should
                # not write a health snapshot as a side effect.
                return (await HealthService(session).compute(user_id)).score

        (trough, forecast_caveats), health = await asyncio.gather(forecast_trough(), health_score())

        observation = await self.analytics.observation_window(user_id)
        cashflow = await self.analytics.cashflow(user_id, months=12)
        reserves = await self.analytics.emergency_reserves(user_id)
        debt_paid = await self.analytics.debt_service(user_id, window)
        goals = await self.analytics.goal_progress(user_id)

        active = [p for p in cashflow if p.income > 0 or p.expense > 0]
        months = Decimal(len(active)) if active else Decimal(1)
        income = sum((p.income for p in active), ZERO) / months
        expenses = sum((p.expense for p in active), ZERO) / months
        total_income = sum((p.income for p in active), ZERO)

        savings_rate = (
            ((total_income - sum((p.expense for p in active), ZERO)) / total_income).quantize(
                Decimal("0.0001")
            )
            if total_income > 0
            else None
        )

        top_goal = goals[0] if goals else None

        return FinancialPicture(
            liquid_reserves=reserves,
            monthly_income=income.quantize(CENTS),
            monthly_expenses=expenses.quantize(CENTS),
            savings_rate=savings_rate,
            debt_service=(debt_paid / Decimal(12)).quantize(CENTS),
            forecast_trough=trough,
            health_score=health,
            top_goal_name=top_goal.name if top_goal else "",
            observation_days=observation.observation_days,
            window_start=observation.first_transaction_on or today,
            window_end=observation.last_transaction_on or today,
            forecast_caveats=forecast_caveats,
        )

    # -- evaluation --------------------------------------------------------

    async def advise(
        self,
        user_id: uuid.UUID,
        *,
        query: str,
        price: Decimal,
        currency: str = "INR",
        external_id: str | None = None,
        consider_emi: bool = True,
    ) -> AdviceResult:
        """Answer "should I buy this?" with the reasoning attached."""
        today = utc_today()
        picture = await self.picture_for(user_id)

        offer = None
        if external_id:
            offer = await self.provider.get(external_id)
        if offer is None:
            offer = ManualEntryProvider.offer(query, price, currency)
        else:
            # The user's price wins over the catalogue's: they may have found a
            # deal, and arguing with them about the price is not the job.
            offer = ProductOffer(
                external_id=offer.external_id,
                name=offer.name,
                category=offer.category,
                price=price,
                currency=currency,
                brand=offer.brand,
                seller=offer.seller,
                specs=offer.specs,
                provider=offer.provider,
            )

        # The goal delay depends on the price, which the picture was measured
        # before knowing. `FinancialPicture` is frozen, so rebuild it.
        picture = _with_goal_delay(picture, self._goal_delay(picture, price))

        options = (
            emi_model.options(
                price,
                monthly_income=picture.monthly_income,
                existing_debt_service=picture.debt_service,
            )
            if consider_emi
            else []
        )
        best = emi_model.best_option(options, debt_ceiling=DEBT_CEILING)

        evaluation = evaluate(
            picture,
            price,
            today=today,
            computed_at=utc_now(),
            emi_available=best is not None,
        )

        alternatives = await self._alternatives(offer, picture, today)

        return AdviceResult(
            evaluation=evaluation,
            offer=offer,
            picture=picture,
            before=self._snapshot(picture, ZERO),
            after=self._snapshot(picture, price),
            goal_impact=self._goal_impact(picture, price),
            emi_options=options,
            alternatives=alternatives,
            forecast_trough_after=(
                picture.forecast_trough - price if picture.forecast_trough is not None else None
            ),
        )

    def _snapshot(self, picture: FinancialPicture, spend: Decimal) -> dict[str, Any]:
        """The user's position before or after the purchase.

        `health_score` after is deliberately *not* recomputed by re-running the
        health engine on hypothetical data: that would need a whole simulated
        ledger. It is adjusted by the emergency-fund factor's share of the
        rubric, which is the part a one-off purchase actually moves, and the
        response labels it an estimate.
        """
        reserves = picture.liquid_reserves - spend
        monthly = picture.monthly_expenses if picture.monthly_expenses > 0 else Decimal(1)
        months = (reserves / monthly).quantize(CENTS)

        health = picture.health_score
        if health is not None and spend > 0:
            before_months = picture.emergency_fund_months
            # The health rubric weights emergency fund at 0.25 and scores it
            # 0-100, so a drop of N months costs at most 25 points.
            drop = min(before_months, max(ZERO, before_months - months))
            health = max(
                ZERO,
                (health - (drop / Decimal(6)) * Decimal(25)).quantize(CENTS),
            )

        return {
            "liquid_savings": reserves,
            "emergency_fund_months": months,
            "health_score": health,
            "savings_rate": picture.savings_rate,
        }

    def _goal_delay(self, picture: FinancialPicture, price: Decimal) -> int:
        """Days the top-priority goal slips if this money is spent instead.

        At the current rate of saving, spending X delays every goal by the time
        it takes to save X again. Simple, and it is what actually happens.
        """
        surplus = picture.monthly_surplus
        if surplus <= 0 or price <= 0:
            return 0
        months = price / surplus
        return int((months * Decimal("30.44")).to_integral_value(rounding=ROUND_HALF_UP))

    def _goal_impact(self, picture: FinancialPicture, price: Decimal) -> list[dict[str, Any]]:
        delay = self._goal_delay(picture, price)
        if not picture.top_goal_name or delay == 0:
            return []
        return [{"goal": picture.top_goal_name, "delay_days": delay, "priority": 1}]

    async def _alternatives(
        self, offer: ProductOffer, picture: FinancialPicture, today: date
    ) -> list[Alternative]:
        """Cheaper options, each scored so the trade-off is visible.

        Listing cheaper products without scoring them would leave the user to
        guess whether the cheaper one is actually affordable. The point is not
        "here is something cheaper" but "here is something you could buy today".
        """
        if not offer.external_id:
            return []

        candidates = await self.provider.alternatives(
            offer, max_price=offer.price * Decimal("0.85"), limit=3
        )

        scored: list[Alternative] = []
        for candidate in candidates:
            result = evaluate(
                picture, candidate.price, today=today, computed_at=utc_now(), emi_available=True
            )
            scored.append(Alternative(offer=candidate, score=result.score, verdict=result.verdict))
        return scored

    # -- persistence -------------------------------------------------------

    async def store(self, user_id: uuid.UUID, result: AdviceResult) -> PurchaseEvaluation:
        before, after = result.before, result.after
        health_delta = (
            (after["health_score"] - before["health_score"])
            if before["health_score"] is not None and after["health_score"] is not None
            else None
        )

        row = PurchaseEvaluation(
            user_id=user_id,
            product_query=result.offer.name[:255],
            price=result.offer.price,
            currency=result.offer.currency,
            verdict=result.evaluation.verdict.value,
            affordability_score=result.evaluation.score,
            confidence=result.evaluation.confidence,
            rubric_version=result.evaluation.rubric_version,
            affordable_from=result.evaluation.affordable_from,
            goal_delay_days=result.picture.goal_delay_days or None,
            emergency_fund_delta_months=(
                after["emergency_fund_months"] - before["emergency_fund_months"]
            ),
            health_score_delta=health_delta,
            simulation={
                "before": _jsonable(before),
                "after": _jsonable(after),
                "goal_impact": result.goal_impact,
                "forecast_trough_after": (
                    format(result.forecast_trough_after, "f")
                    if result.forecast_trough_after is not None
                    else None
                ),
            },
            emi_options=[
                {
                    "tenure_months": option.tenure_months,
                    "monthly": format(option.monthly, "f"),
                    "total_interest": format(option.total_interest, "f"),
                    "new_debt_ratio": format(option.new_debt_ratio, "f"),
                    "is_serviceable": option.is_serviceable,
                }
                for option in result.emi_options
            ]
            or None,
            alternatives=[
                {
                    "name": alt.offer.name,
                    "price": format(alt.offer.price, "f"),
                    "score": format(alt.score, "f"),
                    "verdict": alt.verdict.value,
                }
                for alt in result.alternatives
            ]
            or None,
            explanation=result.explanation.model_dump(mode="json"),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def history(self, user_id: uuid.UUID, *, limit: int = 20) -> Sequence[PurchaseEvaluation]:
        stmt = (
            select(PurchaseEvaluation)
            .where(PurchaseEvaluation.user_id == user_id)
            .order_by(PurchaseEvaluation.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, user_id: uuid.UUID, evaluation_id: uuid.UUID) -> PurchaseEvaluation | None:
        stmt = select(PurchaseEvaluation).where(
            PurchaseEvaluation.user_id == user_id, PurchaseEvaluation.id == evaluation_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


def _with_goal_delay(picture: FinancialPicture, delay: int) -> FinancialPicture:
    """Rebuild the picture with the goal delay filled in.

    `FinancialPicture` is frozen and slotted, so this replaces rather than
    mutates. The delay depends on the price, which the picture is measured
    before knowing.
    """
    return FinancialPicture(
        liquid_reserves=picture.liquid_reserves,
        monthly_income=picture.monthly_income,
        monthly_expenses=picture.monthly_expenses,
        savings_rate=picture.savings_rate,
        debt_service=picture.debt_service,
        forecast_trough=picture.forecast_trough,
        health_score=picture.health_score,
        goal_delay_days=delay,
        top_goal_name=picture.top_goal_name,
        observation_days=picture.observation_days,
        window_start=picture.window_start,
        window_end=picture.window_end,
        forecast_caveats=picture.forecast_caveats,
    )


def _jsonable(snapshot: dict[str, Any]) -> dict[str, str | None]:
    return {
        key: (format(value, "f") if isinstance(value, Decimal) else value)
        for key, value in snapshot.items()
    }
