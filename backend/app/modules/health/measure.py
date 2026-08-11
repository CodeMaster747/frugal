"""Measuring the six sub-metrics from ledger aggregates.

Separated from `scoring.py` on purpose: measurement is where the judgement calls
about *what counts* live (is a credit-card balance debt? does a bonus month
distort the savings rate?), and scoring is where the judgement calls about *what
is good* live. Mixing them produces a file nobody can review, because every
change looks like it might be either.

Every function here returns a `MetricInput`, and every one of them can return
`available=False`. That is the whole point — see the module docstring in
`scoring.py`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from statistics import fmean, pstdev

from app.modules.analytics.service import BudgetOutcome, SeriesPoint
from app.modules.health.rubric import MetricKey
from app.modules.health.scoring import MetricInput

ZERO = Decimal("0")

#: Months of cash flow needed before a stability figure means anything. Three
#: points can describe a line but not a variance a user would recognise.
MIN_MONTHS_FOR_STABILITY = 4

#: Below this, a savings rate is dominated by one unusual month.
MIN_MONTHS_FOR_RATE = 2


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _months(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)} months"


def savings_rate(cashflow: list[SeriesPoint]) -> MetricInput:
    """Share of income kept, across the observed months.

    Aggregated across months rather than averaged per month: averaging monthly
    rates lets one tiny-income month with a windfall dominate, and the honest
    question is what share of everything earned was kept.
    """
    months = [p for p in cashflow if p.income > 0 or p.expense > 0]
    if len(months) < MIN_MONTHS_FOR_RATE:
        return MetricInput(
            raw=None,
            display="—",
            detail="Not enough months of activity to judge a savings rate.",
            available=False,
            unavailable_because=f"{len(months)} months of activity, {MIN_MONTHS_FOR_RATE} needed",
        )

    income = sum((p.income for p in months), ZERO)
    expense = sum((p.expense for p in months), ZERO)

    if income <= 0:
        # A rate with no denominator is undefined, not zero. Reporting 0% would
        # read as "you saved nothing" rather than "we cannot say".
        return MetricInput(
            raw=None,
            display="—",
            detail="No income recorded, so there is no savings rate to compute.",
            available=False,
            unavailable_because="no income recorded in the window",
        )

    rate = ((income - expense) / income).quantize(Decimal("0.0001"))
    if rate >= Decimal("0.20"):
        detail = f"You keep {_pct(rate)} of what you earn, at or above the 20% healthy mark."
    elif rate >= ZERO:
        detail = f"You keep {_pct(rate)} of what you earn; 20% is the usual healthy target."
    else:
        detail = (
            f"You spent {_pct(-rate)} more than you earned over these "
            f"{len(months)} months, which draws down savings."
        )
    return MetricInput(raw=rate, display=_pct(rate), detail=detail)


def emergency_fund(liquid: Decimal, cashflow: list[SeriesPoint]) -> MetricInput:
    """Months of expenses covered by liquid assets.

    Liquid, not total: a net worth held in property or locked deposits does not
    pay rent in the month someone loses their job, and scoring it as though it
    does is the single most flattering error this rubric could make.
    """
    spending_months = [p for p in cashflow if p.expense > 0]
    if len(spending_months) < MIN_MONTHS_FOR_RATE:
        return MetricInput(
            raw=None,
            display="—",
            detail="Not enough spending history to know what one month costs you.",
            available=False,
            unavailable_because="fewer than two months of recorded spending",
        )

    monthly = Decimal(str(fmean(float(p.expense) for p in spending_months)))
    if monthly <= 0:
        return MetricInput(
            raw=None,
            display="—",
            detail="No recorded spending, so months of cover cannot be computed.",
            available=False,
            unavailable_because="no recorded spending",
        )

    months = (liquid / monthly).quantize(Decimal("0.01")) if liquid > 0 else ZERO
    if months >= 6:
        detail = (
            f"Liquid savings cover {_months(months)} of spending, at or past the 6-month target."
        )
    elif months >= 3:
        detail = f"Liquid savings cover {_months(months)}; 6 months is the target."
    else:
        detail = (
            f"Liquid savings cover only {_months(months)} of spending. "
            "Three months is the point at which a sudden loss of income stops being a crisis."
        )
    return MetricInput(raw=months, display=_months(months), detail=detail)


def debt_to_income(debt_paid: Decimal, cashflow: list[SeriesPoint]) -> MetricInput:
    """Share of income going to debt repayment.

    Zero debt is a real, measurable answer -- unlike the other metrics, absence
    of debt rows is information rather than missing data, so this one scores
    rather than abstaining.
    """
    income = sum((p.income for p in cashflow), ZERO)
    if income <= 0:
        return MetricInput(
            raw=None,
            display="—",
            detail="No income recorded, so debt-to-income cannot be computed.",
            available=False,
            unavailable_because="no income recorded in the window",
        )

    ratio = (debt_paid / income).quantize(Decimal("0.0001"))
    if ratio <= ZERO:
        detail = "No debt repayments recorded — nothing of your income is committed to debt."
    elif ratio <= Decimal("0.36"):
        detail = (
            f"Debt repayments take {_pct(ratio)} of income, below the 36% ceiling most lenders use."
        )
    else:
        detail = (
            f"Debt repayments take {_pct(ratio)} of income, above the 36% ceiling. "
            "This limits both borrowing and the ability to absorb a shock."
        )
    return MetricInput(raw=ratio, display=_pct(ratio), detail=detail)


def budget_discipline(outcomes: list[BudgetOutcome]) -> MetricInput:
    """Share of closed budget periods kept.

    Unavailable rather than perfect when no budgets exist. A user who has set
    none has not demonstrated discipline; scoring them 100 would reward not
    trying, which is precisely backwards.
    """
    if not outcomes:
        return MetricInput(
            raw=None,
            display="—",
            detail="No budget periods have closed yet, so discipline cannot be judged.",
            available=False,
            unavailable_because="no budgets set, or none have completed a period",
        )

    kept = sum(1 for o in outcomes if o.kept)
    ratio = (Decimal(kept) / Decimal(len(outcomes))).quantize(Decimal("0.0001"))
    display = f"{kept} of {len(outcomes)} kept"

    if kept == len(outcomes):
        detail = f"You stayed within all {len(outcomes)} budget periods that have closed."
    elif ratio >= Decimal("0.6"):
        breached = ", ".join(sorted({o.category_name for o in outcomes if not o.kept})[:3])
        detail = f"You kept {kept} of {len(outcomes)} budgets. Over on: {breached}."
    else:
        detail = (
            f"You kept only {kept} of {len(outcomes)} budgets. "
            "Budgets that are routinely exceeded are usually set too low rather than ignored."
        )
    return MetricInput(raw=ratio, display=display, detail=detail)


def cashflow_stability(cashflow: list[SeriesPoint]) -> MetricInput:
    """How much monthly net cash flow varies, relative to its own size.

    Coefficient of variation against *expenses* rather than against net: net
    can be near zero for a perfectly stable household, and dividing by it
    produces an enormous number that says nothing about stability.
    """
    months = [p for p in cashflow if p.income > 0 or p.expense > 0]
    if len(months) < MIN_MONTHS_FOR_STABILITY:
        return MetricInput(
            raw=None,
            display="—",
            detail="Not enough months yet to see how steady your cash flow is.",
            available=False,
            unavailable_because=(
                f"{len(months)} months of activity, {MIN_MONTHS_FOR_STABILITY} needed"
            ),
        )

    nets = [float(p.net) for p in months]
    scale = fmean(float(p.expense) for p in months)
    if scale <= 0:
        return MetricInput(
            raw=None,
            display="—",
            detail="No recorded spending to measure variability against.",
            available=False,
            unavailable_because="no recorded spending",
        )

    variation = Decimal(str(pstdev(nets) / scale)).quantize(Decimal("0.0001"))
    if variation <= Decimal("0.30"):
        display, detail = "steady", "Your month-to-month cash flow is consistent."
    elif variation <= Decimal("0.50"):
        display, detail = (
            "moderate",
            "Your cash flow varies moderately month to month, which makes planning harder.",
        )
    else:
        display, detail = (
            "variable",
            "Your cash flow swings a lot month to month. Irregular income or lumpy "
            "spending both do this, and they call for different responses.",
        )
    return MetricInput(raw=variation, display=display, detail=detail)


def growth(net_worth_trend: list[tuple[str, Decimal]]) -> MetricInput:
    """Average month-over-month change in net worth, as a proportion.

    Proportional rather than absolute: ₹5,000 a month is transformative on a
    ₹50,000 base and noise on a ₹5,000,000 one.
    """
    points = [value for _, value in net_worth_trend]
    if len(points) < 3:
        return MetricInput(
            raw=None,
            display="—",
            detail="Not enough months of net-worth history to see a trend.",
            available=False,
            unavailable_because=f"{len(points)} months of net-worth history, 3 needed",
        )

    changes: list[Decimal] = []
    for earlier, later in pairwise(points):
        if earlier <= 0:
            # A ratio against a zero or negative base is meaningless, not
            # infinite. Skipping the step is better than emitting a number that
            # would dominate the average.
            continue
        changes.append((later - earlier) / earlier)

    if not changes:
        return MetricInput(
            raw=None,
            display="—",
            detail="Net worth has not been positive long enough to measure growth.",
            available=False,
            unavailable_because="no month with a positive opening net worth",
        )

    rate = Decimal(str(fmean(float(c) for c in changes))).quantize(Decimal("0.0001"))
    sign = "+" if rate >= 0 else ""
    display = f"{sign}{(rate * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%/mo"

    monthly = f"{(rate * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"
    if rate >= Decimal("0.01"):
        detail = f"Net worth is growing about {monthly} per month."
    elif rate >= ZERO:
        detail = "Net worth is roughly flat month to month."
    else:
        detail = (
            f"Net worth is shrinking about {monthly.lstrip('-')} per month. "
            "Sustained, this reverses years of saving."
        )
    return MetricInput(raw=rate, display=display, detail=detail)


def measure_all(
    *,
    cashflow: list[SeriesPoint],
    liquid: Decimal,
    debt_paid: Decimal,
    budget_outcomes: list[BudgetOutcome],
    net_worth_trend: list[tuple[str, Decimal]],
) -> dict[MetricKey, MetricInput]:
    """Every sub-metric, measured from aggregates."""
    return {
        MetricKey.SAVINGS_RATE: savings_rate(cashflow),
        MetricKey.EMERGENCY_FUND: emergency_fund(liquid, cashflow),
        MetricKey.DEBT_TO_INCOME: debt_to_income(debt_paid, cashflow),
        MetricKey.BUDGET_DISCIPLINE: budget_discipline(budget_outcomes),
        MetricKey.CASHFLOW_STABILITY: cashflow_stability(cashflow),
        MetricKey.GROWTH: growth(net_worth_trend),
    }
