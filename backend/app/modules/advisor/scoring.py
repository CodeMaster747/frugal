"""Turning a financial picture and a price into a verdict.

Pure: `evaluate()` takes measured inputs and returns a verdict, a score, and an
`Explanation`. No session, no clock, no network — which is what lets the
scenario matrix drive twenty realistic users through it in milliseconds.

Two things here are worth reading closely.

**Constraints override the score.** See `rubric.py` for why. The mechanics: the
weighted score produces a provisional verdict, then every triggered constraint
caps it, and the worst cap wins. A capped verdict always states which constraint
capped it — an unexplained downgrade is indistinguishable from a bug.

**WAIT must carry a date.** A verdict of "wait" with no answer to "until when"
is not advice, it is a refusal wearing advice's clothes. `affordable_from` is
solved against the user's actual savings rate, and the database has a check
constraint that refuses to store a `wait` without one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.core.explanation import DataWindow, Direction, Explanation
from app.core.explanation import Factor as ExplanationFactor
from app.modules.advisor.rubric import (
    ADEQUATE_COVER_MONTHS,
    DEBT_CEILING,
    EMERGENCY_FUND_FLOOR_MONTHS,
    FACTORS,
    MATERIAL_PURCHASE_MONTHS,
    RUBRIC_VERSION,
    ConstraintCode,
    FactorKey,
    Verdict,
    cap,
    verdict_for,
)

ZERO = Decimal("0")
CENTS = Decimal("0.01")

#: How far ahead `affordable_from` will look before giving up. Beyond two years
#: "wait" stops being advice and becomes a polite no, so the verdict says no.
MAX_WAIT_DAYS = 730


@dataclass(frozen=True, slots=True)
class FinancialPicture:
    """The user's position, already measured by the calling service."""

    liquid_reserves: Decimal
    monthly_income: Decimal
    monthly_expenses: Decimal
    savings_rate: Decimal | None
    debt_service: Decimal
    #: Lowest projected balance over the forecast horizon, before the purchase.
    forecast_trough: Decimal | None
    health_score: Decimal | None
    #: Days the highest-priority goal slips if this purchase happens.
    goal_delay_days: int = 0
    top_goal_name: str = ""
    observation_days: int = 0
    window_start: date | None = None
    window_end: date | None = None
    forecast_caveats: tuple[str, ...] = ()

    @property
    def monthly_surplus(self) -> Decimal:
        return max(ZERO, self.monthly_income - self.monthly_expenses)

    @property
    def emergency_fund_months(self) -> Decimal:
        if self.monthly_expenses <= 0:
            return ZERO
        return (self.liquid_reserves / self.monthly_expenses).quantize(CENTS)


@dataclass(frozen=True, slots=True)
class TriggeredConstraint:
    code: ConstraintCode
    caps_at: Verdict
    message: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    verdict: Verdict
    score: Decimal
    confidence: Decimal
    explanation: Explanation
    constraints: tuple[TriggeredConstraint, ...] = ()
    affordable_from: date | None = None
    #: What the verdict would have been on score alone. Kept so the response can
    #: say "this would otherwise be BUY_NOW" rather than silently downgrading.
    score_verdict: Verdict = Verdict.BUY_NOW
    rubric_version: str = RUBRIC_VERSION
    subscores: dict[FactorKey, Decimal] = field(default_factory=dict)


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _money(value: Decimal) -> str:
    return f"₹{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _months(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} months"


def _raw_values(picture: FinancialPicture, price: Decimal) -> dict[FactorKey, Decimal]:
    """Each factor's input, in the units its bands expect."""
    monthly = picture.monthly_expenses if picture.monthly_expenses > 0 else Decimal(1)
    reserves_after = picture.liquid_reserves - price
    trough_after = (picture.forecast_trough or ZERO) - price

    return {
        FactorKey.EMERGENCY_FUND_AFTER: (reserves_after / monthly).quantize(CENTS),
        FactorKey.FORECAST_TROUGH_AFTER: (trough_after / monthly).quantize(CENTS),
        FactorKey.CASH_COVERAGE: (
            (picture.liquid_reserves / price).quantize(CENTS) if price > 0 else Decimal("999")
        ),
        FactorKey.SAVINGS_RATE: picture.savings_rate if picture.savings_rate is not None else ZERO,
        FactorKey.DEBT_TO_INCOME: (
            (picture.debt_service / picture.monthly_income).quantize(Decimal("0.0001"))
            if picture.monthly_income > 0
            else Decimal("999")
        ),
        FactorKey.GOAL_IMPACT: Decimal(picture.goal_delay_days),
        FactorKey.INCOME_MULTIPLE: (
            (price / picture.monthly_income).quantize(CENTS)
            if picture.monthly_income > 0
            else Decimal("999")
        ),
    }


def _describe(
    key: FactorKey, raw: Decimal, picture: FinancialPicture, price: Decimal
) -> tuple[str, str]:
    """Display value and plain-language reason for one factor."""
    if key is FactorKey.EMERGENCY_FUND_AFTER:
        before = picture.emergency_fund_months
        detail = f"Buying this takes your emergency fund from {_months(before)} to {_months(raw)}."
        if raw < 3:
            detail += " Three months is the point at which a lost income stops being a crisis."
        return _months(raw), detail

    if key is FactorKey.FORECAST_TROUGH_AFTER:
        absolute = (picture.forecast_trough or ZERO) - price
        if raw < 0:
            return (
                _money(absolute),
                "Your projected balance goes negative within the forecast window if you buy now.",
            )
        return (
            _money(absolute),
            f"Your lowest projected balance would be {_money(absolute)}, about {_months(raw)} "
            "of spending.",
        )

    if key is FactorKey.CASH_COVERAGE:
        if raw < 1:
            return (
                f"{raw}x",
                f"The price is more than your available savings of "
                f"{_money(picture.liquid_reserves)}, so this cannot be paid in cash.",
            )
        return f"{raw}x", f"Your liquid savings cover the price {raw} times over."

    if key is FactorKey.SAVINGS_RATE:
        if raw <= 0:
            return _pct(raw), "You are not currently saving, so this would not be rebuilt."
        return (
            _pct(raw),
            f"You keep {_pct(raw)} of what you earn, so you rebuild at about "
            f"{_money(picture.monthly_surplus)} a month.",
        )

    if key is FactorKey.DEBT_TO_INCOME:
        return (
            _pct(raw),
            f"Existing repayments take {_pct(raw)} of income"
            + (
                ", so an instalment plan is viable."
                if raw <= Decimal("0.36")
                else ", which limits any further borrowing."
            ),
        )

    if key is FactorKey.GOAL_IMPACT:
        if raw <= 0:
            return "no delay", "This does not push back any of your goals."
        goal = picture.top_goal_name or "your top goal"
        # Rounded, not truncated. The impact card renders the same figure from
        # the same day count, and 227 days shown as "7 months" here and "8
        # months" there — a few inches apart on one screen — reads as a bug
        # even though both are defensible roundings of 7.57.
        months = int((raw / Decimal(30)).to_integral_value(rounding=ROUND_HALF_UP))
        return (
            f"{int(raw)} days",
            f"Delays {goal} by roughly {months} months at your current savings rate.",
        )

    return (
        f"{raw}x",
        f"The price is {raw} times your monthly income of {_money(picture.monthly_income)}.",
    )


def _direction(points: Decimal) -> Direction:
    if points >= Decimal("70"):
        return Direction.POSITIVE
    if points >= Decimal("45"):
        return Direction.NEUTRAL
    return Direction.NEGATIVE


def _constraints(
    picture: FinancialPicture, price: Decimal, raws: dict[FactorKey, Decimal]
) -> tuple[TriggeredConstraint, ...]:
    """Thresholds that a high score must not be able to buy its way past."""
    triggered: list[TriggeredConstraint] = []

    reserves_after_months = raws[FactorKey.EMERGENCY_FUND_AFTER]
    monthly = picture.monthly_expenses if picture.monthly_expenses > 0 else Decimal(1)
    # Is this purchase big enough to be the cause of anything? See
    # `MATERIAL_PURCHASE_MONTHS`.
    material = (price / monthly) >= MATERIAL_PURCHASE_MONTHS
    if reserves_after_months < EMERGENCY_FUND_FLOOR_MONTHS:
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.EMERGENCY_FUND_FLOOR,
                caps_at=Verdict.WAIT,
                message=(
                    f"This would leave {_months(max(ZERO, reserves_after_months))} of emergency "
                    "cover. Below one month there is no cushion left, only a countdown — so this "
                    "is not recommended today however well the rest of your finances look."
                ),
            )
        )

    if picture.forecast_trough is not None and raws[FactorKey.FORECAST_TROUGH_AFTER] < 0:
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.NEGATIVE_TROUGH,
                caps_at=Verdict.WAIT,
                message=(
                    "Your projected balance drops below zero within the forecast window if you "
                    "buy now. That means an overdraft or a missed commitment, not merely a "
                    "tighter month."
                ),
            )
        )

    elif material and reserves_after_months < ADEQUATE_COVER_MONTHS:
        # `elif`: below the floor is already covered above, and stacking both
        # messages would say the same thing twice in different words.
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.BELOW_ADEQUATE_COVER,
                caps_at=Verdict.BUY_ON_EMI,
                message=(
                    f"Paying cash would leave {_months(reserves_after_months)} of cover, under "
                    "the three months that counts as adequate. Spreading the cost keeps the "
                    "cushion intact — this is a route, not a refusal."
                ),
            )
        )

    if material and picture.savings_rate is not None and picture.savings_rate < 0:
        # NOT_RECOMMENDED rather than WAIT, because there is no date to wait
        # for: someone whose balance shrinks every month does not become more
        # able to afford this by waiting. A "wait" here would have to name a
        # date that never arrives.
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.IN_DRAWDOWN,
                caps_at=Verdict.NOT_RECOMMENDED,
                message=(
                    "You are currently spending more than you earn, so savings are shrinking "
                    "month by month. A purchase this size accelerates that, however large the "
                    "balance looks today — and waiting does not help, because the trend is "
                    "downward."
                ),
            )
        )

    if raws[FactorKey.CASH_COVERAGE] < 1:
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.CANNOT_AFFORD,
                caps_at=Verdict.BUY_ON_EMI,
                message=(
                    f"The price exceeds your available savings of "
                    f"{_money(picture.liquid_reserves)}, so paying cash is not an option."
                ),
            )
        )

    if raws[FactorKey.DEBT_TO_INCOME] > DEBT_CEILING:
        triggered.append(
            TriggeredConstraint(
                code=ConstraintCode.ALREADY_OVEREXTENDED,
                caps_at=Verdict.NOT_RECOMMENDED,
                message=(
                    f"Debt repayments already take {_pct(raws[FactorKey.DEBT_TO_INCOME])} of your "
                    f"income, above the {DEBT_CEILING:.0%} ceiling most lenders use. Adding to "
                    "that is not advisable at any price."
                ),
            )
        )

    return tuple(triggered)


def affordable_from(picture: FinancialPicture, price: Decimal, today: date) -> date | None:
    """When this becomes affordable at the current rate of saving.

    "Affordable" means paying cash while keeping three months of expenses in
    reserve — not merely having the sticker price. A date that leaves someone
    with nothing is not the answer to "when can I buy this".

    Returns None when the honest answer is "not on this trajectory", which the
    caller turns into NOT_RECOMMENDED rather than a date two decades out.
    """
    target = price + (picture.monthly_expenses * Decimal(3))
    shortfall = target - picture.liquid_reserves
    if shortfall <= 0:
        return today

    surplus = picture.monthly_surplus
    if surplus <= 0:
        return None

    months_needed = shortfall / surplus
    days = int((months_needed * Decimal("30.44")).to_integral_value(rounding=ROUND_HALF_UP))
    return today + timedelta(days=days) if days <= MAX_WAIT_DAYS else None


def evaluate(
    picture: FinancialPicture,
    price: Decimal,
    *,
    today: date,
    computed_at: datetime,
    emi_available: bool = False,
) -> Evaluation:
    """Score a purchase and decide what to advise."""
    raws = _raw_values(picture, price)

    factors: list[ExplanationFactor] = []
    subscores: dict[FactorKey, Decimal] = {}
    total = ZERO

    for factor in FACTORS:
        raw = raws[factor.key]
        points, _label = factor.score(raw)
        contribution = (points * factor.weight).quantize(CENTS, rounding=ROUND_HALF_UP)
        display, detail = _describe(factor.key, raw, picture, price)

        subscores[factor.key] = points
        total += contribution
        factors.append(
            ExplanationFactor(
                name=factor.name,
                value=display,
                raw_value=raw,
                weight=factor.weight,
                contribution=contribution,
                direction=_direction(points),
                explanation=detail,
            )
        )

    score = total.quantize(CENTS, rounding=ROUND_HALF_UP)
    # Rounding each contribution independently can leave the sum a cent off the
    # weighted total. The score is the sum of its parts, so the remainder goes
    # to the largest contributor and the reconciliation stays exact (ADR-002).
    drift = score - sum((f.contribution for f in factors), ZERO)
    if drift and factors:
        biggest = max(range(len(factors)), key=lambda i: factors[i].contribution)
        original = factors[biggest]
        factors[biggest] = ExplanationFactor(
            name=original.name,
            value=original.value,
            raw_value=original.raw_value,
            weight=original.weight,
            contribution=original.contribution + drift,
            direction=original.direction,
            explanation=original.explanation,
        )

    provisional = verdict_for(score)
    triggered = _constraints(picture, price, raws)

    verdict = provisional
    for constraint in triggered:
        verdict = cap(verdict, constraint.caps_at)

    # An EMI verdict is only honest if an instalment plan actually exists and is
    # serviceable. The caller knows; without one, fall through to WAIT.
    if verdict is Verdict.BUY_ON_EMI and not emi_available:
        verdict = Verdict.WAIT

    when: date | None = None
    if verdict is Verdict.WAIT:
        when = affordable_from(picture, price, today)
        if when is None:
            # No date means no trajectory to this purchase. Saying "wait"
            # without an end is worse than saying no.
            verdict = Verdict.NOT_RECOMMENDED

    caveats = _caveats(picture, triggered, provisional, verdict)

    return Evaluation(
        verdict=verdict,
        score=score,
        confidence=_confidence(picture),
        explanation=Explanation(
            verdict=verdict.value.upper(),
            score=score,
            confidence=_confidence(picture),
            method=f"rubric_{RUBRIC_VERSION}",
            data_window=DataWindow(
                start=picture.window_start or today,
                end=picture.window_end or today,
                observation_days=picture.observation_days,
            ),
            factors=factors,
            caveats=caveats,
            computed_at=computed_at,
        ),
        constraints=triggered,
        affordable_from=when,
        score_verdict=provisional,
        subscores=subscores,
    )


def _confidence(picture: FinancialPicture) -> Decimal:
    """How much to trust this verdict.

    Bounded by history, like every other engine: advice built on six weeks of
    data is a guess, and the number that reaches the user should say so.
    """
    by_time = min(Decimal(picture.observation_days) / Decimal(365), Decimal(1))
    # A forecast the advisor could not obtain removes a whole weighted factor's
    # worth of evidence, so it costs confidence rather than being ignored.
    penalty = Decimal("0.85") if picture.forecast_trough is None else Decimal(1)
    return (by_time * penalty).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _caveats(
    picture: FinancialPicture,
    triggered: tuple[TriggeredConstraint, ...],
    provisional: Verdict,
    final: Verdict,
) -> list[str]:
    caveats: list[str] = []

    # Every triggered constraint is stated, whether or not it changed the
    # verdict.
    #
    # An earlier version only spoke up when the constraint *moved* the answer,
    # which meant a user whose emergency fund would be wiped out heard nothing
    # about it whenever the weighted score happened to reach the same
    # conclusion by itself. The reason for advice should not depend on whether
    # two mechanisms coincidentally agreed.
    if triggered:
        if final is not provisional:
            caveats.append(
                f"On the weighted score alone this would be "
                f"{provisional.value.replace('_', ' ')}. It is "
                f"{final.value.replace('_', ' ')} because a hard limit was crossed:"
            )
        else:
            caveats.append(
                "A hard limit was crossed, which fixes this verdict regardless of the score:"
            )
        caveats.extend(constraint.message for constraint in triggered)

    if picture.observation_days < 180:
        caveats.append(
            f"Based on {picture.observation_days} days of history — enough for a first view, "
            "not enough to have seen a full year of your spending."
        )

    if picture.forecast_trough is None:
        caveats.append(
            "No cash-flow forecast was available, so the effect on your lowest projected "
            "balance could not be checked."
        )

    caveats.extend(picture.forecast_caveats)
    caveats.append("Assumes your income and spending continue at their current levels.")
    return caveats
