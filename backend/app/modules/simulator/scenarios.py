"""Scenario shapes and their arithmetic.

Pure. No session, no clock, no I/O.

**A scenario is four kinds of change, not four features.** "Take a holiday",
"buy a car", "change jobs", and "have a baby" look like different products and
are the same three levers underneath: money out once, money out (or in) every
month, and for how long. Modelling them as one generic shape rather than four
bespoke calculators is the difference between a feature that answers the
question the user actually has and a menu that does not contain it.

So the engine takes a list of `Change` values, and the named scenarios in
`TEMPLATES` are nothing but pre-filled lists. A user can always build their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

ZERO = Decimal("0")
CENTS = Decimal("0.01")


class ChangeKind(StrEnum):
    #: A single payment on a date — a deposit, a holiday, a wedding.
    ONE_OFF = "one_off"
    #: A change to what leaves every month — rent, an EMI, a subscription.
    RECURRING_EXPENSE = "recurring_expense"
    #: A change to what arrives every month — a raise, a new job, a loss.
    RECURRING_INCOME = "recurring_income"


@dataclass(frozen=True, slots=True)
class Change:
    """One lever, moved.

    `amount` is always a magnitude; `kind` carries the direction. A negative
    amount would make "reduce a recurring expense" ambiguous with "an expense
    that pays you", and the sign convention is the sort of thing that reads
    fine and is wrong in one branch.
    """

    kind: ChangeKind
    label: str
    amount: Decimal
    #: When it starts. Month 0 is "from now".
    starts_in_months: int = 0
    #: How long it lasts. None means indefinitely.
    lasts_months: int | None = None
    #: True when this *reduces* the flow — a rent cut, a pay cut.
    is_reduction: bool = False

    def signed_monthly(self) -> Decimal:
        """Effect on monthly surplus. Positive means more money kept."""
        if self.kind is ChangeKind.ONE_OFF:
            return ZERO
        direction = Decimal(-1) if self.is_reduction else Decimal(1)
        if self.kind is ChangeKind.RECURRING_EXPENSE:
            # Spending more reduces surplus; spending less increases it.
            return -self.amount * direction
        return self.amount * direction

    def active_in_month(self, month: int) -> bool:
        if month < self.starts_in_months:
            return False
        if self.lasts_months is None:
            return True
        return month < self.starts_in_months + self.lasts_months

    def one_off_in_month(self, month: int) -> Decimal:
        if self.kind is not ChangeKind.ONE_OFF or month != self.starts_in_months:
            return ZERO
        return -self.amount if not self.is_reduction else self.amount


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named set of changes."""

    name: str
    changes: tuple[Change, ...]
    horizon_months: int = 24
    notes: str = ""

    @property
    def total_one_off(self) -> Decimal:
        return sum((c.amount for c in self.changes if c.kind is ChangeKind.ONE_OFF), ZERO)

    def monthly_delta_at(self, month: int) -> Decimal:
        """Change to monthly surplus in a given month."""
        return sum((c.signed_monthly() for c in self.changes if c.active_in_month(month)), ZERO)


@dataclass(frozen=True, slots=True)
class Position:
    """The user's starting point, measured by the service."""

    liquid_reserves: Decimal
    monthly_income: Decimal
    monthly_expenses: Decimal
    health_score: Decimal | None
    observation_days: int
    window_start: date | None = None
    window_end: date | None = None

    @property
    def monthly_surplus(self) -> Decimal:
        return self.monthly_income - self.monthly_expenses

    @property
    def emergency_fund_months(self) -> Decimal:
        if self.monthly_expenses <= 0:
            return ZERO
        return (self.liquid_reserves / self.monthly_expenses).quantize(CENTS)


@dataclass(frozen=True, slots=True)
class MonthPoint:
    month: int
    on: date
    reserves: Decimal
    monthly_surplus: Decimal


@dataclass(frozen=True, slots=True)
class Projection:
    """What the scenario does, month by month."""

    points: tuple[MonthPoint, ...]
    #: Months where reserves go negative. The thing that turns a plan into a
    #: warning.
    shortfall_months: tuple[int, ...] = ()

    @property
    def ending_reserves(self) -> Decimal:
        return self.points[-1].reserves if self.points else ZERO

    @property
    def trough(self) -> MonthPoint | None:
        return min(self.points, key=lambda p: p.reserves) if self.points else None


def project(position: Position, scenario: Scenario, *, today: date) -> Projection:
    """Roll the position forward under the scenario.

    Deliberately simple arithmetic on monthly aggregates rather than a daily
    simulation. A two-year projection built from a daily model would imply a
    precision the inputs cannot support — the monthly income figure is itself an
    average over the last twelve months.
    """
    # Point 0 is today, before anything in the scenario has happened. Point N is
    # the position after living through months 0..N-1.
    #
    # Stated explicitly because the first version branched on `month > 0` and
    # applied one-off costs at both month 0 and month 1 -- a ₹120,000 holiday
    # was deducted twice and the trough came out ₹90,000 too low. An off-by-one
    # in a projection is invisible: the shape of the chart stays plausible.
    reserves = position.liquid_reserves
    shortfalls: list[int] = []
    points: list[MonthPoint] = [
        MonthPoint(
            month=0,
            on=today,
            reserves=reserves.quantize(CENTS, rounding=ROUND_HALF_UP),
            monthly_surplus=position.monthly_surplus.quantize(CENTS, rounding=ROUND_HALF_UP),
        )
    ]

    for month in range(1, scenario.horizon_months + 1):
        lived = month - 1
        surplus = position.monthly_surplus + scenario.monthly_delta_at(lived)
        reserves += surplus
        reserves += sum((c.one_off_in_month(lived) for c in scenario.changes), ZERO)

        if reserves < 0:
            shortfalls.append(month)

        points.append(
            MonthPoint(
                month=month,
                on=_add_months(today, month),
                reserves=reserves.quantize(CENTS, rounding=ROUND_HALF_UP),
                monthly_surplus=surplus.quantize(CENTS, rounding=ROUND_HALF_UP),
            )
        )

    return Projection(points=tuple(points), shortfall_months=tuple(shortfalls))


def _add_months(start: date, months: int) -> date:
    """Calendar-correct month arithmetic.

    `timedelta(days=30 * n)` drifts by five days a year, which over a 24-month
    horizon puts the last point in the wrong month.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    from calendar import monthrange

    return monthrange(year, month)[1]


# --- templates --------------------------------------------------------------
#
# Convenience, not capability. Each is a pre-filled list of the same `Change`
# values a user can assemble by hand, and the engine cannot tell the difference.


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    name: str
    description: str
    #: Fields the user fills in, with sensible defaults.
    inputs: tuple[tuple[str, str, Decimal], ...]
    build: str  # documented in `from_template`


TEMPLATES: tuple[Template, ...] = (
    Template(
        key="holiday",
        name="Take a holiday",
        description="A one-off cost on a chosen date.",
        inputs=(("cost", "Total cost", Decimal("120000")),),
        build="one_off",
    ),
    Template(
        key="vehicle",
        name="Buy a vehicle",
        description="A deposit now, then an EMI for a fixed term.",
        inputs=(
            ("deposit", "Down payment", Decimal("60000")),
            ("monthly", "Monthly EMI", Decimal("12000")),
            ("months", "Term in months", Decimal("36")),
        ),
        build="deposit_and_emi",
    ),
    Template(
        key="job_change",
        name="Change jobs",
        description="A new salary, optionally after a gap with no income.",
        inputs=(
            ("new_income", "New monthly income", Decimal("120000")),
            ("gap_months", "Months without income first", Decimal("1")),
        ),
        build="job_change",
    ),
    Template(
        key="rent_change",
        name="Move home",
        description="A deposit, then a different rent every month.",
        inputs=(
            ("deposit", "Deposit", Decimal("100000")),
            ("rent_change", "Change in monthly rent", Decimal("8000")),
        ),
        build="move",
    ),
    Template(
        key="income_loss",
        name="Lose income",
        description="The stress test: what if the money stopped?",
        inputs=(("months", "Months without income", Decimal("6")),),
        build="income_loss",
    ),
)

BY_KEY: dict[str, Template] = {t.key: t for t in TEMPLATES}


def from_template(
    key: str, values: dict[str, Decimal], *, position: Position, horizon_months: int = 24
) -> Scenario:
    """Build a scenario from a template and the user's numbers."""
    template = BY_KEY.get(key)
    if template is None:
        raise KeyError(key)

    def value(name: str) -> Decimal:
        return values.get(name, next(d for n, _, d in template.inputs if n == name))

    # Annotated up front: without it the first branch fixes the type at
    # `tuple[Change]` and every longer branch is an error.
    changes: tuple[Change, ...]

    if template.build == "one_off":
        changes = (Change(ChangeKind.ONE_OFF, template.name, value("cost")),)
    elif template.build == "deposit_and_emi":
        term = int(value("months"))
        changes = (
            Change(ChangeKind.ONE_OFF, "Down payment", value("deposit")),
            Change(
                ChangeKind.RECURRING_EXPENSE,
                "Monthly EMI",
                value("monthly"),
                lasts_months=term,
            ),
        )
    elif template.build == "job_change":
        gap = int(value("gap_months"))
        delta = value("new_income") - position.monthly_income
        changes = (
            # The gap is modelled as losing the *current* income for N months,
            # then the new salary starting. Two changes rather than one, because
            # a gap and a raise are different facts and the user should see both.
            *(
                (
                    Change(
                        ChangeKind.RECURRING_INCOME,
                        "No income during the gap",
                        position.monthly_income,
                        lasts_months=gap,
                        is_reduction=True,
                    ),
                )
                if gap > 0
                else ()
            ),
            Change(
                ChangeKind.RECURRING_INCOME,
                "New salary" if delta >= 0 else "Lower salary",
                abs(delta),
                starts_in_months=gap,
                is_reduction=delta < 0,
            ),
        )
    elif template.build == "move":
        changes = (
            Change(ChangeKind.ONE_OFF, "Deposit", value("deposit")),
            Change(
                ChangeKind.RECURRING_EXPENSE,
                "Rent change",
                abs(value("rent_change")),
                is_reduction=value("rent_change") < 0,
            ),
        )
    else:  # income_loss
        months = int(value("months"))
        changes = (
            Change(
                ChangeKind.RECURRING_INCOME,
                "Income stops",
                position.monthly_income,
                lasts_months=months,
                is_reduction=True,
            ),
        )

    return Scenario(
        name=template.name,
        changes=changes,
        horizon_months=horizon_months,
        notes=template.description,
    )


def published_templates() -> list[dict[str, object]]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "inputs": [
                {"name": name, "label": label, "default": format(default, "f")}
                for name, label, default in t.inputs
            ],
        }
        for t in TEMPLATES
    ]


def months_until_shortfall(projection: Projection) -> int | None:
    """When the reserves run out, or None if they never do inside the horizon.

    None rather than a large number: "you are fine for the next two years" is
    the answer to most scenarios, and expressing it as `999` would invite the UI
    to render it.
    """
    return projection.shortfall_months[0] if projection.shortfall_months else None
