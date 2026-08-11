"""The affordability rubric — weights, bands, and the constraints that override them.

Pure. No session, no clock, no I/O, which is what makes the scenario matrix in
`tests/unit/test_advisor_matrix.py` possible.

**The most important thing in this file is that the score does not always
decide.** A weighted rubric is a good way to rank *degrees* of affordability and
a bad way to express "this would leave you with three weeks of runway". Those
are not the same claim, and averaging the second into a score lets a strong
savings rate and a low debt ratio outvote it — which is exactly how a
plausible-looking rubric ends up telling someone to drain their emergency fund.

So there are two mechanisms:

* **Seven weighted factors** producing a 0–100 affordability score, for the
  ordinary case where the question is one of degree.
* **Hard constraints** that cap or override the verdict regardless of score,
  for the cases where a threshold has genuinely been crossed.

The constraints are few, they are stated in the response, and each one has a
reason a person would accept. Anything that can be a factor should be a factor;
a constraint is for things a high score should not be able to buy its way past.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

RUBRIC_VERSION = "v1"

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class Verdict(StrEnum):
    BUY_NOW = "buy_now"
    BUY_ON_EMI = "buy_on_emi"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class FactorKey(StrEnum):
    EMERGENCY_FUND_AFTER = "emergency_fund_after"
    FORECAST_TROUGH_AFTER = "forecast_trough_after"
    CASH_COVERAGE = "cash_coverage"
    SAVINGS_RATE = "savings_rate"
    DEBT_TO_INCOME = "debt_to_income"
    GOAL_IMPACT = "goal_impact"
    INCOME_MULTIPLE = "income_multiple"


@dataclass(frozen=True, slots=True)
class Band:
    threshold: Decimal
    points: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class Factor:
    key: FactorKey
    name: str
    weight: Decimal
    bands: tuple[Band, ...]
    higher_is_better: bool = True

    def score(self, raw: Decimal) -> tuple[Decimal, str]:
        for band in self.bands:
            hit = raw >= band.threshold if self.higher_is_better else raw <= band.threshold
            if hit:
                return band.points, band.label
        return ZERO, self.bands[-1].label


#: Weights sum to 1.00, asserted by test.
#:
#: Emergency fund after the purchase carries the most weight because it is the
#: factor that most changes what happens if something goes wrong, and because it
#: is the one people most reliably talk themselves out of. Income multiple
#: carries the least: it is a useful sanity check and a poor decision rule on its
#: own -- a ₹2,00,000 purchase is reckless on ₹40,000 a month and unremarkable on
#: ₹4,00,000.
FACTORS: tuple[Factor, ...] = (
    Factor(
        key=FactorKey.EMERGENCY_FUND_AFTER,
        name="Emergency fund after purchase",
        weight=Decimal("0.25"),
        # Months of expenses still covered once the money has gone.
        bands=(
            Band(Decimal("6"), HUNDRED, "fully funded"),
            Band(Decimal("4"), Decimal("85"), "strong"),
            Band(Decimal("3"), Decimal("70"), "adequate"),
            Band(Decimal("2"), Decimal("45"), "thin"),
            Band(Decimal("1"), Decimal("20"), "very thin"),
            Band(ZERO, ZERO, "wiped out"),
        ),
    ),
    Factor(
        key=FactorKey.FORECAST_TROUGH_AFTER,
        name="Lowest projected balance after",
        weight=Decimal("0.18"),
        # The trough as a multiple of one month's expenses.
        bands=(
            Band(Decimal("3"), HUNDRED, "comfortable"),
            Band(Decimal("1.5"), Decimal("80"), "sound"),
            Band(Decimal("0.75"), Decimal("55"), "tight"),
            Band(Decimal("0.25"), Decimal("25"), "very tight"),
            Band(Decimal("-99"), ZERO, "goes negative"),
        ),
    ),
    Factor(
        key=FactorKey.CASH_COVERAGE,
        name="Cash cover for the price",
        weight=Decimal("0.15"),
        # Liquid reserves divided by the price. Below 1 they cannot pay cash.
        bands=(
            Band(Decimal("4"), HUNDRED, "easily covered"),
            Band(Decimal("2.5"), Decimal("85"), "well covered"),
            Band(Decimal("1.5"), Decimal("65"), "covered"),
            Band(Decimal("1"), Decimal("40"), "just covered"),
            Band(ZERO, ZERO, "not covered"),
        ),
    ),
    Factor(
        key=FactorKey.SAVINGS_RATE,
        name="Savings rate",
        weight=Decimal("0.13"),
        # How fast they rebuild. This is the factor that separates WAIT from
        # NOT_RECOMMENDED: a strong saver recovers from a big purchase.
        bands=(
            Band(Decimal("0.30"), HUNDRED, "excellent"),
            Band(Decimal("0.20"), Decimal("85"), "healthy"),
            Band(Decimal("0.10"), Decimal("60"), "modest"),
            Band(Decimal("0.02"), Decimal("30"), "thin"),
            Band(Decimal("-99"), ZERO, "spending more than earned"),
        ),
    ),
    Factor(
        key=FactorKey.DEBT_TO_INCOME,
        name="Existing debt load",
        weight=Decimal("0.12"),
        higher_is_better=False,
        bands=(
            Band(Decimal("0.10"), HUNDRED, "minimal"),
            Band(Decimal("0.20"), Decimal("85"), "comfortable"),
            Band(Decimal("0.36"), Decimal("60"), "manageable"),
            Band(Decimal("0.43"), Decimal("25"), "stretched"),
            Band(Decimal("999"), ZERO, "overextended"),
        ),
    ),
    Factor(
        key=FactorKey.GOAL_IMPACT,
        name="Impact on your goals",
        weight=Decimal("0.10"),
        higher_is_better=False,
        # Days of delay to the highest-priority goal.
        bands=(
            Band(Decimal("0"), HUNDRED, "no delay"),
            Band(Decimal("30"), Decimal("80"), "about a month"),
            Band(Decimal("90"), Decimal("55"), "a quarter"),
            Band(Decimal("180"), Decimal("30"), "half a year"),
            Band(Decimal("99999"), Decimal("5"), "substantial"),
        ),
    ),
    Factor(
        key=FactorKey.INCOME_MULTIPLE,
        name="Price against monthly income",
        weight=Decimal("0.07"),
        higher_is_better=False,
        bands=(
            Band(Decimal("0.25"), HUNDRED, "a small share"),
            Band(Decimal("0.75"), Decimal("80"), "manageable"),
            Band(Decimal("1.5"), Decimal("55"), "a big purchase"),
            Band(Decimal("3"), Decimal("25"), "several months of income"),
            Band(Decimal("999"), Decimal("5"), "very large"),
        ),
    ),
)

BY_KEY: dict[FactorKey, Factor] = {f.key: f for f in FACTORS}


# --- hard constraints -------------------------------------------------------


class ConstraintCode(StrEnum):
    EMERGENCY_FUND_FLOOR = "emergency_fund_floor"
    NEGATIVE_TROUGH = "negative_trough"
    CANNOT_AFFORD = "cannot_afford"
    ALREADY_OVEREXTENDED = "already_overextended"
    BELOW_ADEQUATE_COVER = "below_adequate_cover"
    IN_DRAWDOWN = "in_drawdown"


#: Below this many months of cover after the purchase, no score justifies it.
#: One month is not a cushion; it is a countdown.
EMERGENCY_FUND_FLOOR_MONTHS = Decimal("1")

#: Paying cash and landing below this many months of cover caps the verdict at
#: BUY_ON_EMI rather than BUY_NOW.
#:
#: Found by the scenario matrix. The emergency-fund factor scores 70 at three
#: months and 45 at two, and at weight 0.25 that six-point difference was not
#: enough to move a verdict -- so someone on ₹95,000 a month was told to buy a
#: ₹95,000 laptop in cash and land at 2.2 months of cover. Three months is the
#: line the health rubric already calls "adequate", and dropping below it is
#: precisely when spreading the cost is the better route. Not a refusal: the
#: purchase is still advised, by a different means.
ADEQUATE_COVER_MONTHS = Decimal("3")

#: A purchase smaller than this many months of expenses does not trigger the
#: soft constraints at all.
#:
#: Also found by the matrix. Without it, a ₹25,000 phone that moved someone from
#: 3.33 to 2.92 months of cover was answered with "take it on instalments" —
#: technically inside the rule and obviously silly advice. And a ₹8,000 pair of
#: headphones, bought by someone with ₹400,000 in reserve who happens to be
#: spending 2.5% more than they earn, was capped for "accelerating a decline" it
#: could not measurably accelerate.
#:
#: A constraint should fire when a purchase *causes* a problem, not when the
#: user is merely near a line while buying something incidental.
MATERIAL_PURCHASE_MONTHS = Decimal("0.5")

#: Debt service above this share of income means an EMI route is off the table
#: regardless of how affordable the item looks in isolation.
DEBT_CEILING = Decimal("0.43")


@dataclass(frozen=True, slots=True)
class Constraint:
    """A threshold crossed, and what it forces."""

    code: ConstraintCode
    #: The best verdict still permitted once this fires.
    caps_at: Verdict
    message: str


#: Score thresholds for the ordinary case, applied only when no constraint fires.
BUY_NOW_SCORE = Decimal("70")
EMI_SCORE = Decimal("55")
WAIT_SCORE = Decimal("30")


def verdict_for(score: Decimal) -> Verdict:
    """Verdict from score alone, before constraints are applied."""
    if score >= BUY_NOW_SCORE:
        return Verdict.BUY_NOW
    if score >= EMI_SCORE:
        return Verdict.BUY_ON_EMI
    if score >= WAIT_SCORE:
        return Verdict.WAIT
    return Verdict.NOT_RECOMMENDED


#: Verdicts from best to worst, so capping is a list position rather than a
#: nest of comparisons.
SEVERITY: tuple[Verdict, ...] = (
    Verdict.BUY_NOW,
    Verdict.BUY_ON_EMI,
    Verdict.WAIT,
    Verdict.NOT_RECOMMENDED,
)


def cap(verdict: Verdict, ceiling: Verdict) -> Verdict:
    """The worse of two verdicts."""
    return verdict if SEVERITY.index(verdict) >= SEVERITY.index(ceiling) else ceiling


def total_weight() -> Decimal:
    return sum((f.weight for f in FACTORS), ZERO)


def published() -> dict[str, object]:
    """The rubric as data, for `GET /advisor/rubric`.

    Published for the same reason the health rubric is: a recommendation the
    user cannot interrogate is an instruction, and this product is not in the
    business of issuing those.
    """
    return {
        "version": RUBRIC_VERSION,
        "total_weight": format(total_weight(), "f"),
        "score_thresholds": {
            "buy_now": format(BUY_NOW_SCORE, "f"),
            "buy_on_emi": format(EMI_SCORE, "f"),
            "wait": format(WAIT_SCORE, "f"),
        },
        "factors": [
            {
                "key": f.key.value,
                "name": f.name,
                "weight": format(f.weight, "f"),
                "higher_is_better": f.higher_is_better,
                "bands": [
                    {
                        "at_least" if f.higher_is_better else "at_most": format(b.threshold, "f"),
                        "points": format(b.points, "f"),
                        "label": b.label,
                    }
                    for b in f.bands
                ],
            }
            for f in FACTORS
        ],
        "hard_constraints": [
            {
                "code": ConstraintCode.EMERGENCY_FUND_FLOOR.value,
                "rule": (
                    f"Emergency fund below {EMERGENCY_FUND_FLOOR_MONTHS} month of expenses "
                    "after the purchase"
                ),
                "caps_at": Verdict.WAIT.value,
            },
            {
                "code": ConstraintCode.NEGATIVE_TROUGH.value,
                "rule": "Projected balance goes negative within the forecast horizon",
                "caps_at": Verdict.WAIT.value,
            },
            {
                "code": ConstraintCode.CANNOT_AFFORD.value,
                "rule": "Price exceeds available liquid savings",
                "caps_at": Verdict.BUY_ON_EMI.value,
            },
            {
                "code": ConstraintCode.BELOW_ADEQUATE_COVER.value,
                "rule": (
                    f"Paying cash would leave under {ADEQUATE_COVER_MONTHS} months of cover, "
                    f"for a purchase of at least {MATERIAL_PURCHASE_MONTHS} months of expenses"
                ),
                "caps_at": Verdict.BUY_ON_EMI.value,
            },
            {
                "code": ConstraintCode.IN_DRAWDOWN.value,
                "rule": "Spending exceeds income, for a material purchase",
                "caps_at": Verdict.NOT_RECOMMENDED.value,
            },
            {
                "code": ConstraintCode.ALREADY_OVEREXTENDED.value,
                "rule": f"Existing debt service above {DEBT_CEILING:.0%} of income",
                "caps_at": Verdict.NOT_RECOMMENDED.value,
            },
        ],
    }


def quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
