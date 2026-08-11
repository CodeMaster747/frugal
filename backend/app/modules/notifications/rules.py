"""Notification rules.

Pure functions from measured state to candidate notifications. No session, no
clock — the service supplies both.

**Every rule here answers "would a person want to be interrupted for this?"**
That is a higher bar than "is this true", and a much higher bar than the insight
engine's. An insight sits in a feed until someone looks; a notification arrives.
So the thresholds are deliberately less sensitive than the equivalent insight
detectors, and each rule says why.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.modules.notifications.models import NotificationCategory, Urgency

ZERO = Decimal("0")

#: A budget is flagged when spending passes this share of the limit, *before*
#: the period ends. Warning at 100% is a receipt, not an alert -- the money is
#: already gone and there is nothing left to decide.
BUDGET_WARN_AT = Decimal("0.85")

#: Bills are announced this many days ahead. Long enough to move money, short
#: enough that it is still relevant.
BILL_LEAD_DAYS = 3

#: Subscription renewals get more notice, because cancelling takes longer than
#: paying and the whole value is the chance to opt out.
RENEWAL_LEAD_DAYS = 7

#: Goal milestones worth mentioning. Not every percent -- four moments in a
#: goal's life, each of which actually feels like something.
GOAL_MILESTONES: tuple[Decimal, ...] = (
    Decimal("0.25"),
    Decimal("0.50"),
    Decimal("0.75"),
    Decimal("1.00"),
)


@dataclass(frozen=True, slots=True)
class Candidate:
    category: NotificationCategory
    urgency: Urgency
    subject: str
    body: str
    dedup_key: str
    link: str | None = None


def _money(value: Decimal) -> str:
    return f"₹{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)}%"


@dataclass(frozen=True, slots=True)
class BudgetState:
    category_name: str
    category_slug: str
    limit: Decimal
    spent: Decimal
    period_label: str
    days_left: int


def budget_alerts(budgets: list[BudgetState]) -> list[Candidate]:
    """A budget approaching or past its limit, while there is still time.

    Skipped once the period is over: telling someone on the 1st that they
    overspent in the month just gone is a report, and reports belong in the
    insight feed rather than in a notification.
    """
    found: list[Candidate] = []

    for budget in budgets:
        if budget.limit <= 0 or budget.days_left <= 0:
            continue

        used = (budget.spent / budget.limit).quantize(Decimal("0.0001"))
        if used < BUDGET_WARN_AT:
            continue

        over = budget.spent > budget.limit
        remaining = budget.limit - budget.spent

        found.append(
            Candidate(
                category=NotificationCategory.BUDGET,
                urgency=Urgency.DAILY,
                subject=(
                    f"{budget.category_name} budget is over by {_money(-remaining)}"
                    if over
                    else f"{budget.category_name} budget is {_pct(used)} spent"
                ),
                body=(
                    f"You have spent {_money(budget.spent)} of your "
                    f"{_money(budget.limit)} {budget.category_name.lower()} budget with "
                    f"{budget.days_left} days left in the period."
                    + ("" if over else f" That leaves {_money(remaining)}.")
                ),
                dedup_key=f"budget:{budget.category_slug}:{budget.period_label}:"
                f"{'over' if over else 'warn'}",
                link="/transactions",
            )
        )

    return found


@dataclass(frozen=True, slots=True)
class UpcomingItem:
    item_id: uuid.UUID
    name: str
    amount: Decimal
    due_on: date
    item_type: str
    cadence: str


def bill_reminders(items: list[UpcomingItem], *, today: date) -> list[Candidate]:
    """Committed payments falling due shortly."""
    found: list[Candidate] = []

    for item in items:
        if item.item_type == "subscription":
            continue  # handled by `renewal_reminders`, with more notice
        days = (item.due_on - today).days
        if not 0 <= days <= BILL_LEAD_DAYS:
            continue

        found.append(
            Candidate(
                category=NotificationCategory.BILL,
                urgency=Urgency.DAILY,
                subject=f"{item.name} is due {'today' if days == 0 else f'in {days} days'}",
                body=(
                    f"{_money(item.amount)} is due on {item.due_on.isoformat()}. "
                    "This is a recurring payment detected from your transactions."
                ),
                dedup_key=f"bill:{item.item_id}:{item.due_on.isoformat()}",
                link="/transactions",
            )
        )

    return found


def renewal_reminders(items: list[UpcomingItem], *, today: date) -> list[Candidate]:
    """Subscriptions about to renew.

    Separate from bills and with a week's notice, because the useful action is
    *cancelling*, and cancelling takes longer than paying. A reminder that
    arrives the morning a subscription renews is a notification about something
    the user can no longer change.
    """
    found: list[Candidate] = []

    for item in items:
        if item.item_type != "subscription":
            continue
        days = (item.due_on - today).days
        if not 0 <= days <= RENEWAL_LEAD_DAYS:
            continue

        annual = item.amount * (Decimal(12) if item.cadence == "monthly" else Decimal(1))
        found.append(
            Candidate(
                category=NotificationCategory.RENEWAL,
                urgency=Urgency.DAILY,
                subject=f"{item.name} renews in {days} days",
                body=(
                    f"{_money(item.amount)} {item.cadence}, which is {_money(annual)} a "
                    "year. If you no longer use it, this is the point at which "
                    "cancelling still saves the next payment."
                ),
                dedup_key=f"renewal:{item.item_id}:{item.due_on.isoformat()}",
                link="/forecast",
            )
        )

    return found


@dataclass(frozen=True, slots=True)
class GoalState:
    goal_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal


def goal_milestones(goals: list[GoalState]) -> list[Candidate]:
    """A goal crossing a quarter mark.

    The dedup key includes the milestone, so each is announced once and progress
    that wobbles back and forth across a line does not re-announce.
    """
    found: list[Candidate] = []

    for goal in goals:
        if goal.target_amount <= 0:
            continue
        progress = (goal.current_amount / goal.target_amount).quantize(Decimal("0.0001"))

        reached = [m for m in GOAL_MILESTONES if progress >= m]
        if not reached:
            continue
        milestone = max(reached)

        complete = milestone >= Decimal("1.00")
        found.append(
            Candidate(
                category=NotificationCategory.GOAL_MILESTONE,
                urgency=Urgency.DAILY,
                subject=(
                    f"You reached your {goal.name} goal"
                    if complete
                    else f"{goal.name} is {_pct(milestone)} of the way there"
                ),
                body=(
                    f"{_money(goal.current_amount)} of {_money(goal.target_amount)}."
                    + ("" if complete else " Keep going.")
                ),
                dedup_key=f"goal:{goal.goal_id}:{milestone}",
                link="/transactions",
            )
        )

    return found


def forecast_shortfall(
    *,
    shortfall_dates: list[date],
    trough_amount: Decimal | None,
    trough_on: date | None,
    today: date,
) -> list[Candidate]:
    """A projected balance going negative.

    The one rule here that is `IMMEDIATE`. Everything else can wait for the
    morning digest; money running out is the case where a day's delay costs a
    returned payment.

    Keyed on the *month* of the first shortfall rather than the exact date, so a
    projection that shifts by a day or two as new transactions land does not
    re-notify.
    """
    if not shortfall_dates:
        return []

    first = min(shortfall_dates)
    days_away = (first - today).days

    return [
        Candidate(
            category=NotificationCategory.FORECAST_SHORTFALL,
            urgency=Urgency.IMMEDIATE,
            subject=f"Your balance could go negative in {days_away} days",
            body=(
                f"On current projections your balance dips below zero around "
                f"{first.isoformat()}"
                + (
                    f", reaching {_money(trough_amount)} on {trough_on.isoformat()}."
                    if trough_amount is not None and trough_on is not None
                    else "."
                )
                + " This is the pessimistic edge of the forecast, not the most likely "
                "path — but it is the one worth planning around."
            ),
            dedup_key=f"shortfall:{first.strftime('%Y-%m')}",
            link="/forecast",
        )
    ]


def price_drop(
    *,
    product_name: str,
    product_id: uuid.UUID,
    previous: Decimal,
    now: Decimal,
    seller: str,
    is_lowest: bool,
    on: date,
) -> Candidate:
    """A tracked product getting cheaper.

    Built from an existing `PriceAlert` rather than re-detecting: M9 already
    decided what counts as a drop worth raising, and a second opinion here would
    mean two thresholds to keep in step.
    """
    drop = previous - now
    fraction = (drop / previous).quantize(Decimal("0.0001")) if previous > 0 else ZERO

    return Candidate(
        category=NotificationCategory.PRICE_DROP,
        urgency=Urgency.DAILY,
        subject=f"{product_name} is {_money(drop)} cheaper",
        body=(
            f"Now {_money(now)} at {seller}, down {_pct(fraction)} from "
            f"{_money(previous)}."
            + (" That is the lowest price we have recorded." if is_lowest else "")
        ),
        dedup_key=f"price_drop:{product_id}:{on.isoformat()}",
        link="/watchlist",
    )


def next_due(item_due: date, cadence: str, *, today: date) -> date:
    """Roll a due date forward past today.

    A recurring item whose stored due date has slipped into the past would
    otherwise never trigger a reminder again.
    """
    step = {
        "weekly": 7,
        "fortnightly": 14,
        "monthly": 30,
        "quarterly": 91,
        "yearly": 365,
    }.get(cadence, 30)

    due = item_due
    while due < today:
        due += timedelta(days=step)
    return due
