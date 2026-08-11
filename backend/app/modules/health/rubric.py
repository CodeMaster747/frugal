"""The financial health rubric — weights, bands, and scoring curves.

Deliberately a **published, deterministic rubric** rather than a learned model
(ADR-005). A user who disagrees with their score can read this file's output at
`GET /health-score/rubric` and see exactly what produced it. A gradient-boosted
score of equivalent accuracy would be unarguable, which for a number that tells
someone their finances are unhealthy is a worse product.

Everything here is pure. No database, no clock, no I/O -- which is what lets the
golden-file tests pin exact output for a fixture and lets every band be checked
without standing up Postgres.

**Weights sum to 1.00 and contributions sum to the score.** Both are asserted by
test, because a rubric whose parts do not reconstruct the whole is decoration
rather than an explanation (ADR-002).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

#: Bumped whenever a weight or band changes. Stored on every snapshot, because a
#: trend line that silently mixes two rubrics is worse than no trend line: the
#: user sees a jump they made no decision to cause.
RUBRIC_VERSION = "v1"

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class MetricKey(StrEnum):
    SAVINGS_RATE = "savings_rate"
    EMERGENCY_FUND = "emergency_fund"
    DEBT_TO_INCOME = "debt_to_income"
    BUDGET_DISCIPLINE = "budget_discipline"
    CASHFLOW_STABILITY = "cashflow_stability"
    GROWTH = "growth"


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Band:
    """One rung of a scoring ladder: at or above `threshold`, score `points`."""

    threshold: Decimal
    points: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class Metric:
    """A sub-metric's weight and its scoring ladder.

    `higher_is_better=False` inverts the comparison, so debt-to-income can use
    the same ladder machinery as everything else rather than a special case.
    """

    key: MetricKey
    name: str
    weight: Decimal
    bands: tuple[Band, ...]
    higher_is_better: bool = True
    unit: str = ""

    def score(self, raw: Decimal) -> tuple[Decimal, str]:
        """Sub-score in 0..100 and the band label it fell in.

        Bands are walked best-first, so the first match is the highest rung the
        value clears. A value below every threshold scores 0 -- which is a real
        answer, not a failure.
        """
        for band in self.bands:
            hit = raw >= band.threshold if self.higher_is_better else raw <= band.threshold
            if hit:
                return band.points, band.label
        return ZERO, self.bands[-1].label

    def contribution(self, raw: Decimal) -> Decimal:
        """Points this metric adds to the overall score, weight applied."""
        points, _ = self.score(raw)
        return (points * self.weight).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


#: The rubric. Weights are the judgement call; everything else follows from them.
#:
#: Savings rate and emergency fund carry the most weight (0.25 each) because
#: they are the two things a user can actually change and that most change their
#: resilience. Growth carries the least (0.05): it is the most volatile and the
#: least actionable month to month, and weighting it heavily would make the
#: score swing on market noise rather than behaviour.
METRICS: tuple[Metric, ...] = (
    Metric(
        key=MetricKey.SAVINGS_RATE,
        name="Savings rate",
        weight=Decimal("0.25"),
        unit="%",
        # 20% is the widely-cited healthy floor; 30%+ is genuinely strong. The
        # ladder is generous below 20% on purpose -- someone saving 12% is doing
        # something right, and a score that tells them otherwise loses them.
        bands=(
            Band(Decimal("0.30"), HUNDRED, "excellent"),
            Band(Decimal("0.20"), Decimal("85"), "healthy"),
            Band(Decimal("0.15"), Decimal("70"), "adequate"),
            Band(Decimal("0.10"), Decimal("55"), "thin"),
            Band(Decimal("0.05"), Decimal("35"), "low"),
            Band(Decimal("0.00"), Decimal("15"), "breaking even"),
            Band(Decimal("-99"), ZERO, "spending more than you earn"),
        ),
    ),
    Metric(
        key=MetricKey.EMERGENCY_FUND,
        name="Emergency fund",
        weight=Decimal("0.25"),
        unit=" months",
        # Months of expenses covered by liquid assets. Six is the standard
        # target; three is the point at which a job loss stops being a crisis
        # this month.
        bands=(
            Band(Decimal("6"), HUNDRED, "fully funded"),
            Band(Decimal("4.5"), Decimal("85"), "strong"),
            Band(Decimal("3"), Decimal("70"), "adequate"),
            Band(Decimal("1.5"), Decimal("45"), "thin"),
            Band(Decimal("0.5"), Decimal("20"), "minimal"),
            Band(ZERO, ZERO, "none"),
        ),
    ),
    Metric(
        key=MetricKey.DEBT_TO_INCOME,
        name="Debt-to-income",
        weight=Decimal("0.20"),
        unit="%",
        higher_is_better=False,
        # 36% is the conventional lending ceiling; 43% is where most lenders
        # stop entirely. Below 10% is effectively unencumbered.
        bands=(
            Band(Decimal("0.10"), HUNDRED, "minimal"),
            Band(Decimal("0.20"), Decimal("85"), "comfortable"),
            Band(Decimal("0.36"), Decimal("65"), "manageable"),
            Band(Decimal("0.43"), Decimal("35"), "stretched"),
            Band(Decimal("999"), ZERO, "overextended"),
        ),
    ),
    Metric(
        key=MetricKey.BUDGET_DISCIPLINE,
        name="Budget discipline",
        weight=Decimal("0.15"),
        unit="",
        # Share of closed budget periods kept.
        bands=(
            Band(Decimal("1.00"), HUNDRED, "every budget kept"),
            Band(Decimal("0.80"), Decimal("85"), "mostly kept"),
            Band(Decimal("0.60"), Decimal("65"), "mixed"),
            Band(Decimal("0.40"), Decimal("40"), "often exceeded"),
            Band(ZERO, Decimal("15"), "rarely kept"),
        ),
    ),
    Metric(
        key=MetricKey.CASHFLOW_STABILITY,
        name="Cash-flow stability",
        weight=Decimal("0.10"),
        unit="",
        higher_is_better=False,
        # Coefficient of variation of monthly net cash flow. Lower is steadier;
        # a volatile month-to-month net makes every other plan unreliable.
        bands=(
            Band(Decimal("0.15"), HUNDRED, "very steady"),
            Band(Decimal("0.30"), Decimal("85"), "steady"),
            Band(Decimal("0.50"), Decimal("65"), "moderate"),
            Band(Decimal("0.80"), Decimal("40"), "variable"),
            Band(Decimal("999"), Decimal("15"), "erratic"),
        ),
    ),
    Metric(
        key=MetricKey.GROWTH,
        name="Financial growth",
        weight=Decimal("0.05"),
        unit="%/mo",
        # Average month-over-month change in net worth.
        bands=(
            Band(Decimal("0.02"), HUNDRED, "growing well"),
            Band(Decimal("0.01"), Decimal("85"), "growing"),
            Band(Decimal("0.00"), Decimal("65"), "holding"),
            Band(Decimal("-0.01"), Decimal("35"), "slipping"),
            Band(Decimal("-99"), Decimal("10"), "shrinking"),
        ),
    ),
)

BY_KEY: dict[MetricKey, Metric] = {m.key: m for m in METRICS}

#: Overall score to risk level. Deliberately not evenly spaced: the interesting
#: distinction is between "fine" and "one bad month from trouble", which sits
#: around 55--70, so the bands are tighter there.
RISK_BANDS: tuple[tuple[Decimal, RiskLevel], ...] = (
    (Decimal("75"), RiskLevel.LOW),
    (Decimal("60"), RiskLevel.MODERATE),
    (Decimal("40"), RiskLevel.ELEVATED),
    (ZERO, RiskLevel.HIGH),
)


def risk_level(score: Decimal) -> RiskLevel:
    for threshold, level in RISK_BANDS:
        if score >= threshold:
            return level
    return RiskLevel.HIGH


def total_weight() -> Decimal:
    """Must be exactly 1.00. Asserted by test, not assumed."""
    return sum((m.weight for m in METRICS), ZERO)


def published() -> dict[str, object]:
    """The rubric as data, for `GET /health-score/rubric`.

    Published so the scoring model is inspectable without reverse-engineering it
    from outputs. A user who disagrees with their score can see what produced
    it, which is the difference between a tool and an oracle.
    """
    return {
        "version": RUBRIC_VERSION,
        "total_weight": format(total_weight(), "f"),
        "metrics": [
            {
                "key": m.key.value,
                "name": m.name,
                "weight": format(m.weight, "f"),
                "unit": m.unit,
                "higher_is_better": m.higher_is_better,
                "bands": [
                    {
                        "at_least" if m.higher_is_better else "at_most": format(b.threshold, "f"),
                        "points": format(b.points, "f"),
                        "label": b.label,
                    }
                    for b in m.bands
                ],
            }
            for m in METRICS
        ],
        "risk_levels": [
            {"at_least": format(threshold, "f"), "level": level.value}
            for threshold, level in RISK_BANDS
        ],
    }
