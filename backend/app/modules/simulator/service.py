"""Scenario simulation — the module's public interface.

Measures the user's position, runs scenarios, and compares them. The engine and
the scenario shapes are pure; this is the plumbing.

Nothing is persisted. A scenario is a question, and the answer changes with the
ledger — storing one would mean serving a stale answer to "what if", which is
the opposite of what the feature is for. The advisor persists its evaluations
because those record a *decision*; this records a musing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now, utc_today
from app.modules.analytics.service import AnalyticsService
from app.modules.simulator import scenarios
from app.modules.simulator.engine import Comparison, Result, compare, evaluate

ZERO = Decimal("0")


class SimulatorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)

    async def position(self, user_id: uuid.UUID) -> scenarios.Position:
        """Where the user stands today.

        Reuses the same aggregates the advisor and health engine use, so a
        scenario's "before" matches what those pages show. Three views of one
        position that disagree is worse than any of them being wrong.
        """
        from app.modules.health.service import HealthService

        window = await self.analytics.observation_window(user_id)
        cashflow = await self.analytics.cashflow(user_id, months=12)
        reserves = await self.analytics.emergency_reserves(user_id)
        health = (await HealthService(self.session).compute(user_id)).score

        active = [p for p in cashflow if p.income > 0 or p.expense > 0]
        months = Decimal(len(active)) if active else Decimal(1)

        return scenarios.Position(
            liquid_reserves=reserves,
            monthly_income=(sum((p.income for p in active), ZERO) / months).quantize(
                Decimal("0.01")
            ),
            monthly_expenses=(sum((p.expense for p in active), ZERO) / months).quantize(
                Decimal("0.01")
            ),
            health_score=health,
            observation_days=window.observation_days,
            window_start=window.first_transaction_on,
            window_end=window.last_transaction_on,
        )

    async def run(self, user_id: uuid.UUID, scenario: scenarios.Scenario) -> Result:
        position = await self.position(user_id)
        return evaluate(position, scenario, today=utc_today(), computed_at=utc_now())

    async def run_many(self, user_id: uuid.UUID, many: list[scenarios.Scenario]) -> Comparison:
        """Several scenarios against one measured position.

        Measured once and shared: running the position query per scenario would
        be three round trips for one screen, and a position that shifted between
        them would make the comparison meaningless.
        """
        position = await self.position(user_id)
        return compare(position, many, today=utc_today(), computed_at=utc_now())
