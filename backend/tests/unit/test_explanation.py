"""Tests for the Explanation contract (ADR-002).

The central invariant: a score or verdict with no factors must be
unconstructable. This is the M0 exit criterion that matters most -- it is
enforced before any engine exists, so no engine can ever ship without it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.explanation import DataWindow, Direction, Explanation, Factor

WINDOW = DataWindow(start=date(2026, 3, 15), end=date(2026, 8, 4), observation_days=142)
NOW = datetime(2026, 8, 4, 9, 12, tzinfo=UTC)


def factor(name: str = "Savings rate", weight: str = "1.0", contribution: str = "72.5") -> Factor:
    return Factor(
        name=name,
        value="51.4%",
        raw_value=Decimal("0.514"),
        weight=Decimal(weight),
        contribution=Decimal(contribution),
        direction=Direction.POSITIVE,
        explanation="You save 51% of income, above the 20% healthy threshold.",
    )


class TestTheCentralInvariant:
    """A conclusion without reasoning must not be representable."""

    def test_score_without_factors_is_rejected(self):
        with pytest.raises(ValidationError, match="lists no factors"):
            Explanation(
                score=Decimal("72.5"),
                confidence=Decimal("0.81"),
                method="rubric_v1",
                data_window=WINDOW,
                factors=[],
                computed_at=NOW,
            )

    def test_verdict_without_factors_is_rejected(self):
        with pytest.raises(ValidationError, match="lists no factors"):
            Explanation(
                verdict="WAIT",
                confidence=Decimal("0.77"),
                method="rubric_v1",
                data_window=WINDOW,
                factors=[],
                computed_at=NOW,
            )

    def test_score_with_factors_is_accepted(self):
        explanation = Explanation(
            score=Decimal("72.5"),
            confidence=Decimal("0.81"),
            method="rubric_v1",
            data_window=WINDOW,
            factors=[factor()],
            computed_at=NOW,
        )
        assert explanation.score == Decimal("72.5")

    def test_informational_explanation_needs_no_factors(self):
        """An Explanation asserting neither score nor verdict is legitimate --
        it is how an engine reports 'insufficient data' with caveats only."""
        explanation = Explanation(
            confidence=Decimal("0.2"),
            method="recurring_projection",
            data_window=DataWindow(
                start=date(2026, 7, 25), end=date(2026, 8, 4), observation_days=10
            ),
            factors=[],
            caveats=["Only 10 days of history; forecasting needs at least 14."],
            computed_at=NOW,
        )
        assert explanation.score is None
        assert explanation.caveats


class TestArithmeticConsistency:
    """Contributions must reconstruct the score, and weights must total 1.00.

    A rubric whose parts do not sum to the whole is decoration, not an
    explanation -- so this is asserted rather than assumed.
    """

    def test_contributions_sum_to_score(self):
        explanation = Explanation(
            score=Decimal("72.5"),
            confidence=Decimal("0.81"),
            method="rubric_v1",
            data_window=WINDOW,
            factors=[
                factor("Savings rate", "0.25", "22.1"),
                factor("Emergency fund", "0.25", "13.3"),
                factor("Debt-to-income", "0.20", "16.0"),
                factor("Budget discipline", "0.15", "10.8"),
                factor("Cash-flow stability", "0.10", "6.2"),
                factor("Financial growth", "0.05", "4.1"),
            ],
            computed_at=NOW,
        )
        assert explanation.total_weight == Decimal("1.00")
        assert explanation.total_contribution == Decimal("72.5")
        assert explanation.is_arithmetically_consistent()

    def test_inconsistent_rubric_is_detected(self):
        explanation = Explanation(
            score=Decimal("90.0"),
            confidence=Decimal("0.8"),
            method="rubric_v1",
            data_window=WINDOW,
            factors=[factor(contribution="10.0")],
            computed_at=NOW,
        )
        assert not explanation.is_arithmetically_consistent()

    def test_negative_contributions_are_supported(self):
        """Factors that worsen an outcome carry negative contributions --
        this is what lets the advisor explain a WAIT verdict."""
        explanation = Explanation(
            verdict="WAIT",
            score=Decimal("48.2"),
            confidence=Decimal("0.77"),
            method="rubric_v1",
            data_window=WINDOW,
            factors=[
                Factor(
                    name="Emergency fund after purchase",
                    value="0.8 months",
                    raw_value=Decimal("0.8"),
                    weight=Decimal("0.30"),
                    contribution=Decimal("-24.0"),
                    direction=Direction.NEGATIVE,
                    explanation="Drops from 3.2 to 0.8 months, below the 3-month floor.",
                ),
                factor("Savings rate", "0.70", "72.2"),
            ],
            computed_at=NOW,
        )
        assert explanation.total_contribution == Decimal("48.2")


class TestFieldConstraints:
    def test_confidence_must_be_a_probability(self):
        for bad in (Decimal("1.5"), Decimal("-0.1")):
            with pytest.raises(ValidationError):
                Explanation(confidence=bad, method="rubric_v1", data_window=WINDOW, computed_at=NOW)

    def test_weight_must_be_within_zero_and_one(self):
        with pytest.raises(ValidationError):
            factor(weight="1.4")

    def test_method_is_required_and_non_empty(self):
        with pytest.raises(ValidationError):
            Explanation(confidence=Decimal("0.5"), method="", data_window=WINDOW, computed_at=NOW)

    def test_data_window_rejects_reversed_dates(self):
        with pytest.raises(ValidationError, match="must not precede"):
            DataWindow(start=date(2026, 8, 4), end=date(2026, 3, 15), observation_days=142)

    def test_explanation_is_immutable(self):
        explanation = Explanation(
            confidence=Decimal("0.5"), method="rubric_v1", data_window=WINDOW, computed_at=NOW
        )
        with pytest.raises(ValidationError):
            explanation.confidence = Decimal("0.9")  # type: ignore[misc]
