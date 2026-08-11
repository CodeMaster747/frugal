"""Cash-flow forecasting — the module's public interface.

Gathers daily flows and detected commitments, selects a tier, and caches the
result against the user's `data_version`.

**Tier 3 is not reachable from a web request.** The API image does not install
Prophet, so `select_tier(..., allow_prophet=False)` is what the request path
calls. A user with enough history for tier 3 is served tier 2 immediately and a
worker job is queued; the better forecast replaces it on the next read. That is
slower to converge than running Prophet inline and it is the only version that
fits in 1 GB.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import DailyPoint, ForecastRequest, ForecastResult
from app.core import cache
from app.core.clock import utc_now, utc_today
from app.core.explanation import DataWindow, Direction, Explanation, Factor
from app.core.logging import get_logger
from app.modules.analytics.service import AnalyticsService
from app.modules.forecasting import recurring
from app.modules.forecasting.models import Forecast
from app.modules.forecasting.tiers import (
    ABSOLUTE_MINIMUM_DAYS,
    TIER3_MINIMUM_DAYS,
    ProphetForecaster,
    select_tier,
)

logger = get_logger(__name__)

ZERO = Decimal("0")

#: How much history to feed the forecaster. Two years is more than any tier
#: needs and bounds the query.
LOOKBACK_DAYS = 730

#: Patterns weaker than this are left in the statistical baseline rather than
#: projected as scheduled events.
SCHEDULE_CONFIDENCE_FLOOR = Decimal("0.60")


class InsufficientHistoryError(Exception):
    """Not enough data to forecast at all.

    Carries the caveats so the 503 can explain itself rather than being a bare
    status code — declining is a real answer and deserves a real body.
    """

    def __init__(self, observation_days: int) -> None:
        self.observation_days = observation_days
        self.caveats = [
            f"Only {observation_days} days of transaction history. A forecast needs at "
            f"least {ABSOLUTE_MINIMUM_DAYS} days before it means anything.",
            "Import a bank statement or add a few weeks of transactions to unlock this.",
        ]
        super().__init__(f"{observation_days} days of history")


class ForecastService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)

    # -- inputs ------------------------------------------------------------

    async def _build_request(
        self,
        user_id: uuid.UUID,
        *,
        horizon_days: int,
        extra_events: list[tuple[date, Decimal]] | None = None,
    ) -> tuple[ForecastRequest, list[recurring.Pattern], date]:
        today = utc_today()
        history = await self.analytics.daily_net_flows(user_id, days=LOOKBACK_DAYS)

        if len(history) < ABSOLUTE_MINIMUM_DAYS:
            raise InsufficientHistoryError(len(history))

        total_balance, _ = await self.analytics.net_worth(user_id)
        candidates = await self.analytics.recurring_candidates(user_id, days=LOOKBACK_DAYS)
        patterns = self._patterns_from(candidates, today)
        confident = [p for p in patterns if p.confidence >= SCHEDULE_CONFIDENCE_FLOOR]

        horizon_end = today + timedelta(days=horizon_days)
        scheduled: list[tuple[date, Decimal]] = []
        for pattern in confident:
            # Income adds, expense subtracts. The pattern records a magnitude;
            # the sign belongs to the direction of the flow.
            sign = Decimal(1) if pattern.kind == "income" else Decimal(-1)
            for when in pattern.due_dates(today, horizon_end):
                scheduled.append((when, pattern.amount * sign))

        scheduled.extend(extra_events or [])

        return (
            ForecastRequest(
                horizon_days=horizon_days,
                opening_balance=total_balance,
                # The *residual* series -- see `_residual_history`.
                history=self._residual_history(history, candidates, confident),
                scheduled=scheduled,
                start_on=today + timedelta(days=1),
            ),
            patterns,
            today,
        )

    @staticmethod
    def _residual_history(
        history: list[tuple[date, Decimal]],
        candidates: list[Any],
        patterns: list[recurring.Pattern],
    ) -> list[tuple[date, Decimal]]:
        """Daily flow with recurring commitments taken out.

        **The tiers lay scheduled commitments *over* a statistical baseline.**
        If that baseline is computed from history still containing salary and
        rent, every commitment is counted twice -- once in the average and again
        on its due date. The projection then climbs at roughly double the true
        savings rate, which looks plausible on a chart and is badly wrong.

        Caught by the backtest, where feeding the tiers their real production
        inputs pushed 90-day MAPE from 31% to 77%. It would not have shown up in
        any unit test, because each half is individually correct.
        """
        recurring_keys = {(p.merchant, p.kind) for p in patterns}
        if not recurring_keys:
            return history

        committed: dict[date, Decimal] = {}
        for row in candidates:
            if (row.merchant, row.kind) not in recurring_keys:
                continue
            sign = Decimal(1) if row.kind == "income" else Decimal(-1)
            committed[row.occurred_on] = committed.get(row.occurred_on, ZERO) + row.amount * sign

        return [(when, amount - committed.get(when, ZERO)) for when, amount in history]

    def _patterns_from(self, candidates: list[Any], today: date) -> list[recurring.Pattern]:
        occurrences = [
            recurring.Occurrence(
                transaction_id=row.transaction_id,
                merchant=row.merchant,
                occurred_on=row.occurred_on,
                amount=row.amount,
                kind=row.kind,
                category_id=row.category_id,
                account_id=row.account_id,
            )
            for row in candidates
        ]
        return [
            pattern
            for pattern in recurring.detect(occurrences, today=today)
            if not recurring.is_stale(pattern, today=today)
        ]

    async def detect_patterns(self, user_id: uuid.UUID) -> list[recurring.Pattern]:
        """Recurring commitments detected from the ledger.

        Stale patterns are dropped here rather than in the detector: whether a
        cancelled subscription should still count depends on today's date, and
        the detector is deliberately clock-free.
        """
        rows = await self.analytics.recurring_candidates(user_id, days=LOOKBACK_DAYS)
        return self._patterns_from(rows, utc_today())

    async def sync_recurring(self, user_id: uuid.UUID) -> dict[str, int]:
        """Persist detected patterns through finance's service.

        Written through `FinanceService` rather than into `recurring_items`
        directly: the table belongs to finance, and a cross-module write would
        be the coupling ADR-001 exists to prevent.

        This is what makes M6's `new_recurring` insight fire — the detector
        there reads auto-detected items, and until now nothing produced any.
        """
        from app.modules.finance.service import DetectedRecurring, FinanceService

        patterns = await self.detect_patterns(user_id)
        confident = [p for p in patterns if p.confidence >= SCHEDULE_CONFIDENCE_FLOOR]

        return await FinanceService(self.session).upsert_detected_recurring(
            user_id,
            [
                DetectedRecurring(
                    name=pattern.merchant,
                    kind=pattern.kind,
                    item_type=_classify_type(pattern),
                    cadence=pattern.cadence,
                    amount=pattern.amount,
                    next_due_on=pattern.next_due_on,
                    first_seen_on=pattern.first_seen_on,
                    amount_variance=pattern.amount_variance,
                    confidence=pattern.confidence,
                    category_id=pattern.category_id,
                    account_id=pattern.account_id,
                )
                for pattern in confident
            ],
        )

    # -- forecasting -------------------------------------------------------

    async def forecast(
        self,
        user_id: uuid.UUID,
        *,
        horizon_days: int = 90,
        allow_prophet: bool = False,
        use_cache: bool = True,
        extra_events: list[tuple[date, Decimal]] | None = None,
    ) -> tuple[ForecastResult, Explanation, list[recurring.Pattern]]:
        """Project cash flow, serving a cached row when one is still valid."""
        version = await cache.current_version(user_id)

        if use_cache and extra_events is None:
            cached = await self._cached(user_id, horizon_days=horizon_days, version=version)
            if cached is not None:
                return cached

        request, patterns, today = await self._build_request(
            user_id, horizon_days=horizon_days, extra_events=extra_events
        )

        tier = select_tier(len(request.history), allow_prophet=allow_prophet)
        if tier is None:
            raise InsufficientHistoryError(len(request.history))

        result = tier.forecast(request)
        explanation = self._explain(result, request, today)

        # A scenario is hypothetical and must never become the cached answer to
        # "what is my forecast".
        if extra_events is None:
            await self._store(user_id, result, explanation, horizon_days, version)

        return result, explanation, patterns

    def wants_better_tier(self, result: ForecastResult) -> bool:
        """Whether a worker run would produce a materially better answer.

        The API cannot run Prophet, so this is how it knows to queue one.
        """
        return (
            result.method != ProphetForecaster.name
            and result.observation_days >= TIER3_MINIMUM_DAYS
        )

    def _explain(
        self, result: ForecastResult, request: ForecastRequest, today: date
    ) -> Explanation:
        """Wrap a forecast in the shared contract (ADR-002).

        Forecast factors carry **zero contribution**: they are inputs to a
        projection, not weighted parts of a score. Fabricating contributions so
        the panel looks fuller would break the one invariant the contract has.
        """
        window_start = request.history[0][0] if request.history else today
        weight = (
            (Decimal(1) / Decimal(len(result.factors))).quantize(Decimal("0.0001"))
            if result.factors
            else ZERO
        )

        return Explanation(
            verdict=None,
            score=None,
            confidence=result.confidence,
            method=result.method,
            data_window=DataWindow(
                start=window_start,
                end=today,
                observation_days=result.observation_days,
            ),
            factors=[
                Factor(
                    name=name,
                    value=value,
                    raw_value=ZERO,
                    weight=weight,
                    contribution=ZERO,
                    direction=Direction.NEUTRAL,
                    explanation=detail,
                )
                for name, value, detail in result.factors
            ],
            caveats=result.caveats,
            computed_at=utc_now(),
        )

    # -- caching -----------------------------------------------------------

    async def _cached(
        self, user_id: uuid.UUID, *, horizon_days: int, version: int
    ) -> tuple[ForecastResult, Explanation, list[recurring.Pattern]] | None:
        """The stored forecast, if it is still built on current data."""
        stmt = (
            select(Forecast)
            .where(
                Forecast.user_id == user_id,
                Forecast.horizon_days == horizon_days,
                Forecast.data_version == version,
                Forecast.generated_on == utc_today(),
            )
            .order_by(Forecast.created_at.desc())
            .limit(1)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        result = ForecastResult(
            method=row.method,
            series=[
                DailyPoint(
                    on=date.fromisoformat(point["date"]),
                    p10=Decimal(point["p10"]),
                    p50=Decimal(point["p50"]),
                    p90=Decimal(point["p90"]),
                )
                for point in row.series
            ],
            confidence=row.confidence,
            observation_days=row.observation_days,
            caveats=list(row.explanation.get("caveats", [])),
        )
        return (
            result,
            Explanation.model_validate(row.explanation),
            await self.detect_patterns(user_id),
        )

    async def _store(
        self,
        user_id: uuid.UUID,
        result: ForecastResult,
        explanation: Explanation,
        horizon_days: int,
        version: int,
    ) -> None:
        trough = result.trough
        if trough is None:
            return

        self.session.add(
            Forecast(
                user_id=user_id,
                generated_on=utc_today(),
                horizon_days=horizon_days,
                method=result.method,
                observation_days=result.observation_days,
                confidence=result.confidence,
                projected_balance_end=result.ending_balance,
                trough_amount=trough.p50,
                trough_on=trough.on,
                shortfall_dates=[d.isoformat() for d in result.shortfall_dates()],
                series=[
                    {
                        "date": point.on.isoformat(),
                        "p10": format(point.p10, "f"),
                        "p50": format(point.p50, "f"),
                        "p90": format(point.p90, "f"),
                    }
                    for point in result.series
                ],
                explanation=explanation.model_dump(mode="json"),
                data_version=version,
            )
        )
        await self.session.flush()

    async def prune(self, user_id: uuid.UUID, *, keep: int = 10) -> int:
        """Drop superseded forecasts.

        Every read on changed data writes a row, so without this the table grows
        with usage rather than with information.
        """
        stmt = (
            select(Forecast.id)
            .where(Forecast.user_id == user_id)
            .order_by(Forecast.created_at.desc())
            .offset(keep)
        )
        stale = [row[0] for row in (await self.session.execute(stmt)).all()]
        for forecast_id in stale:
            await self.session.delete(await self.session.get(Forecast, forecast_id))
        return len(stale)


def summarise(result: ForecastResult) -> dict[str, Any]:
    """Headline numbers, for a caller that does not want the whole series."""
    trough = result.trough
    return {
        "method": result.method,
        "confidence": result.confidence,
        "observation_days": result.observation_days,
        "projected_balance_end": result.ending_balance,
        "trough_amount": trough.p50 if trough else ZERO,
        "trough_on": trough.on if trough else None,
        "shortfall_dates": result.shortfall_dates(),
    }


def _classify_type(pattern: recurring.Pattern) -> str:
    """Best guess at what kind of commitment this is.

    Deliberately crude. Classifying an outflow as rent versus EMI versus
    subscription would need the category *slug*, and the pattern carries only a
    category id — resolving it would mean another query per pattern for a field
    the user can correct in one tap. `other` is honest; a wrong guess is not.

    Income defaults to salary because a recurring inflow that is not salary is
    rare enough that the default is right far more often than not.
    """
    return "salary" if pattern.kind == "income" else "other"
