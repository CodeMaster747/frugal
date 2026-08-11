"""The Explanation contract.

Every engine in Frugal -- financial health, insights, forecasting, the purchase
advisor -- returns this envelope. One shape means one frontend component renders
all of them, and it means explainability is structural rather than aspirational.

The invariant enforced here is the product's central promise: a score or verdict
with no factors cannot be constructed, so an unexplainable recommendation cannot
reach a user. See ADR-002.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Direction(StrEnum):
    """Which way a factor pushes the outcome."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class DataWindow(BaseModel):
    """The span of history a result was computed from.

    Carried on every Explanation so the UI can say "based on 142 days" rather
    than implying the result is grounded in more data than it is.
    """

    model_config = ConfigDict(frozen=True)

    start: date
    end: date
    observation_days: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> DataWindow:
        if self.end < self.start:
            raise ValueError("data_window.end must not precede start")
        return self


class Factor(BaseModel):
    """One weighted input to a score, with its contribution made explicit."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Human-readable label, e.g. 'Emergency fund'")
    value: str = Field(min_length=1, description="Display form, e.g. '3.2 months'")
    raw_value: Decimal = Field(description="Underlying numeric value, for clients that recompute")
    weight: Decimal = Field(ge=0, le=1, description="Rubric weight in 0..1")
    contribution: Decimal = Field(description="Signed points this factor added to the score")
    direction: Direction
    explanation: str = Field(min_length=1, description="Why this factor matters, in plain language")


class Explanation(BaseModel):
    """The reasoning behind a score or verdict.

    Construction fails if a conclusion is asserted without factors -- that state
    is a defect, and making it unrepresentable is cheaper than catching it in
    review one engine at a time.
    """

    model_config = ConfigDict(frozen=True)

    verdict: str | None = Field(default=None, description="e.g. BUY_NOW, WAIT, HEALTHY")
    score: Decimal | None = Field(default=None, description="0..100 where applicable")
    confidence: Decimal = Field(ge=0, le=1, description="Calibrated confidence in this result")
    method: str = Field(min_length=1, description="e.g. 'prophet', 'ewma_seasonal', 'rubric_v1'")
    data_window: DataWindow
    factors: list[Factor] = Field(default_factory=list)
    caveats: list[str] = Field(
        default_factory=list,
        description="Known limitations. Always surfaced to the user, never collapsed.",
    )
    computed_at: datetime

    @model_validator(mode="after")
    def _conclusions_require_factors(self) -> Explanation:
        """The central invariant of the product (ADR-002)."""
        if (self.score is not None or self.verdict is not None) and not self.factors:
            raise ValueError(
                "Explanation asserts a score or verdict but lists no factors. "
                "An unexplainable recommendation must not reach a user (ADR-002)."
            )
        return self

    @property
    def total_contribution(self) -> Decimal:
        """Sum of factor contributions.

        For rubric-based engines this must reconstruct ``score``; a rubric whose
        parts do not sum to the whole is decoration, not an explanation.
        """
        return sum((f.contribution for f in self.factors), Decimal(0))

    @property
    def total_weight(self) -> Decimal:
        """Sum of factor weights. Rubric-based engines must total 1.00."""
        return sum((f.weight for f in self.factors), Decimal(0))

    def is_arithmetically_consistent(self, tolerance: Decimal = Decimal("0.01")) -> bool:
        """Whether contributions reconstruct the score within ``tolerance``.

        Asserted in tests for every rubric-based engine.
        """
        if self.score is None:
            return True
        return abs(self.total_contribution - self.score) <= tolerance
