"""Insight generation — the module's public interface.

Gathers aggregates, runs every detector, and persists what survives dedup and
ranking. The detectors are pure and live in `detectors.py`; everything here is
about *not saying the same thing twice*.

Three suppression layers, in order:

1. **Dedup key + period, under a unique constraint.** Re-running the engine
   cannot create a second copy of the same finding. Enforced by the database, so
   a detector that forgets is still safe.
2. **Dismissal cooling period.** A user who dismissed a finding does not see it
   again for `COOLING_DAYS`, even in a new period.
3. **Per-run cap.** Twenty true findings at once is not twenty times as useful
   as the five that matter; it is a wall of text nobody reads.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now, utc_today
from app.core.explanation import DataWindow
from app.core.logging import get_logger
from app.modules.analytics.service import AnalyticsService, Period
from app.modules.insights import detectors
from app.modules.insights.detectors import Candidate
from app.modules.insights.models import Insight, Severity

logger = get_logger(__name__)

#: A dismissed finding stays suppressed this long. Long enough that dismissing
#: means something, short enough that a genuinely worsening situation resurfaces.
COOLING_DAYS = 60

#: Most material findings kept per run.
MAX_PER_RUN = 8

#: The comparison window for detectors that need a "this period vs last period"
#: contrast. Thirty days is always a full window and always current, unlike
#: month-to-date which is five days on the 5th.
DETECTION_WINDOW_DAYS = 30

ZERO = Decimal("0")


class InsightService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)

    # -- generation --------------------------------------------------------

    async def refresh(self, user_id: uuid.UUID) -> dict[str, int]:
        """Run every detector and persist what is new.

        Returns counts rather than the rows: the caller is a background task or
        a fire-and-forget endpoint, and neither needs the objects.
        """
        candidates = await self._detect(user_id)
        ranked = sorted(candidates, key=lambda c: c.materiality, reverse=True)

        suppressed = await self._suppressed_keys(user_id)
        eligible = [c for c in ranked if c.dedup_key not in suppressed][:MAX_PER_RUN]

        created = 0
        for candidate in eligible:
            if await self._persist(user_id, candidate):
                created += 1

        return {
            "detected": len(candidates),
            "suppressed": len(candidates) - len(eligible),
            "created": created,
        }

    async def _detect(self, user_id: uuid.UUID) -> list[Candidate]:
        """Every detector, over one shared set of aggregates."""
        today = utc_today()
        now = utc_now()
        # Trailing 30 days rather than month-to-date: see `Period.trailing_days`.
        # The label stays the calendar month, so a finding is still raised once
        # per month rather than once per run.
        period = Period.trailing_days(DETECTION_WINDOW_DAYS, today)
        period_label = today.strftime("%Y-%m")

        observation = await self.analytics.observation_window(user_id)
        window = DataWindow(
            start=observation.first_transaction_on or today,
            end=observation.last_transaction_on or today,
            observation_days=observation.observation_days,
        )

        cashflow = await self.analytics.cashflow(user_id, months=12)
        slices = await self.analytics.categories(user_id, period)
        budget_outcomes = await self.analytics.budget_outcomes(user_id, months=3)
        reserves = await self.analytics.emergency_reserves(user_id)
        savings_trend = await self.analytics.savings_rate_trend(user_id, months=12)

        spending_months = [p.expense for p in cashflow if p.expense > 0]
        monthly_expense = (
            sum(spending_months, ZERO) / Decimal(len(spending_months)) if spending_months else ZERO
        )

        outliers = await self.analytics.spending_outliers(user_id, period)
        sub_current, sub_prior, sub_count = await self.analytics.subscription_spend(user_id, period)
        goals = await self.analytics.goal_progress(user_id)
        recurring = await self.analytics.recurring_items(user_id)

        return [
            *detectors.category_spikes(
                slices, period_label=period_label, window=window, computed_at=now
            ),
            *detectors.budget_breaches(
                budget_outcomes, period_label=period_label, window=window, computed_at=now
            ),
            *detectors.emergency_fund_low(
                reserves,
                monthly_expense,
                period_label=period_label,
                window=window,
                computed_at=now,
            ),
            *detectors.savings_rate_change(
                savings_trend, period_label=period_label, window=window, computed_at=now
            ),
            *detectors.cashflow_shortfall(
                cashflow, period_label=period_label, window=window, computed_at=now
            ),
            *detectors.anomalous_transactions(
                [
                    detectors.OutlierTransaction(
                        transaction_id=row.transaction_id,
                        merchant=row.merchant,
                        category_name=row.category_name,
                        amount=row.amount,
                        category_median=row.category_median,
                        occurred_on=row.occurred_on,
                    )
                    for row in outliers
                ],
                window=window,
                computed_at=now,
            ),
            *detectors.subscription_creep(
                detectors.SubscriptionSummary(
                    current_total=sub_current, prior_total=sub_prior, count=sub_count
                ),
                period_label=period_label,
                window=window,
                computed_at=now,
            ),
            *detectors.goals_at_risk(
                [
                    detectors.GoalProgress(
                        goal_id=row.goal_id,
                        name=row.name,
                        target_amount=row.target_amount,
                        current_amount=row.current_amount,
                        target_date=row.target_date,
                        monthly_surplus=row.monthly_surplus,
                    )
                    for row in goals
                ],
                today=today,
                window=window,
                computed_at=now,
            ),
            *detectors.new_recurring(
                [
                    detectors.RecurringItem(
                        item_id=row.item_id,
                        name=row.name,
                        amount=row.amount,
                        cadence=row.cadence,
                        first_seen_on=row.first_seen_on,
                        is_auto_detected=row.is_auto_detected,
                        kind=row.kind,
                    )
                    for row in recurring
                ],
                # "New" means since the start of the previous month. A window
                # shorter than the cadence would miss a monthly charge entirely.
                since=period.previous().start,
                window=window,
                computed_at=now,
            ),
        ]

    async def _suppressed_keys(self, user_id: uuid.UUID) -> set[str]:
        """Dedup keys the user has dismissed recently.

        The unique constraint already stops a repeat within a period. This is
        the other half: a dismissal has to survive the period rolling over, or
        "not interested" means "not interested until Tuesday".
        """
        cutoff = utc_now() - timedelta(days=COOLING_DAYS)
        stmt = select(Insight.dedup_key).where(
            Insight.user_id == user_id,
            Insight.dismissed_at.is_not(None),
            Insight.dismissed_at >= cutoff,
        )
        return {row[0] for row in (await self.session.execute(stmt)).all()}

    async def _persist(self, user_id: uuid.UUID, candidate: Candidate) -> bool:
        """Insert, or do nothing if this finding already exists for the period.

        `ON CONFLICT DO NOTHING` rather than a check-then-insert: two concurrent
        refreshes would both see no row and both insert, and the unique
        constraint would turn the loser into a 500 for something that is
        working exactly as designed.

        Deliberately *not* an update. Rewriting an insight the user has already
        read -- possibly already dismissed -- would resurrect it silently.
        """
        stmt = (
            insert(Insight)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                insight_type=candidate.insight_type.value,
                severity=candidate.severity.value,
                title=candidate.title,
                body=candidate.body,
                impact_amount=candidate.impact_amount,
                materiality=candidate.materiality,
                confidence=candidate.confidence,
                dedup_key=candidate.dedup_key,
                period_start=candidate.explanation.data_window.start,
                period_end=candidate.explanation.data_window.end,
                explanation=candidate.explanation.model_dump(mode="json"),
                subject_id=candidate.subject_id,
            )
            .on_conflict_do_nothing(constraint="uq_insights_dedup")
            .returning(Insight.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    # -- reading -----------------------------------------------------------

    def _visible(self, user_id: uuid.UUID) -> Select[tuple[Insight]]:
        return select(Insight).where(
            Insight.user_id == user_id,
            Insight.deleted_at.is_(None),
            Insight.dismissed_at.is_(None),
        )

    async def feed(
        self,
        user_id: uuid.UUID,
        *,
        severity: Severity | None = None,
        unread_only: bool = False,
        limit: int = 20,
    ) -> Sequence[Insight]:
        """Active insights, most material first."""
        stmt = self._visible(user_id)
        if severity is not None:
            stmt = stmt.where(Insight.severity == severity.value)
        if unread_only:
            stmt = stmt.where(Insight.read_at.is_(None))

        stmt = stmt.order_by(Insight.materiality.desc(), Insight.created_at.desc()).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def unread_count(self, user_id: uuid.UUID) -> int:
        stmt = select(Insight.id).where(
            Insight.user_id == user_id,
            Insight.deleted_at.is_(None),
            Insight.dismissed_at.is_(None),
            Insight.read_at.is_(None),
        )
        return len((await self.session.execute(stmt)).all())

    # -- state changes -----------------------------------------------------

    async def mark_read(self, user_id: uuid.UUID, insight_id: uuid.UUID) -> bool:
        """Idempotent: marking a read insight read again is not an error, and
        the client may retry."""
        stmt = (
            update(Insight)
            .where(
                Insight.user_id == user_id,
                Insight.id == insight_id,
                Insight.deleted_at.is_(None),
            )
            .values(read_at=utc_now())
            .returning(Insight.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def dismiss(self, user_id: uuid.UUID, insight_id: uuid.UUID) -> bool:
        """Dismiss, and start the cooling period for this finding."""
        stmt = (
            update(Insight)
            .where(
                Insight.user_id == user_id,
                Insight.id == insight_id,
                Insight.deleted_at.is_(None),
            )
            .values(dismissed_at=utc_now(), read_at=utc_now())
            .returning(Insight.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None
