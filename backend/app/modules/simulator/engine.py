"""Turning a projection into an explained answer.

Pure. Takes a `Position` and a `Scenario`, returns a before/after with an
`Explanation` (ADR-002) — the same envelope health, insights, forecasting, and
the advisor return, so the frontend renders it with the component it already
has.

**Scenario factors carry zero contribution.** They are inputs to a projection,
not weighted parts of a score, and there is no score here to decompose.
Fabricating contributions so the panel looks fuller would break the one
invariant the contract has.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.explanation import DataWindow, Direction, Explanation, Factor
from app.modules.simulator.scenarios import (
    ChangeKind,
    Position,
    Projection,
    Scenario,
    months_until_shortfall,
    project,
)

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: Cover below this many months at the trough is called out. Same line the
#: health rubric and the advisor use, so a user is not learning a third
#: threshold.
COMFORT_MONTHS = Decimal("3")


class Outlook:
    """How a scenario turns out. Deliberately not a score.

    A scenario is not graded — the user is asking "what happens", not "how well
    did I do". Ranking two holidays out of a hundred would invent a precision
    that does not exist, and the honest output is a shape plus the numbers that
    made it.
    """

    COMFORTABLE = "comfortable"
    TIGHT = "tight"
    UNSUSTAINABLE = "unsustainable"


@dataclass(frozen=True, slots=True)
class Snapshot:
    liquid_reserves: Decimal
    monthly_surplus: Decimal
    emergency_fund_months: Decimal


@dataclass(frozen=True, slots=True)
class Result:
    scenario_name: str
    outlook: str
    before: Snapshot
    after: Snapshot
    projection: Projection
    explanation: Explanation
    months_until_shortfall: int | None
    #: Cover at the worst point, which is the number that decides the outlook.
    trough_months_of_cover: Decimal


def _money(value: Decimal) -> str:
    return f"₹{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _months(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} months"


def _outlook(trough_cover: Decimal, shortfall: int | None) -> str:
    if shortfall is not None:
        return Outlook.UNSUSTAINABLE
    if trough_cover < COMFORT_MONTHS:
        return Outlook.TIGHT
    return Outlook.COMFORTABLE


def _factors(
    position: Position, scenario: Scenario, projection: Projection, after: Snapshot
) -> list[Factor]:
    """One factor per change, plus the two figures that summarise the outcome.

    Per change rather than per category: the user typed these in, and seeing
    their own inputs reflected back is what makes the projection checkable.
    """
    weight = (
        (Decimal(1) / Decimal(len(scenario.changes) + 2)).quantize(Decimal("0.0001"))
        if scenario.changes
        else Decimal("0.5")
    )

    factors: list[Factor] = []
    for change in scenario.changes:
        if change.kind is ChangeKind.ONE_OFF:
            value = _money(change.amount)
            detail = f"A single payment of {_money(change.amount)}" + (
                f" in {change.starts_in_months} months." if change.starts_in_months else " now."
            )
            direction = Direction.NEGATIVE
        else:
            monthly = change.signed_monthly()
            value = f"{_money(abs(monthly))}/month"
            span = (
                f"for {change.lasts_months} months"
                if change.lasts_months is not None
                else "indefinitely"
            )
            start = (
                f" starting in {change.starts_in_months} months" if change.starts_in_months else ""
            )
            detail = (
                f"{'Adds' if monthly > 0 else 'Costs'} {_money(abs(monthly))} a month "
                f"{span}{start}."
            )
            direction = Direction.POSITIVE if monthly > 0 else Direction.NEGATIVE

        factors.append(
            Factor(
                name=change.label,
                value=value,
                raw_value=change.amount,
                weight=weight,
                contribution=ZERO,
                direction=direction,
                explanation=detail,
            )
        )

    trough = projection.trough
    factors.append(
        Factor(
            name="Lowest point",
            value=_money(trough.reserves) if trough else "—",
            raw_value=trough.reserves if trough else ZERO,
            weight=weight,
            contribution=ZERO,
            direction=Direction.NEUTRAL,
            explanation=(
                f"Your savings bottom out at {_money(trough.reserves)} in "
                f"{trough.month} months, about {_months(after.emergency_fund_months)} "
                "of spending."
                if trough
                else "No projection was possible."
            ),
        )
    )
    factors.append(
        Factor(
            name="Where you end up",
            value=_money(projection.ending_reserves),
            raw_value=projection.ending_reserves,
            weight=weight,
            contribution=ZERO,
            direction=(
                Direction.POSITIVE
                if projection.ending_reserves >= position.liquid_reserves
                else Direction.NEGATIVE
            ),
            explanation=(
                f"After {scenario.horizon_months} months you would have "
                f"{_money(projection.ending_reserves)}, against "
                f"{_money(position.liquid_reserves)} today."
            ),
        )
    )
    return factors


def _caveats(position: Position, scenario: Scenario, shortfall: int | None) -> list[str]:
    caveats: list[str] = []

    if shortfall is not None:
        caveats.append(
            f"Your savings run out in month {shortfall}. Everything after that point "
            "assumes borrowing, which this projection does not model."
        )

    if position.observation_days < 180:
        caveats.append(
            f"Built on {position.observation_days} days of history, so the monthly "
            "income and spending figures behind it are still settling."
        )

    caveats.append(
        "Assumes income and spending otherwise continue at their current levels, with "
        "no inflation, no pay rise, and no unplanned expenses."
    )
    if any(c.lasts_months is None for c in scenario.changes):
        caveats.append(
            "One or more changes were modelled as permanent. If it is temporary, set a "
            "duration and run it again."
        )
    return caveats


def evaluate(
    position: Position, scenario: Scenario, *, today: date, computed_at: datetime
) -> Result:
    """Project the scenario and explain the result."""
    projection = project(position, scenario, today=today)
    shortfall = months_until_shortfall(projection)

    # Monthly outgoings after the scenario settles, used for the cover figure.
    final_delta = scenario.monthly_delta_at(scenario.horizon_months - 1)
    expenses_after = position.monthly_expenses
    for change in scenario.changes:
        if change.kind is ChangeKind.RECURRING_EXPENSE and change.active_in_month(
            scenario.horizon_months - 1
        ):
            expenses_after += -change.amount if change.is_reduction else change.amount

    trough = projection.trough
    trough_cover = (
        (trough.reserves / expenses_after).quantize(CENTS)
        if trough and expenses_after > 0
        else ZERO
    )

    after = Snapshot(
        liquid_reserves=projection.ending_reserves,
        monthly_surplus=(position.monthly_surplus + final_delta).quantize(CENTS),
        emergency_fund_months=trough_cover,
    )
    before = Snapshot(
        liquid_reserves=position.liquid_reserves,
        monthly_surplus=position.monthly_surplus.quantize(CENTS),
        emergency_fund_months=position.emergency_fund_months,
    )

    outlook = _outlook(trough_cover, shortfall)

    return Result(
        scenario_name=scenario.name,
        outlook=outlook,
        before=before,
        after=after,
        projection=projection,
        months_until_shortfall=shortfall,
        trough_months_of_cover=trough_cover,
        explanation=Explanation(
            verdict=outlook.upper(),
            # No score: a scenario is not graded. See `Outlook`.
            score=None,
            confidence=_confidence(position),
            method="scenario_v1",
            data_window=DataWindow(
                start=position.window_start or today,
                end=position.window_end or today,
                observation_days=position.observation_days,
            ),
            factors=_factors(position, scenario, projection, after),
            caveats=_caveats(position, scenario, shortfall),
            computed_at=computed_at,
        ),
    )


def _confidence(position: Position) -> Decimal:
    """Bounded by history, like every other engine here."""
    return min(Decimal(position.observation_days) / Decimal(365), Decimal(1)).quantize(
        Decimal("0.001"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True, slots=True)
class Comparison:
    """Several scenarios against the same starting position."""

    results: tuple[Result, ...]

    @property
    def safest(self) -> Result | None:
        """The one that leaves the most cover at its worst point.

        Not "the best" — that depends on what the user wants, which the software
        does not know. It is the one that leaves the most room if things go
        wrong, and the UI labels it exactly that way.
        """
        survivable = [r for r in self.results if r.months_until_shortfall is None]
        pool = survivable or list(self.results)
        return max(pool, key=lambda r: r.trough_months_of_cover) if pool else None


def compare(
    position: Position,
    scenarios: list[Scenario],
    *,
    today: date,
    computed_at: datetime,
) -> Comparison:
    return Comparison(
        results=tuple(
            evaluate(position, scenario, today=today, computed_at=computed_at)
            for scenario in scenarios
        )
    )
