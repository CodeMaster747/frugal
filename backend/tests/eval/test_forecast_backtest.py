"""Forecast backtesting harness (M7 exit criterion).

Walk-forward backtest: cut the history at a point in the past, forecast forward
from there, and compare against what actually happened. Repeated at several cut
points so one lucky quarter cannot flatter the result.

**MAPE on a balance path, not on daily flows.** Daily net flow crosses zero
constantly, and percentage error against a near-zero denominator explodes —
the metric would measure how often the user broke even, not how good the
forecast is. The balance path is what the UI draws and what a user acts on, so
it is what gets scored.

The personas are synthetic and generated from a seeded RNG, so the numbers here
are reproducible but are **not** a claim about real users. What the harness
proves is relative: that tier 2 beats tier 1 where it should, that error grows
with horizon rather than staying suspiciously flat, and that no tier is wildly
worse than carrying the current balance forward unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.adapters.ports import ForecastRequest
from app.modules.forecasting.tiers import EwmaSeasonal, RecurringProjection

pytestmark = pytest.mark.eval

ZERO = Decimal(0)

START = date(2025, 1, 1)

#: Ceilings from the measured baseline, with headroom. Guards against a
#: regression, not targets to aim at.
# Measured at 2.7% and 1.4%; ceilings set with headroom for RNG drift. Low
# because the synthetic personas are well-structured and the harness hands the
# forecaster exactly the right commitments. Real ledgers are messier -- treat
# these as an upper bound on quality, not a promise.
MAX_TIER1_MAPE = 8.0
MAX_TIER2_MAPE = 5.0


@dataclass(frozen=True, slots=True)
class Persona:
    name: str
    monthly_income: int
    monthly_rent: int
    daily_spend: tuple[int, int]
    weekend_multiplier: float
    opening_balance: int


PERSONAS = (
    Persona("salaried, steady", 95_000, 22_000, (400, 1400), 2.1, 180_000),
    Persona("salaried, tight", 48_000, 16_000, (300, 900), 1.6, 25_000),
    Persona("irregular income", 70_000, 18_000, (200, 2600), 1.3, 90_000),
)


def synthesise(persona: Persona, days: int, seed: int) -> list[tuple[date, Decimal]]:
    """A plausible daily net-flow series.

    Salary on the 1st, rent on the 3rd, discretionary spending every day with a
    weekend lift — the structure the tiers are meant to pick up.
    """
    rng = random.Random(seed)
    series: list[tuple[date, Decimal]] = []

    for offset in range(days):
        when = START + timedelta(days=offset)
        flow = 0.0

        if when.day == 1:
            wobble = rng.uniform(0.95, 1.05) if persona.name == "irregular income" else 1.0
            flow += persona.monthly_income * wobble
        if when.day == 3:
            flow -= persona.monthly_rent

        low, high = persona.daily_spend
        spend = rng.uniform(low, high)
        if when.weekday() >= 5:
            spend *= persona.weekend_multiplier
        flow -= spend

        series.append((when, Decimal(str(round(flow, 2)))))

    return series


def commitments(persona: Persona, start: date, horizon: int) -> list[tuple[date, Decimal]]:
    """The scheduled events the forecaster would be handed in production.

    `ForecastService` runs recurrence detection and passes the confident
    patterns through; a backtest that omits them is not testing the system that
    ships. It matters most for tier 1, whose entire contribution *is* projecting
    known commitments — without them it degenerates to extrapolating a noisy
    short-history mean, and measures worse than assuming nothing changes.

    Derived from the persona definition rather than by running the detector:
    the detector has its own tests, and mixing the two would make a failure here
    ambiguous between "the forecaster regressed" and "detection regressed".
    """
    events: list[tuple[date, Decimal]] = []
    for offset in range(horizon):
        when = start + timedelta(days=offset)
        if when.day == 1:
            events.append((when, Decimal(persona.monthly_income)))
        if when.day == 3:
            events.append((when, Decimal(-persona.monthly_rent)))
    return events


def balance_path(opening: float, flows: list[tuple[date, Decimal]]) -> list[float]:
    balance = opening
    path: list[float] = []
    for _, amount in flows:
        balance += float(amount)
        path.append(balance)
    return path


def terminal_error(actual: list[float], predicted: list[float]) -> float:
    """Absolute rupee error on the final day of the horizon.

    Reported alongside MAPE because neither MAPE nor mean-absolute-error answers
    "is forecasting further out harder", and both were tried first:

    * **MAPE falls with horizon** here, because these personas save. The
      denominator grows faster than the error does, so the percentage improves
      while the projection gets worse in rupees.
    * **Mean absolute error also falls**, for a subtler reason. It averages over
      every day in the horizon, and the dominant error is lumpy: whether a
      ₹95,000 salary lands inside the window. That miss swamps a 30-day mean and
      is diluted across 90 days.

    Terminal error has neither problem, and it is what a user actually reads --
    the gap at the right-hand edge of the chart, which is the number they plan
    against.
    """
    return abs(actual[-1] - predicted[-1]) if actual else 0.0


def mape(actual: list[float], predicted: list[float]) -> float:
    """Mean absolute percentage error.

    Days where the actual balance is near zero are skipped: the percentage is
    unbounded there and would swamp the average with an artefact of the
    denominator rather than an error in the forecast.
    """
    pairs = [(a, p) for a, p in zip(actual, predicted, strict=True) if abs(a) > 1000]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def backtest(
    tier, persona: Persona, *, train_days: int, horizon: int, seed: int
) -> tuple[float, float]:
    """One walk-forward evaluation, as (MAPE, terminal rupee error)."""
    full = synthesise(persona, train_days + horizon, seed)
    train, actual_flows = full[:train_days], full[train_days:]

    opening = float(persona.opening_balance) + sum(float(a) for _, a in train)
    start = train[-1][0] + timedelta(days=1)
    scheduled = commitments(persona, start, horizon)

    # The tiers lay commitments *over* a statistical baseline, so the baseline
    # must not also contain them -- otherwise salary is counted twice and the
    # projection climbs at double the true rate. `ForecastService` strips them
    # in `_residual_history`; the backtest strips them the same way, or it would
    # be measuring a system that does not ship.
    residual = [
        (
            when,
            amount
            - (Decimal(persona.monthly_income) if when.day == 1 else ZERO)
            + (Decimal(persona.monthly_rent) if when.day == 3 else ZERO),
        )
        for when, amount in train
    ]

    result = tier.forecast(
        ForecastRequest(
            horizon_days=horizon,
            opening_balance=Decimal(str(round(opening, 2))),
            history=residual,
            scheduled=scheduled,
            start_on=start,
        )
    )

    actual = balance_path(opening, actual_flows)
    predicted = [float(point.p50) for point in result.series]
    return mape(actual, predicted), terminal_error(actual, predicted)


def naive_mape(persona: Persona, *, train_days: int, horizon: int, seed: int) -> float:
    """The do-nothing baseline: assume the balance never changes.

    A forecast that cannot beat this is not earning its complexity.
    """
    full = synthesise(persona, train_days + horizon, seed)
    train, actual_flows = full[:train_days], full[train_days:]
    opening = float(persona.opening_balance) + sum(float(a) for _, a in train)
    return mape(balance_path(opening, actual_flows), [opening] * horizon)


@pytest.fixture(scope="module")
def report() -> dict[str, object]:
    tiers = {"recurring_projection": RecurringProjection(), "ewma_seasonal": EwmaSeasonal()}
    horizons = (30, 60, 90)
    # Cut points spread across a **whole salary cycle**, not just a few.
    #
    # Income here is monthly and lumpy, and every horizon tested is a multiple
    # of 30 days. With two or three cut points the result is dominated by phase
    # -- whether the horizon happens to end just before or just after a
    # ₹95,000 salary -- and the error trend inverts depending on which. Ten cut
    # points three days apart cover every phase of the month, so the average
    # measures the model rather than the calendar.
    cuts = {
        "recurring_projection": tuple(range(20, 50, 3)),
        "ewma_seasonal": tuple(range(90, 120, 3)),
    }

    by_tier: dict[str, dict[int, float]] = {}
    terminal_by_tier: dict[str, dict[int, float]] = {}
    for name, tier in tiers.items():
        by_horizon: dict[int, float] = {}
        terminal_horizon: dict[int, float] = {}
        for horizon in horizons:
            scores = [
                backtest(tier, persona, train_days=cut, horizon=horizon, seed=seed)
                for persona in PERSONAS
                for cut in cuts[name]
                for seed in (11, 23, 37)
            ]
            by_horizon[horizon] = sum(s[0] for s in scores) / len(scores)
            terminal_horizon[horizon] = sum(s[1] for s in scores) / len(scores)
        by_tier[name] = by_horizon
        terminal_by_tier[name] = terminal_horizon

    naive = {
        horizon: sum(
            naive_mape(persona, train_days=90, horizon=horizon, seed=seed)
            for persona in PERSONAS
            for seed in (11, 23, 37)
        )
        / (len(PERSONAS) * 3)
        for horizon in horizons
    }

    return {
        "by_tier": by_tier,
        "terminal_by_tier": terminal_by_tier,
        "naive": naive,
        "horizons": horizons,
    }


def test_baseline_report(report: dict[str, object]) -> None:
    """Print the baseline. This is the deliverable."""
    by_tier: dict[str, dict[int, float]] = report["by_tier"]  # type: ignore[assignment]
    naive: dict[int, float] = report["naive"]  # type: ignore[assignment]

    print("\n" + "=" * 68)
    print("  Forecast backtest — MAPE on the projected balance path")
    print("  3 synthetic personas x 3 seeds x several cut points")
    print("=" * 68)
    print(f"\n  {'tier':<24}{'30d':>10}{'60d':>10}{'90d':>10}")
    print("  " + "-" * 54)
    for name, scores in by_tier.items():
        row = "".join(f"{scores[h]:>9.1f}%" for h in report["horizons"])  # type: ignore[union-attr]
        print(f"  {name:<24}{row}")
    naive_row = "".join(f"{naive[h]:>9.1f}%" for h in report["horizons"])  # type: ignore[union-attr]
    print(f"  {'(naive: no change)':<24}{naive_row}")

    terminal: dict[str, dict[int, float]] = report["terminal_by_tier"]  # type: ignore[assignment]
    print("\n  Terminal error (₹ at the end of the horizon), same runs:")
    print("  " + "-" * 54)
    for name, scores in terminal.items():
        row = "".join(f"{scores[h]:>10,.0f}" for h in report["horizons"])  # type: ignore[union-attr]
        print(f"  {name:<24}{row}")
    print("\n  MAPE falls with horizon while terminal error rises. These personas")
    print("  save, so the balance MAPE divides by grows faster than the error.")
    print("  Terminal error is the honest read on 'is further out harder'.")
    print("\n  Synthetic data. The absolute numbers are not a claim about real")
    print("  users — what matters is the ordering and the trend with horizon.")
    print("=" * 68 + "\n")


def test_every_tier_beats_doing_nothing(report: dict[str, object]) -> None:
    """The bar any forecast has to clear to justify existing.

    Assuming the balance never moves is free and requires no model. A tier that
    cannot beat it is adding computation, latency, and a confidence figure for
    nothing.
    """
    by_tier: dict[str, dict[int, float]] = report["by_tier"]  # type: ignore[assignment]
    naive: dict[int, float] = report["naive"]  # type: ignore[assignment]

    for name, scores in by_tier.items():
        for horizon, score in scores.items():
            assert score < naive[horizon], (
                f"{name} at {horizon}d scores {score:.1f}% against a naive {naive[horizon]:.1f}% "
                "— it is not earning its complexity"
            )


def test_the_better_tier_is_actually_better(report: dict[str, object]) -> None:
    """Tier 2 exists because it beats tier 1 given enough history. If it does
    not, the tiering is ceremony."""
    by_tier: dict[str, dict[int, float]] = report["by_tier"]  # type: ignore[assignment]

    for horizon in report["horizons"]:  # type: ignore[union-attr]
        assert by_tier["ewma_seasonal"][horizon] <= by_tier["recurring_projection"][horizon]


def test_error_grows_with_horizon(report: dict[str, object]) -> None:
    """Ninety days out must be harder than thirty.

    Asserted on **terminal error**, after MAPE and mean-absolute-error both
    failed for reasons that were about the metric rather than the model -- see
    `terminal_error`. A flat curve here would make the widening confidence band
    a lie.
    """
    terminal: dict[str, dict[int, float]] = report["terminal_by_tier"]  # type: ignore[assignment]

    for name, scores in terminal.items():
        assert scores[90] > scores[30], f"{name} shows no cost to forecasting further out"


def test_accuracy_holds(report: dict[str, object]) -> None:
    """Regression guard against the recorded baseline."""
    by_tier: dict[str, dict[int, float]] = report["by_tier"]  # type: ignore[assignment]

    assert by_tier["recurring_projection"][90] < MAX_TIER1_MAPE
    assert by_tier["ewma_seasonal"][90] < MAX_TIER2_MAPE
