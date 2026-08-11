"""Turning measured inputs into a scored, explained health verdict.

Pure: `score()` takes a `HealthInputs` and returns an `Explanation`. No session,
no clock, no I/O. That is what makes the golden-file tests possible and what
lets every band be exercised without a database.

The hard part of this module is not the arithmetic -- it is **being honest when
the data is thin**. A user two weeks into using Frugal has no meaningful
emergency-fund history and no closed budget periods. The temptation is to score
those as zero, which produces a confident, precise, wrong number that tells them
their finances are in crisis when the truth is that we do not know yet.

So a metric that cannot be computed is *excluded* rather than zeroed, its weight
is redistributed across the metrics that remain, and the omission is stated as a
caveat. The score stays interpretable, the arithmetic still reconciles, and the
user is told what was left out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.explanation import DataWindow, Direction, Explanation, Factor
from app.modules.health.rubric import (
    METRICS,
    RUBRIC_VERSION,
    MetricKey,
    RiskLevel,
    risk_level,
)

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Below this, a score is not offered at all. Two weeks of transactions cannot
#: support a claim about someone's financial health, and a number produced from
#: them would be believed anyway -- that is the problem with numbers.
MIN_OBSERVATION_DAYS = 30

#: Full confidence needs roughly a year: enough to have seen an annual insurance
#: premium, a festival month, and a bonus.
FULL_CONFIDENCE_DAYS = 365

#: Below this confidence, the risk verdict is capped at MODERATE.
#:
#: The two errors here do not cost the same. Telling someone they are low-risk
#: on six weeks of data, when half the metrics could not be measured, invites
#: them to act on a reassurance we have not earned -- and reassurance is the one
#: thing a financial tool should never overclaim. Telling a genuinely healthy
#: user "moderate, and here is why we are not sure yet" costs them nothing but a
#: month of patience. Asymmetric costs justify an asymmetric cap.
CONFIDENT_ENOUGH_FOR_REASSURANCE = Decimal("0.35")


@dataclass(frozen=True, slots=True)
class MetricInput:
    """One measured sub-metric, or an explicit statement that it is unknown.

    `available=False` is the important state. It is not "zero" and it is not
    "average" -- it means we have nothing to say, and the scorer must not
    pretend otherwise.
    """

    raw: Decimal | None
    display: str
    detail: str
    available: bool = True
    #: Why it could not be measured. Becomes a caveat verbatim.
    unavailable_because: str = ""


@dataclass(frozen=True, slots=True)
class HealthInputs:
    """Everything the rubric needs, already measured."""

    window_start: date
    window_end: date
    observation_days: int
    transaction_count: int
    computed_at: datetime
    metrics: dict[MetricKey, MetricInput] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HealthResult:
    explanation: Explanation
    score: Decimal | None
    risk: RiskLevel | None
    #: Sub-scores in 0..100, keyed by metric. Promoted out of the Explanation so
    #: trend queries do not pay JSONB extraction cost on every row.
    subscores: dict[MetricKey, Decimal]
    rubric_version: str = RUBRIC_VERSION


def confidence_for(observation_days: int, transaction_count: int) -> Decimal:
    """How much to trust this score, from how much history produced it.

    Two independent limits, and the lower wins: a year of history with nine
    transactions in it is not a year of evidence, and 400 transactions in a
    fortnight is a busy fortnight rather than a pattern.
    """
    by_time = min(Decimal(observation_days) / Decimal(FULL_CONFIDENCE_DAYS), Decimal(1))
    by_volume = min(Decimal(transaction_count) / Decimal(150), Decimal(1))
    return min(by_time, by_volume).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _direction(points: Decimal) -> Direction:
    if points >= Decimal("70"):
        return Direction.POSITIVE
    if points >= Decimal("45"):
        return Direction.NEUTRAL
    return Direction.NEGATIVE


def score(inputs: HealthInputs) -> HealthResult:
    """Compute the health score and the reasoning behind it."""
    window = DataWindow(
        start=inputs.window_start,
        end=inputs.window_end,
        observation_days=inputs.observation_days,
    )
    caveats: list[str] = []

    if inputs.observation_days < MIN_OBSERVATION_DAYS:
        # No score at all rather than a bad one. An Explanation with no score
        # and no factors is legal by construction (ADR-002) and is the honest
        # shape for "ask me again in a month".
        return HealthResult(
            explanation=Explanation(
                verdict=None,
                score=None,
                confidence=ZERO,
                method=f"rubric_{RUBRIC_VERSION}",
                data_window=window,
                factors=[],
                caveats=[
                    f"Only {inputs.observation_days} days of history — "
                    f"at least {MIN_OBSERVATION_DAYS} are needed before a health score means "
                    "anything. Add transactions or import a statement.",
                ],
                computed_at=inputs.computed_at,
            ),
            score=None,
            risk=None,
            subscores={},
        )

    available = [
        (metric, inputs.metrics[metric.key])
        for metric in METRICS
        if metric.key in inputs.metrics and inputs.metrics[metric.key].available
    ]
    for metric in METRICS:
        measured = inputs.metrics.get(metric.key)
        if measured is None or not measured.available:
            reason = (
                measured.unavailable_because
                if measured and measured.unavailable_because
                else "not enough data"
            )
            caveats.append(
                f"{metric.name} could not be measured ({reason}); its weight was "
                "redistributed across the metrics that could."
            )

    if not available:
        return HealthResult(
            explanation=Explanation(
                verdict=None,
                score=None,
                confidence=ZERO,
                method=f"rubric_{RUBRIC_VERSION}",
                data_window=window,
                factors=[],
                caveats=caveats
                or ["None of the six health metrics could be measured from this data."],
                computed_at=inputs.computed_at,
            ),
            score=None,
            risk=None,
            subscores={},
        )

    # Redistribute the missing weight proportionally, so the surviving metrics
    # still sum to 1.00 and the score stays on a 0--100 scale. Without this a
    # user missing one metric would be capped at 85 forever, which reads as a
    # judgement rather than as missing data.
    measured_weight = sum((m.weight for m, _ in available), ZERO)
    scale = Decimal(1) / measured_weight if measured_weight else ZERO

    # Redistributed weights are irrational in general (1/0.85 = 1.17647...), so
    # rounding each to four places leaves them summing to 0.9999 rather than
    # 1.0000 -- which caps a flawless user at 99.99 and makes the published
    # "weights sum to 1.00" claim false on any user missing a metric. The
    # remainder goes onto the heaviest weight.
    weights = [
        (metric.weight * scale).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        for metric, _ in available
    ]
    weight_drift = Decimal(1) - sum(weights, ZERO)
    if weight_drift:
        weights[weights.index(max(weights))] += weight_drift

    subscores: dict[MetricKey, Decimal] = {}
    parts: list[tuple[str, Decimal, Decimal, Decimal, Decimal, str]] = []
    exact_total = ZERO

    for (metric, measured), effective_weight in zip(available, weights, strict=True):
        raw = measured.raw if measured.raw is not None else ZERO
        points, _band = metric.score(raw)
        exact = points * effective_weight
        exact_total += exact

        subscores[metric.key] = points
        parts.append(
            (
                metric.name,
                raw,
                effective_weight,
                exact.quantize(CENTS, rounding=ROUND_HALF_UP),
                points,
                measured.detail,
            )
        )

    total = max(ZERO, min(Decimal("100"), exact_total.quantize(CENTS, rounding=ROUND_HALF_UP)))

    # Rounding each contribution independently leaves the sum a cent or two off
    # the total -- with six redistributed weights a perfect user scores 99.99,
    # which reads as a bug rather than as rounding. The remainder goes onto the
    # largest contribution, where a cent is proportionally least visible, so the
    # parts reconstruct the score *exactly* rather than nearly. Nearly is not
    # good enough: the reconstruction is the claim (ADR-002).
    drift = total - sum((p[3] for p in parts), ZERO)
    if drift and parts:
        biggest = max(range(len(parts)), key=lambda i: parts[i][3])
        name, raw, weight, contribution, points, detail = parts[biggest]
        parts[biggest] = (name, raw, weight, contribution + drift, points, detail)

    factors: list[Factor] = [
        Factor(
            name=name,
            value=display,
            raw_value=raw,
            weight=weight,
            contribution=contribution,
            direction=_direction(points),
            explanation=detail,
        )
        for (name, raw, weight, contribution, points, detail), display in zip(
            parts, [m.display for _, m in available], strict=True
        )
    ]

    confidence = confidence_for(inputs.observation_days, inputs.transaction_count)
    if confidence < Decimal("0.5"):
        caveats.append(
            f"Based on {inputs.observation_days} days and {inputs.transaction_count} "
            "transactions — enough for a first estimate, not enough to be confident. "
            "The score will steady as more history accumulates."
        )

    level = risk_level(total)
    if confidence < CONFIDENT_ENOUGH_FOR_REASSURANCE and level is RiskLevel.LOW:
        level = RiskLevel.MODERATE
        caveats.append(
            "Shown as moderate rather than low risk: the score is high, but there is not "
            "yet enough history to say so with confidence. It will settle as data accumulates."
        )

    return HealthResult(
        explanation=Explanation(
            verdict=level.value.upper(),
            score=total,
            confidence=confidence,
            method=f"rubric_{RUBRIC_VERSION}",
            data_window=window,
            factors=factors,
            caveats=caveats,
            computed_at=inputs.computed_at,
        ),
        score=total,
        risk=level,
        subscores=subscores,
    )


def subscore_of(result: HealthResult, key: MetricKey) -> Decimal:
    """A sub-score for the snapshot columns, zero when the metric was skipped.

    Zero in the *column* is acceptable where it is not in the score: the column
    exists only for trend queries, and the Explanation stored beside it records
    that the metric was unmeasured.
    """
    return result.subscores.get(key, ZERO)
