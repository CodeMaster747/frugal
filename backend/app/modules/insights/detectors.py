"""Insight detectors.

Each detector is a pure function from measured aggregates to zero or more
`Candidate` findings. No session, no clock -- the service supplies both, which
keeps every detector directly testable and keeps "what counts as notable" in one
readable place.

**A detector's job is to stay quiet.** Every threshold here exists to suppress a
finding that is technically true and not worth a user's attention: a 30% jump in
a category they spend ₹200 on, a budget missed by ₹12, a "new" subscription that
is the same one renewing. Insight features die by crying wolf, and the fix is
thresholds chosen for materiality rather than for detectability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.explanation import DataWindow, Direction, Explanation, Factor
from app.modules.analytics.service import BudgetOutcome, CategorySlice, SeriesPoint
from app.modules.insights.models import InsightType, Severity

ZERO = Decimal("0")

#: A category has to move by this much *and* by the absolute floor below before
#: it is worth mentioning. Percentage alone flags every small category; rupees
#: alone flags every large one having a normal month.
SPIKE_PCT = Decimal("0.40")
SPIKE_FLOOR = Decimal("2000")

#: A percentage is only quoted when the baseline is at least this large.
#:
#: Below it the arithmetic is still correct and the sentence is still absurd:
#: ₹46 last month against ₹12,082 this month is "up 26,133%", which reads as a
#: glitch and costs the whole feed its credibility. Sporadic categories -- a
#: laptop, a flight -- have near-zero baselines constantly, so this is the
#: common case rather than an edge one. Rupees are quoted instead.
PCT_BASELINE_FLOOR = Decimal("1000")

#: A budget missed by less than this is a rounding error, not a breach.
BREACH_FLOOR = Decimal("500")

#: A single transaction this many times the category's typical size is worth a
#: second look -- usually a typo, occasionally a fraud.
ANOMALY_MULTIPLE = Decimal("4")
ANOMALY_FLOOR = Decimal("5000")

#: Emergency fund below this many months is raised as a finding regardless of
#: the health score, because it is the single most consequential gap.
RESERVE_MONTHS_FLOOR = Decimal("3")

#: Savings rate moving by more than this between periods is a real change in
#: behaviour rather than month-to-month noise.
SAVINGS_SHIFT = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class Candidate:
    """A finding, before it is persisted or ranked."""

    insight_type: InsightType
    severity: Severity
    title: str
    body: str
    dedup_key: str
    confidence: Decimal
    explanation: Explanation
    impact_amount: Decimal | None = None
    subject_id: uuid.UUID | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def materiality(self) -> Decimal:
        """₹impact × confidence — the ranking key.

        An unquantifiable finding is not worthless, so it gets a nominal base
        rather than zero; without that, "your emergency fund is thin" would rank
        below every ₹300 category wobble.
        """
        base = self.impact_amount if self.impact_amount is not None else Decimal("1000")
        return (abs(base) * self.confidence).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _explanation(
    *,
    verdict: str,
    confidence: Decimal,
    window: DataWindow,
    computed_at: datetime,
    factors: list[Factor],
    caveats: list[str] | None = None,
) -> Explanation:
    return Explanation(
        verdict=verdict,
        score=None,
        confidence=confidence,
        method="rule_v1",
        data_window=window,
        factors=factors,
        caveats=caveats or [],
        computed_at=computed_at,
    )


def _money(value: Decimal) -> str:
    return f"₹{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def category_spikes(
    slices: list[CategorySlice], *, window: DataWindow, computed_at: datetime, period_label: str
) -> list[Candidate]:
    """Categories that jumped materially against the comparable prior period."""
    found: list[Candidate] = []

    for slice_ in slices:
        if slice_.change_pct is None or slice_.previous_amount <= 0:
            # No baseline means no spike. A category's first month is new
            # information, not a 100% increase.
            continue

        # `CategorySlice.change_pct` is a *percentage* (261.33), not a fraction.
        # Normalising here rather than comparing raw: the thresholds in this
        # file are all fractions, and mixing the two silently disabled this one
        # -- every category clearing the rupee floor fired regardless of how
        # small the relative move was.
        change = slice_.change_pct / Decimal(100)
        delta = slice_.amount - slice_.previous_amount
        if change < SPIKE_PCT or delta < SPIKE_FLOOR:
            continue

        quotable = slice_.previous_amount >= PCT_BASELINE_FLOOR
        if quotable:
            title = f"{slice_.name} spending is up {_pct(change)}"
            body = (
                f"You spent {_money(slice_.amount)} on {slice_.name.lower()} in the last 30 "
                f"days, {_money(delta)} more than the {_money(slice_.previous_amount)} in the "
                "30 days before."
            )
        else:
            # No percentage at all when the baseline cannot support one.
            title = f"{slice_.name} spending is up {_money(delta)}"
            body = (
                f"You spent {_money(slice_.amount)} on {slice_.name.lower()} in the last 30 "
                f"days, against {_money(slice_.previous_amount)} in the 30 days before. "
                "Occasional categories swing like this — worth a glance, not an alarm."
            )

        found.append(
            Candidate(
                insight_type=InsightType.CATEGORY_SPIKE,
                severity=(
                    Severity.WARNING if delta >= SPIKE_FLOOR * 5 and quotable else Severity.INFO
                ),
                title=title,
                body=body,
                dedup_key=f"category_spike:{slice_.slug}:{period_label}",
                confidence=Decimal("0.90"),
                impact_amount=delta,
                explanation=_explanation(
                    verdict="SPIKE",
                    confidence=Decimal("0.90"),
                    window=window,
                    computed_at=computed_at,
                    factors=[
                        Factor(
                            name="This period",
                            value=_money(slice_.amount),
                            raw_value=slice_.amount,
                            weight=Decimal("0.5"),
                            contribution=delta,
                            direction=Direction.NEGATIVE,
                            explanation=f"Spending on {slice_.name.lower()} in the current window.",
                        ),
                        Factor(
                            name="Comparable prior period",
                            value=_money(slice_.previous_amount),
                            raw_value=slice_.previous_amount,
                            weight=Decimal("0.5"),
                            contribution=ZERO,
                            direction=Direction.NEUTRAL,
                            explanation=(
                                "The same day range in the previous month, so a partial month "
                                "is never compared against a full one."
                            ),
                        ),
                    ],
                ),
                tags={"category": slice_.slug},
            )
        )

    return found


def budget_breaches(
    outcomes: list[BudgetOutcome], *, window: DataWindow, computed_at: datetime, period_label: str
) -> list[Candidate]:
    """Closed budget periods that were exceeded by a material amount."""
    found: list[Candidate] = []

    for outcome in outcomes:
        overspend = outcome.spent - outcome.limit
        if overspend < BREACH_FLOOR:
            continue

        share = (overspend / outcome.limit) if outcome.limit > 0 else ZERO
        found.append(
            Candidate(
                insight_type=InsightType.BUDGET_BREACH,
                severity=Severity.WARNING if share >= Decimal("0.25") else Severity.INFO,
                title=f"{outcome.category_name} budget exceeded by {_money(overspend)}",
                body=(
                    f"You budgeted {_money(outcome.limit)} for {outcome.category_name.lower()} "
                    f"and spent {_money(outcome.spent)}. A budget exceeded every month is "
                    "usually set too low rather than ignored."
                ),
                dedup_key=f"budget_breach:{outcome.category_name.lower()}:{period_label}",
                confidence=Decimal("1.000"),
                impact_amount=overspend,
                explanation=_explanation(
                    verdict="BREACHED",
                    confidence=Decimal("1.000"),
                    window=window,
                    computed_at=computed_at,
                    factors=[
                        Factor(
                            name="Budgeted",
                            value=_money(outcome.limit),
                            raw_value=outcome.limit,
                            weight=Decimal("0.5"),
                            contribution=ZERO,
                            direction=Direction.NEUTRAL,
                            explanation="The limit you set for this category.",
                        ),
                        Factor(
                            name="Spent",
                            value=_money(outcome.spent),
                            raw_value=outcome.spent,
                            weight=Decimal("0.5"),
                            contribution=overspend,
                            direction=Direction.NEGATIVE,
                            explanation=f"{_pct(share)} over the limit.",
                        ),
                    ],
                ),
            )
        )

    return found


def emergency_fund_low(
    reserves: Decimal,
    monthly_expense: Decimal,
    *,
    window: DataWindow,
    computed_at: datetime,
    period_label: str,
) -> list[Candidate]:
    """Reserves below the point where a lost month becomes a crisis."""
    if monthly_expense <= 0:
        return []

    months = (reserves / monthly_expense).quantize(Decimal("0.01"))
    if months >= RESERVE_MONTHS_FLOOR:
        return []

    shortfall = (RESERVE_MONTHS_FLOOR * monthly_expense) - reserves
    severity = Severity.CRITICAL if months < 1 else Severity.WARNING

    return [
        Candidate(
            insight_type=InsightType.EMERGENCY_FUND_LOW,
            severity=severity,
            title=f"Emergency fund covers {months} months",
            body=(
                f"Your liquid savings, after revolving debt, cover {months} months of typical "
                f"spending. Three months is the point at which a sudden loss of income stops "
                f"being an immediate crisis — that is {_money(shortfall)} away."
            ),
            dedup_key=f"emergency_fund_low:{period_label}",
            confidence=Decimal("0.95"),
            impact_amount=shortfall,
            explanation=_explanation(
                verdict="LOW_RESERVES",
                confidence=Decimal("0.95"),
                window=window,
                computed_at=computed_at,
                factors=[
                    Factor(
                        name="Liquid reserves",
                        value=_money(reserves),
                        raw_value=reserves,
                        weight=Decimal("0.5"),
                        contribution=ZERO,
                        direction=Direction.NEGATIVE,
                        explanation="Cash and savings, less any credit-card balance owed.",
                    ),
                    Factor(
                        name="Typical monthly spending",
                        value=_money(monthly_expense),
                        raw_value=monthly_expense,
                        weight=Decimal("0.5"),
                        contribution=shortfall,
                        direction=Direction.NEUTRAL,
                        explanation="Averaged across the months with recorded spending.",
                    ),
                ],
            ),
        )
    ]


def savings_rate_change(
    trend: list[tuple[str, Decimal | None]],
    *,
    window: DataWindow,
    computed_at: datetime,
    period_label: str,
) -> list[Candidate]:
    """A material shift in the share of income kept.

    Compares the last three months against the three before, rather than month
    against month: one bonus or one annual premium moves a single month enough
    to trigger on noise.
    """
    rates = [(label, rate) for label, rate in trend if rate is not None]
    if len(rates) < 6:
        return []

    recent = [r for _, r in rates[-3:]]
    prior = [r for _, r in rates[-6:-3]]
    recent_avg = sum(recent, ZERO) / len(recent)
    prior_avg = sum(prior, ZERO) / len(prior)
    shift = (recent_avg - prior_avg).quantize(Decimal("0.0001"))

    if abs(shift) < SAVINGS_SHIFT:
        return []

    improved = shift > 0
    return [
        Candidate(
            insight_type=InsightType.SAVINGS_RATE_CHANGE,
            severity=Severity.INFO if improved else Severity.WARNING,
            title=(
                f"You're saving {_pct(abs(shift))} {'more' if improved else 'less'} of your income"
            ),
            body=(
                f"Over the last three months you kept {_pct(recent_avg)} of what you earned, "
                f"against {_pct(prior_avg)} in the three before. "
                + (
                    "Worth knowing what changed, so you can keep it."
                    if improved
                    else "Worth knowing what changed, before it becomes the new normal."
                )
            ),
            dedup_key=f"savings_rate_change:{period_label}",
            confidence=Decimal("0.80"),
            explanation=_explanation(
                verdict="IMPROVED" if improved else "DECLINED",
                confidence=Decimal("0.80"),
                window=window,
                computed_at=computed_at,
                factors=[
                    Factor(
                        name="Last three months",
                        value=_pct(recent_avg),
                        raw_value=recent_avg,
                        weight=Decimal("0.5"),
                        contribution=shift,
                        direction=Direction.POSITIVE if improved else Direction.NEGATIVE,
                        explanation="Average share of income kept, most recent three months.",
                    ),
                    Factor(
                        name="Three months before",
                        value=_pct(prior_avg),
                        raw_value=prior_avg,
                        weight=Decimal("0.5"),
                        contribution=ZERO,
                        direction=Direction.NEUTRAL,
                        explanation="The baseline being compared against.",
                    ),
                ],
                caveats=["Months with no recorded income are excluded from both averages."],
            ),
        )
    ]


@dataclass(frozen=True, slots=True)
class OutlierTransaction:
    """A single transaction that is large relative to its category."""

    transaction_id: uuid.UUID
    merchant: str
    category_name: str
    amount: Decimal
    category_median: Decimal
    occurred_on: date


def anomalous_transactions(
    outliers: list[OutlierTransaction], *, window: DataWindow, computed_at: datetime
) -> list[Candidate]:
    """Transactions far larger than their category's norm.

    Usually a mistyped amount, occasionally a duplicate charge, rarely fraud.
    All three are worth one look and none justify an alarm, so these are `info`
    unless the amount is genuinely large.
    """
    found: list[Candidate] = []

    for outlier in outliers:
        if outlier.category_median <= 0 or outlier.amount < ANOMALY_FLOOR:
            continue
        multiple = (outlier.amount / outlier.category_median).quantize(Decimal("0.1"))
        if multiple < ANOMALY_MULTIPLE:
            continue

        found.append(
            Candidate(
                insight_type=InsightType.ANOMALOUS_TRANSACTION,
                severity=Severity.WARNING
                if outlier.amount >= ANOMALY_FLOOR * 10
                else Severity.INFO,
                title=f"{_money(outlier.amount)} at {outlier.merchant} is unusually large",
                body=(
                    f"This is {multiple}× your typical {outlier.category_name.lower()} "
                    f"transaction of {_money(outlier.category_median)}. Worth confirming it is "
                    "right — a mistyped amount distorts every number that follows."
                ),
                # Keyed on the transaction, so it is raised once ever rather
                # than once per period for as long as it stays in the window.
                dedup_key=f"anomalous_transaction:{outlier.transaction_id}",
                confidence=Decimal("0.70"),
                impact_amount=outlier.amount - outlier.category_median,
                subject_id=outlier.transaction_id,
                explanation=_explanation(
                    verdict="OUTLIER",
                    confidence=Decimal("0.70"),
                    window=window,
                    computed_at=computed_at,
                    factors=[
                        Factor(
                            name="This transaction",
                            value=_money(outlier.amount),
                            raw_value=outlier.amount,
                            weight=Decimal("0.5"),
                            contribution=outlier.amount - outlier.category_median,
                            direction=Direction.NEGATIVE,
                            explanation=f"Recorded on {outlier.occurred_on.isoformat()}.",
                        ),
                        Factor(
                            name="Typical for this category",
                            value=_money(outlier.category_median),
                            raw_value=outlier.category_median,
                            weight=Decimal("0.5"),
                            contribution=ZERO,
                            direction=Direction.NEUTRAL,
                            explanation=(
                                "Median rather than mean, so one large transaction does not "
                                "raise the bar that detects the next one."
                            ),
                        ),
                    ],
                    caveats=[
                        "A large transaction is often legitimate. This is a prompt, not a "
                        "judgement."
                    ],
                ),
            )
        )

    return found


@dataclass(frozen=True, slots=True)
class SubscriptionSummary:
    """Recurring subscription spend, this period against the baseline."""

    current_total: Decimal
    prior_total: Decimal
    count: int


def subscription_creep(
    summary: SubscriptionSummary,
    *,
    window: DataWindow,
    computed_at: datetime,
    period_label: str,
) -> list[Candidate]:
    """Subscriptions quietly accumulating.

    The archetypal invisible expense: no single charge is large enough to
    notice, and the total only becomes obvious when someone adds it up.
    """
    delta = summary.current_total - summary.prior_total
    if summary.prior_total <= 0 or delta < Decimal("300"):
        return []

    annualised = delta * 12
    return [
        Candidate(
            insight_type=InsightType.SUBSCRIPTION_CREEP,
            severity=Severity.INFO,
            title=f"Subscriptions up {_money(delta)} a month",
            body=(
                f"You are paying {_money(summary.current_total)} a month across "
                f"{summary.count} subscriptions, up from {_money(summary.prior_total)}. "
                f"That increase alone is {_money(annualised)} a year."
            ),
            dedup_key=f"subscription_creep:{period_label}",
            confidence=Decimal("0.85"),
            impact_amount=annualised,
            explanation=_explanation(
                verdict="CREEP",
                confidence=Decimal("0.85"),
                window=window,
                computed_at=computed_at,
                factors=[
                    Factor(
                        name="Subscriptions now",
                        value=_money(summary.current_total),
                        raw_value=summary.current_total,
                        weight=Decimal("0.5"),
                        contribution=annualised,
                        direction=Direction.NEGATIVE,
                        explanation=f"Across {summary.count} recurring charges.",
                    ),
                    Factor(
                        name="Baseline",
                        value=_money(summary.prior_total),
                        raw_value=summary.prior_total,
                        weight=Decimal("0.5"),
                        contribution=ZERO,
                        direction=Direction.NEUTRAL,
                        explanation="The comparable earlier period.",
                    ),
                ],
            ),
        )
    ]


@dataclass(frozen=True, slots=True)
class GoalProgress:
    """A savings goal and how it is tracking."""

    goal_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    target_date: date | None
    monthly_surplus: Decimal


def goals_at_risk(
    goals: list[GoalProgress],
    *,
    today: date,
    window: DataWindow,
    computed_at: datetime,
) -> list[Candidate]:
    """Goals that will not be met at the current rate of saving.

    Compared against actual surplus rather than against a plan the user typed
    once: the useful signal is the gap between intention and behaviour.
    """
    found: list[Candidate] = []

    for goal in goals:
        remaining = goal.target_amount - goal.current_amount
        if remaining <= 0 or goal.target_date is None or goal.target_date <= today:
            continue

        months_left = Decimal(max((goal.target_date - today).days, 1)) / Decimal("30.44")
        needed = (remaining / months_left).quantize(Decimal("0.01"))
        if goal.monthly_surplus >= needed:
            continue

        gap = needed - goal.monthly_surplus
        found.append(
            Candidate(
                insight_type=InsightType.GOAL_AT_RISK,
                severity=Severity.WARNING,
                title=f"“{goal.name}” is behind schedule",
                body=(
                    f"Reaching {_money(goal.target_amount)} by "
                    f"{goal.target_date.isoformat()} needs {_money(needed)} a month. "
                    f"You are currently saving about {_money(goal.monthly_surplus)}, "
                    f"a shortfall of {_money(gap)} a month."
                ),
                # Keyed on the goal, not the period: it is one ongoing situation
                # rather than a fresh finding every month.
                dedup_key=f"goal_at_risk:{goal.goal_id}",
                confidence=Decimal("0.75"),
                impact_amount=remaining,
                explanation=_explanation(
                    verdict="AT_RISK",
                    confidence=Decimal("0.75"),
                    window=window,
                    computed_at=computed_at,
                    factors=[
                        Factor(
                            name="Needed each month",
                            value=_money(needed),
                            raw_value=needed,
                            weight=Decimal("0.5"),
                            contribution=gap,
                            direction=Direction.NEGATIVE,
                            explanation=f"{_money(remaining)} remaining over "
                            f"{months_left.quantize(Decimal('0.1'))} months.",
                        ),
                        Factor(
                            name="Currently saving",
                            value=_money(goal.monthly_surplus),
                            raw_value=goal.monthly_surplus,
                            weight=Decimal("0.5"),
                            contribution=ZERO,
                            direction=Direction.NEUTRAL,
                            explanation="Your recent average monthly surplus.",
                        ),
                    ],
                    caveats=[
                        "Assumes your current rate of saving continues and that nothing else "
                        "claims the surplus."
                    ],
                ),
            )
        )

    return found


def cashflow_shortfall(
    cashflow: list[SeriesPoint], *, window: DataWindow, computed_at: datetime, period_label: str
) -> list[Candidate]:
    """Sustained negative net cash flow.

    One negative month is a large purchase. Two consecutive is a pattern, and
    saying so before the third is the entire value of noticing.
    """
    recent = [p for p in cashflow if p.income > 0 or p.expense > 0][-3:]
    negatives = [p for p in recent if p.net < 0]
    if len(recent) < 2 or len(negatives) < 2:
        return []

    total = sum((p.net for p in negatives), ZERO)
    return [
        Candidate(
            insight_type=InsightType.CASHFLOW_SHORTFALL,
            severity=Severity.CRITICAL,
            title=f"You spent more than you earned in {len(negatives)} of the last "
            f"{len(recent)} months",
            body=(
                f"Across those months you were {_money(abs(total))} short, which comes out "
                "of savings. One negative month is a large purchase; a run of them is a "
                "pattern worth interrupting."
            ),
            dedup_key=f"cashflow_shortfall:{period_label}",
            confidence=Decimal("0.95"),
            impact_amount=abs(total),
            explanation=_explanation(
                verdict="SHORTFALL",
                confidence=Decimal("0.95"),
                window=window,
                computed_at=computed_at,
                factors=[
                    Factor(
                        name=point.period,
                        value=_money(point.net),
                        raw_value=point.net,
                        weight=(Decimal(1) / Decimal(len(negatives))).quantize(Decimal("0.0001")),
                        contribution=point.net,
                        direction=Direction.NEGATIVE,
                        explanation=(
                            f"Earned {_money(point.income)}, spent {_money(point.expense)}."
                        ),
                    )
                    for point in negatives
                ],
            ),
        )
    ]


@dataclass(frozen=True, slots=True)
class RecurringItem:
    """A recurring charge, as the finance module records it."""

    item_id: uuid.UUID
    name: str
    amount: Decimal
    cadence: str
    #: When the *pattern* started, not when we noticed it. Using the row's
    #: creation date would make every long-standing commitment "new" on the day
    #: detection first runs.
    first_seen_on: date
    is_auto_detected: bool
    kind: str = "expense"


def new_recurring(
    items: list[RecurringItem], *, since: date, window: DataWindow, computed_at: datetime
) -> list[Candidate]:
    """Recurring commitments that appeared recently.

    Only auto-detected ones. A recurring item the user typed in themselves is
    not news to them, and telling someone about a thing they just did is the
    fastest way to teach them the feed is not worth reading.

    Reported as an annual figure because that is the number that changes a
    decision: ₹499 a month is easy to wave through, ₹5,988 a year is not.
    """
    found: list[Candidate] = []

    for item in items:
        if not item.is_auto_detected or item.first_seen_on < since:
            continue
        # Income only looks like a commitment from the schema's point of view.
        # "New recurring charge: salary" is wrong on its face, and because it
        # annualises to the largest number in the ledger it ranked *first* --
        # burying the budget breach the user could actually act on.
        if item.kind == "income":
            continue

        annualised = item.amount * _ANNUAL_MULTIPLES.get(item.cadence, Decimal("12"))
        found.append(
            Candidate(
                insight_type=InsightType.NEW_RECURRING,
                severity=Severity.INFO,
                title=f"New recurring charge: {item.name}",
                body=(
                    f"{_money(item.amount)} {item.cadence}, first seen on "
                    f"{item.first_seen_on.isoformat()}. That is {_money(annualised)} a year "
                    "if it continues."
                ),
                # Keyed on the item: a new commitment is news once, not monthly.
                dedup_key=f"new_recurring:{item.item_id}",
                confidence=Decimal("0.80"),
                impact_amount=annualised,
                explanation=_explanation(
                    verdict="NEW_COMMITMENT",
                    confidence=Decimal("0.80"),
                    window=window,
                    computed_at=computed_at,
                    factors=[
                        Factor(
                            name="Charge",
                            value=f"{_money(item.amount)} {item.cadence}",
                            raw_value=item.amount,
                            weight=Decimal("0.6"),
                            contribution=annualised,
                            direction=Direction.NEGATIVE,
                            explanation="Detected from a repeating pattern in your transactions.",
                        ),
                        Factor(
                            name="Annual cost",
                            value=_money(annualised),
                            raw_value=annualised,
                            weight=Decimal("0.4"),
                            contribution=ZERO,
                            direction=Direction.NEUTRAL,
                            explanation=(
                                "The figure worth deciding on. A monthly price is designed to "
                                "feel small."
                            ),
                        ),
                    ],
                    caveats=[
                        "Detected automatically from repeating charges, so an irregular but "
                        "similar-looking series can be misread as recurring."
                    ],
                ),
            )
        )

    return found


#: Charges per year, by cadence. Used to annualise a recurring commitment.
_ANNUAL_MULTIPLES: dict[str, Decimal] = {
    "weekly": Decimal("52"),
    "fortnightly": Decimal("26"),
    "monthly": Decimal("12"),
    "quarterly": Decimal("4"),
    "yearly": Decimal("1"),
}
