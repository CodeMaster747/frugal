"""Persisted health scores.

One row per user per day. The full `Explanation` is stored as JSONB alongside
the promoted sub-scores, so a six-month-old score can still be decomposed into
the factors that produced it -- a trend line the user cannot interrogate is just
a shape.

`rubric_version` is not optional. When weights change, historical scores must
stay interpretable under the rubric that produced them; without it a trend
silently mixes incompatible scales and the user sees a jump they made no
decision to cause.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import CONFIDENCE, Base, TenantMixin, TimestampMixin, UUIDMixin

#: 0..100 with two decimals.
SCORE = Numeric(5, 2)


class HealthSnapshot(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "health_snapshots"

    snapshot_on: Mapped[date] = mapped_column(Date, nullable=False)

    overall_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(16), nullable=False)

    # Promoted out of the JSONB because trend queries -- AVG(savings_rate_score)
    # over twelve months -- run constantly and should not pay JSONB extraction
    # cost per row.
    savings_rate_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    emergency_fund_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    debt_to_income_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    budget_discipline_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    cashflow_stability_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)
    growth_score: Mapped[Decimal] = mapped_column(SCORE, nullable=False)

    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        # One score per user per day: recomputing within a day updates rather
        # than accumulating, so a user who opens the app six times does not get
        # six points on their trend line.
        UniqueConstraint("user_id", "snapshot_on", name="uq_health_snapshots"),
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100", name="ck_health_score_range"
        ),
        Index("ix_health_snapshots_user_date", "user_id", text("snapshot_on DESC")),
    )
