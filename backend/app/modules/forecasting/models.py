"""Persisted forecasts.

Cached rather than logged. A forecast is expensive to compute and cheap to
invalidate, so the row exists to be *served* -- and `data_version` is what makes
that safe. It carries the user's write counter at generation time; a forecast
whose version is behind is regenerated rather than returned, so a projection is
never built on superseded data.

The daily series lives in JSONB rather than a child table: it is always read
whole, never queried by individual point, and 90 rows per forecast per user
would grow quickly for no query benefit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import CONFIDENCE, MONEY, Base, TenantMixin, TimestampMixin, UUIDMixin


class Forecast(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "forecasts"

    generated_on: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    method: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    projected_balance_end: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    trough_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    trough_on: Mapped[date] = mapped_column(Date, nullable=False)

    shortfall_dates: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    series: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    #: The user's write counter when this was generated. A row whose value is
    #: behind the current one is stale and must not be served.
    data_version: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index(
            "ix_forecasts_user_gen",
            "user_id",
            text("generated_on DESC"),
            "horizon_days",
        ),
    )
