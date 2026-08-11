"""Analytics aggregation.

Every widget is **one SQL query**. The client never receives raw transactions to
add up: a year of history is thousands of rows, and shipping them to a phone to
compute a total is slow, wasteful, and produces a different answer than the
server would.

This module reads from `finance` through its service interface only (ADR-001) --
except for aggregate SQL, which needs to run in the database rather than in
Python, so the queries live here and are the one place analytics touches the
transactions table directly.
"""

from __future__ import annotations

import uuid
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import ColumnElement, Date, case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_today
from app.modules.finance.models import (
    Account,
    Budget,
    Cadence,
    Category,
    Goal,
    GoalStatus,
    RecurringItem,
    Transaction,
    TransactionKind,
)

#: Account types whose negative balance is money already spent and owed now,
#: rather than a long-term liability. A mortgage does not reduce this month's
#: runway; a credit-card balance does.
REVOLVING_DEBT_TYPES = ("credit_card",)

#: Category slugs that represent debt repayment. Kept here rather than inferred
#: from account type because an EMI paid from a savings account is still debt
#: service, and the category is what the user assigned.
DEBT_SLUGS = ("loan-emi",)

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date

    @classmethod
    def month_of(cls, day: date) -> Period:
        """The calendar month containing `day`, ending no later than today.

        A month in progress ends today, not on the 31st. Reporting a full-month
        window for a month that is five days old invites the comparison in
        `previous()` to be five days against thirty-one.
        """
        first = day.replace(day=1)
        last = date(day.year, day.month, monthrange(day.year, day.month)[1])
        today = utc_today()
        return cls(first, min(last, today) if first <= today <= last else last)

    @classmethod
    def trailing_months(cls, months: int, ending: date | None = None) -> Period:
        end = ending or utc_today()
        start = end.replace(day=1)
        for _ in range(months - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        return cls(start, end)

    @classmethod
    def trailing_days(cls, days: int, ending: date | None = None) -> Period:
        """The last `days` days, inclusive of today.

        The right window for *detection*, where `month_of` is the right window
        for *reporting*. On the 5th of a month, month-to-date is five days, and
        comparing it against five days of the previous month is too noisy to
        raise a finding on -- which would leave the insight feed empty for the
        first week of every month, exactly when someone would look at it.
        """
        end = ending or utc_today()
        return cls(end - timedelta(days=days - 1), end)

    def previous(self) -> Period:
        """The comparable window in the preceding month.

        Same day-of-month range, not the same number of days ending yesterday.
        On the 5th of August this is 1--5 July, so "spending is down 52%" means
        something. Comparing a partial month against a complete one would make
        every dashboard look like a dramatic improvement for the first three
        weeks of every month -- a number that is arithmetically true and
        completely misleading.
        """
        # A fixed-length window shifts back by its own length; only a calendar
        # month needs the day-of-month alignment below.
        if self.start.day != 1:
            span = (self.end - self.start).days + 1
            return Period(self.start - timedelta(days=span), self.end - timedelta(days=span))

        prev_month_end = self.start - timedelta(days=1)
        prev_start = prev_month_end.replace(day=1)
        days_in_prev = monthrange(prev_start.year, prev_start.month)[1]
        prev_end = prev_start.replace(day=min(self.end.day, days_in_prev))
        return Period(prev_start, prev_end)


@dataclass(slots=True)
class CategorySlice:
    category_id: uuid.UUID | None
    name: str
    slug: str
    amount: Decimal
    share_pct: Decimal
    previous_amount: Decimal
    change_pct: Decimal | None


@dataclass(slots=True)
class SeriesPoint:
    period: str
    income: Decimal = ZERO
    expense: Decimal = ZERO
    net: Decimal = ZERO


@dataclass(slots=True)
class Totals:
    income: Decimal = ZERO
    expense: Decimal = ZERO

    @property
    def net(self) -> Decimal:
        return self.income - self.expense

    @property
    def savings_rate(self) -> Decimal | None:
        """Share of income kept.

        None rather than zero when there is no income: a savings rate is
        undefined without a denominator, and reporting 0% would read as "you
        saved nothing" rather than "we cannot say".
        """
        if self.income <= 0:
            return None
        return ((self.income - self.expense) / self.income).quantize(Decimal("0.0001"))


@dataclass(frozen=True, slots=True)
class BudgetOutcome:
    """One budget period, and whether it was kept."""

    category_name: str
    limit: Decimal
    spent: Decimal

    @property
    def kept(self) -> bool:
        return self.spent <= self.limit


@dataclass(frozen=True, slots=True)
class RecurringCandidate:
    """One transaction, reduced to what recurrence detection needs."""

    transaction_id: uuid.UUID
    merchant: str
    occurred_on: date
    amount: Decimal
    kind: str
    category_id: uuid.UUID | None
    account_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class OutlierRow:
    """A transaction large relative to its category's median."""

    transaction_id: uuid.UUID
    merchant: str
    category_name: str
    amount: Decimal
    category_median: Decimal
    occurred_on: date


@dataclass(frozen=True, slots=True)
class GoalRow:
    goal_id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    target_date: date | None
    monthly_surplus: Decimal


@dataclass(frozen=True, slots=True)
class OpenBudget:
    """A budget period still running, and how much of it is used."""

    category_name: str
    category_slug: str
    limit: Decimal
    spent: Decimal
    period_label: str
    days_left: int


@dataclass(frozen=True, slots=True)
class RecurringRow:
    item_id: uuid.UUID
    name: str
    amount: Decimal
    cadence: str
    first_seen_on: date
    is_auto_detected: bool
    kind: str = "expense"
    item_type: str = "other"
    next_due_on: date | None = None


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """How much history exists, which bounds what can honestly be claimed."""

    first_transaction_on: date | None
    last_transaction_on: date | None
    transaction_count: int

    @property
    def observation_days(self) -> int:
        if self.first_transaction_on is None or self.last_transaction_on is None:
            return 0
        return (self.last_transaction_on - self.first_transaction_on).days + 1

    @property
    def months(self) -> Decimal:
        return (Decimal(self.observation_days) / Decimal("30.44")).quantize(Decimal("0.01"))


@dataclass(slots=True)
class Dashboard:
    period: Period
    net_worth: Decimal
    liquid: Decimal
    totals: Totals
    previous_totals: Totals
    top_categories: list[CategorySlice] = field(default_factory=list)
    cashflow: list[SeriesPoint] = field(default_factory=list)
    net_worth_trend: list[tuple[str, Decimal]] = field(default_factory=list)
    account_count: int = 0
    transaction_count: int = 0


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- shared predicates -------------------------------------------------

    @staticmethod
    def _spendable(user_id: uuid.UUID) -> list[ColumnElement[bool]]:
        """Rows that count as real income or spending.

        Excludes soft-deleted rows, anything the user has flagged out of
        analytics, and both legs of a transfer -- moving money between your own
        accounts is not income or expense, and counting it would double every
        saving (FR-2.3).
        """
        return [
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.excluded_from_analytics.is_(False),
            Transaction.transfer_pair_id.is_(None),
        ]

    def _in_period(self, user_id: uuid.UUID, period: Period) -> list[ColumnElement[bool]]:
        return [
            *self._spendable(user_id),
            Transaction.occurred_on >= period.start,
            Transaction.occurred_on <= period.end,
        ]

    # -- totals ------------------------------------------------------------

    async def totals(self, user_id: uuid.UUID, period: Period) -> Totals:
        stmt = (
            select(Transaction.kind, func.coalesce(func.sum(Transaction.amount), ZERO))
            .where(*self._in_period(user_id, period))
            .group_by(Transaction.kind)
        )
        rows: dict[str, Decimal] = {
            row[0]: Decimal(row[1]) for row in (await self.session.execute(stmt)).all()
        }
        return Totals(
            income=rows.get(TransactionKind.INCOME.value, ZERO),
            expense=rows.get(TransactionKind.EXPENSE.value, ZERO),
        )

    # -- category breakdown ------------------------------------------------

    async def categories(
        self, user_id: uuid.UUID, period: Period, *, limit: int | None = None
    ) -> list[CategorySlice]:
        """Expense breakdown with period-over-period change.

        The comparison is what makes the number actionable: "₹12,400 on food" is
        a fact, "₹12,400, up 23%" is something to act on.
        """
        current = await self._spend_by_category(user_id, period)
        previous = await self._spend_by_category(user_id, period.previous())
        total = sum(current.values(), ZERO)

        names = await self._category_names(user_id)
        slices: list[CategorySlice] = []

        for category_id, amount in current.items():
            before = previous.get(category_id, ZERO)
            name, slug = names.get(category_id, ("Uncategorised", "uncategorised"))
            slices.append(
                CategorySlice(
                    category_id=category_id,
                    name=name,
                    slug=slug,
                    amount=amount,
                    share_pct=(amount / total * 100).quantize(Decimal("0.01")) if total else ZERO,
                    previous_amount=before,
                    # None, not 0%, when there is nothing to compare against --
                    # "new this month" and "unchanged" are different facts.
                    change_pct=(
                        ((amount - before) / before * 100).quantize(Decimal("0.01"))
                        if before > 0
                        else None
                    ),
                )
            )

        slices.sort(key=lambda s: s.amount, reverse=True)
        return slices[:limit] if limit else slices

    async def _spend_by_category(
        self, user_id: uuid.UUID, period: Period
    ) -> dict[uuid.UUID | None, Decimal]:
        stmt = (
            select(Transaction.category_id, func.coalesce(func.sum(Transaction.amount), ZERO))
            .where(
                *self._in_period(user_id, period),
                Transaction.kind == TransactionKind.EXPENSE.value,
            )
            .group_by(Transaction.category_id)
        )
        return {row[0]: Decimal(row[1]) for row in (await self.session.execute(stmt)).all()}

    async def _category_names(self, user_id: uuid.UUID) -> dict[uuid.UUID | None, tuple[str, str]]:
        stmt = select(Category.id, Category.name, Category.slug).where(
            (Category.user_id.is_(None)) | (Category.user_id == user_id)
        )
        return {row[0]: (row[1], row[2]) for row in (await self.session.execute(stmt)).all()}

    # -- time series -------------------------------------------------------

    async def cashflow(self, user_id: uuid.UUID, months: int = 6) -> list[SeriesPoint]:
        """Income and expense per month.

        Grouped in SQL with date_trunc rather than bucketed in Python: the
        database already has the rows and an index on (user_id, occurred_on).
        """
        period = Period.trailing_months(months)
        month = func.date_trunc("month", Transaction.occurred_on).label("month")

        stmt = (
            select(
                month,
                func.coalesce(
                    func.sum(
                        case(
                            (Transaction.kind == TransactionKind.INCOME.value, Transaction.amount),
                            else_=0,
                        )
                    ),
                    ZERO,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (Transaction.kind == TransactionKind.EXPENSE.value, Transaction.amount),
                            else_=0,
                        )
                    ),
                    ZERO,
                ),
            )
            .where(*self._in_period(user_id, period))
            .group_by(month)
            .order_by(month)
        )
        rows = (await self.session.execute(stmt)).all()
        observed = {
            row[0].date().strftime("%Y-%m"): SeriesPoint(
                period=row[0].date().strftime("%Y-%m"),
                income=Decimal(row[1]),
                expense=Decimal(row[2]),
                net=Decimal(row[1]) - Decimal(row[2]),
            )
            for row in rows
        }

        # Emit every month in the window, including empty ones. A gap in a time
        # series reads as missing data; a zero reads as a quiet month, which is
        # what it actually was.
        return [observed.get(label, SeriesPoint(period=label)) for label in _month_labels(period)]

    async def net_worth_trend(
        self, user_id: uuid.UUID, months: int = 12
    ) -> list[tuple[str, Decimal]]:
        """Cumulative balance at each month end.

        Includes transfers: they move money between accounts, so they net to
        zero in the total while still being part of the ledger.
        """
        period = Period.trailing_months(months)
        opening = await self._opening_balance_total(user_id)

        month = func.date_trunc("month", Transaction.occurred_on).label("month")
        signed = func.sum(
            case(
                (Transaction.kind == TransactionKind.INCOME.value, Transaction.amount),
                else_=-Transaction.amount,
            )
        )

        stmt = (
            select(month, func.coalesce(signed, ZERO))
            .where(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
                Transaction.occurred_on <= period.end,
            )
            .group_by(month)
            .order_by(month)
        )
        rows = (await self.session.execute(stmt)).all()

        running = opening
        by_month: dict[str, Decimal] = {}
        for row in rows:
            running += Decimal(row[1])
            by_month[row[0].date().strftime("%Y-%m")] = running

        # Carry the last known value forward through months with no activity,
        # so the line stays continuous instead of dropping to zero.
        out: list[tuple[str, Decimal]] = []
        carried = opening
        for label in _month_labels(period):
            carried = by_month.get(label, carried)
            out.append((label, carried))
        return out

    async def _opening_balance_total(self, user_id: uuid.UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(Account.opening_balance), ZERO)).where(
            Account.user_id == user_id, Account.deleted_at.is_(None)
        )
        return Decimal((await self.session.execute(stmt)).scalar_one())

    async def savings_rate_trend(
        self, user_id: uuid.UUID, months: int = 12
    ) -> list[tuple[str, Decimal | None]]:
        """Monthly savings rate.

        None in a month with no income -- the rate is undefined, and plotting
        zero would draw a cliff the user never experienced.
        """
        points = await self.cashflow(user_id, months)
        return [
            (
                p.period,
                ((p.income - p.expense) / p.income).quantize(Decimal("0.0001"))
                if p.income > 0
                else None,
            )
            for p in points
        ]

    # -- balances ----------------------------------------------------------

    async def net_worth(self, user_id: uuid.UUID) -> tuple[Decimal, Decimal]:
        """Total and liquid balances.

        Liquid excludes locked or invested accounts -- the advisor and the
        emergency-fund metric need spendable money, not paper wealth.
        """
        stmt = select(
            func.coalesce(func.sum(Account.current_balance), ZERO),
            func.coalesce(
                func.sum(case((Account.is_liquid.is_(True), Account.current_balance), else_=0)),
                ZERO,
            ),
        ).where(
            Account.user_id == user_id,
            Account.deleted_at.is_(None),
            Account.archived_at.is_(None),
        )
        row = (await self.session.execute(stmt)).one()
        return Decimal(row[0]), Decimal(row[1])

    async def _counts(self, user_id: uuid.UUID) -> tuple[int, int]:
        accounts = await self.session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.user_id == user_id, Account.deleted_at.is_(None))
        )
        transactions = await self.session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
        )
        return int(accounts or 0), int(transactions or 0)

    # -- health inputs -----------------------------------------------------
    #
    # Health scores are a rubric over aggregates, not another SQL consumer.
    # Keeping the queries here means the health module stays pure and testable
    # without a database, and there is one place that knows how a transaction
    # becomes a number.

    async def debt_service(self, user_id: uuid.UUID, period: Period) -> Decimal:
        """Money that left as debt repayment in the window.

        Identified by category rather than account type: an EMI paid from a
        savings account is still debt service, and the category is what the
        user actually assigned. Missing an EMI understates debt-to-income,
        which is the direction that flatters -- so this is deliberately broad.
        """
        stmt = (
            select(func.coalesce(func.sum(Transaction.amount), ZERO))
            .join(Category, Category.id == Transaction.category_id)
            .where(
                *self._in_period(user_id, period),
                Transaction.kind == TransactionKind.EXPENSE.value,
                Category.slug.in_(DEBT_SLUGS),
            )
        )
        return Decimal((await self.session.execute(stmt)).scalar_one())

    async def budget_outcomes(self, user_id: uuid.UUID, months: int = 3) -> list[BudgetOutcome]:
        """Every budget period that has *closed*, with what was actually spent.

        Closed periods only. Judging someone against a monthly budget four days
        into the month would score them as failing every month until the 28th --
        the metric would measure the calendar, not their discipline.

        Restricted to monthly budgets, which is the only period the product
        actually creates today. A weekly or yearly budget would need a different
        close rule, and silently treating one as monthly would be wrong in a way
        nobody would notice.
        """
        start = Period.trailing_months(months).start
        this_month = func.date_trunc("month", literal(utc_today(), Date))
        budget_month = func.date_trunc("month", Budget.period_start)

        spent = (
            select(
                Transaction.category_id.label("category_id"),
                func.date_trunc("month", Transaction.occurred_on).label("month"),
                func.coalesce(func.sum(Transaction.amount), ZERO).label("total"),
            )
            .where(
                *self._spendable(user_id),
                Transaction.kind == TransactionKind.EXPENSE.value,
                Transaction.occurred_on >= start,
            )
            .group_by(Transaction.category_id, "month")
            .subquery()
        )

        stmt = (
            select(Category.name, Budget.amount_limit, func.coalesce(spent.c.total, ZERO))
            .select_from(Budget)
            .join(Category, Category.id == Budget.category_id)
            .outerjoin(
                spent,
                (spent.c.category_id == Budget.category_id) & (budget_month == spent.c.month),
            )
            .where(
                Budget.user_id == user_id,
                Budget.deleted_at.is_(None),
                Budget.period == Cadence.MONTHLY.value,
                Budget.period_start >= start,
                # The month has to be over. `budgets` stores only a start, so
                # the close date is derived rather than read.
                budget_month < this_month,
            )
            .order_by(Budget.period_start)
        )
        return [
            BudgetOutcome(category_name=row[0], limit=Decimal(row[1]), spent=Decimal(row[2]))
            for row in (await self.session.execute(stmt)).all()
        ]

    async def emergency_reserves(self, user_id: uuid.UUID) -> Decimal:
        """Liquid assets *net of revolving debt* -- the real runway.

        `net_worth()[1]` sums liquid accounts alone, which is the right number
        for the dashboard's "how much can I move today". It is the wrong number
        for an emergency fund: someone holding ₹680k in savings against ₹190k on
        a credit card does not have ₹680k of runway, and scoring them as fully
        funded overstates their resilience.

        Overstating resilience is the most flattering error this rubric could
        make, so it is the one worth spending a query to avoid. Floors at zero:
        debt exceeding savings means no reserve, not a negative one.
        """
        stmt = select(
            func.coalesce(
                func.sum(case((Account.is_liquid.is_(True), Account.current_balance), else_=0)),
                ZERO,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            Account.type.in_(REVOLVING_DEBT_TYPES),
                            func.least(Account.current_balance, 0),
                        ),
                        else_=0,
                    )
                ),
                ZERO,
            ),
        ).where(
            Account.user_id == user_id,
            Account.deleted_at.is_(None),
            Account.archived_at.is_(None),
        )
        row = (await self.session.execute(stmt)).one()
        return max(ZERO, Decimal(row[0]) + Decimal(row[1]))

    async def spending_outliers(
        self, user_id: uuid.UUID, period: Period, *, multiple: Decimal = Decimal("4")
    ) -> list[OutlierRow]:
        """Transactions far larger than their own category's median.

        Median rather than mean: a mean is dragged upward by the very outlier
        being looked for, so each large transaction quietly raises the bar that
        would have caught the next one.
        """
        median = (
            select(
                Transaction.category_id.label("category_id"),
                func.percentile_cont(0.5).within_group(Transaction.amount).label("median_amount"),
            )
            .where(
                *self._spendable(user_id),
                Transaction.kind == TransactionKind.EXPENSE.value,
                Transaction.category_id.is_not(None),
            )
            .group_by(Transaction.category_id)
            .subquery()
        )

        stmt = (
            select(
                Transaction.id,
                Transaction.merchant_raw,
                Category.name,
                Transaction.amount,
                median.c.median_amount,
                Transaction.occurred_on,
            )
            .join(median, median.c.category_id == Transaction.category_id)
            .join(Category, Category.id == Transaction.category_id)
            .where(
                *self._in_period(user_id, period),
                Transaction.kind == TransactionKind.EXPENSE.value,
                median.c.median_amount > 0,
                Transaction.amount >= median.c.median_amount * multiple,
            )
            .order_by(Transaction.amount.desc())
            .limit(5)
        )
        return [
            OutlierRow(
                transaction_id=row[0],
                merchant=row[1] or "Unknown",
                category_name=row[2],
                amount=Decimal(row[3]),
                category_median=Decimal(row[4]),
                occurred_on=row[5],
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def subscription_spend(
        self, user_id: uuid.UUID, period: Period
    ) -> tuple[Decimal, Decimal, int]:
        """Subscription spend this period and the comparable prior one.

        Returns `(current, prior, distinct_merchants)`.
        """

        async def total(target: Period) -> tuple[Decimal, int]:
            stmt = (
                select(
                    func.coalesce(func.sum(Transaction.amount), ZERO),
                    func.count(func.distinct(Transaction.merchant_normalized)),
                )
                .join(Category, Category.id == Transaction.category_id)
                .where(
                    *self._in_period(user_id, target),
                    Transaction.kind == TransactionKind.EXPENSE.value,
                    Category.slug == "subscriptions",
                )
            )
            row = (await self.session.execute(stmt)).one()
            return Decimal(row[0]), int(row[1] or 0)

        current, count = await total(period)
        prior, _ = await total(period.previous())
        return current, prior, count

    async def goal_progress(self, user_id: uuid.UUID) -> list[GoalRow]:
        """Active goals, with the monthly surplus available to fund them.

        One surplus figure shared across goals rather than apportioned: the
        product does not ask users to allocate savings per goal, so inventing an
        allocation would make the shortfall arithmetic look more precise than it
        is.
        """
        recent = await self.cashflow(user_id, months=6)
        active = [p for p in recent if p.income > 0 or p.expense > 0]
        surplus = (
            max(ZERO, sum((p.net for p in active), ZERO) / Decimal(len(active))) if active else ZERO
        )

        stmt = (
            select(Goal.id, Goal.name, Goal.target_amount, Goal.current_amount, Goal.target_date)
            .where(
                Goal.user_id == user_id,
                Goal.deleted_at.is_(None),
                Goal.status == GoalStatus.ACTIVE.value,
            )
            .order_by(Goal.target_date)
        )
        return [
            GoalRow(
                goal_id=row[0],
                name=row[1],
                target_amount=Decimal(row[2]),
                current_amount=Decimal(row[3]),
                target_date=row[4],
                monthly_surplus=surplus,
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def recurring_items(self, user_id: uuid.UUID) -> list[RecurringRow]:
        """Active recurring commitments."""
        stmt = (
            select(
                RecurringItem.id,
                RecurringItem.name,
                RecurringItem.amount,
                RecurringItem.cadence,
                RecurringItem.first_seen_on,
                RecurringItem.is_auto_detected,
                RecurringItem.kind,
                RecurringItem.created_at,
                RecurringItem.item_type,
                RecurringItem.next_due_on,
            )
            .where(
                RecurringItem.user_id == user_id,
                RecurringItem.deleted_at.is_(None),
                RecurringItem.is_active.is_(True),
            )
            .order_by(RecurringItem.created_at.desc())
        )
        return [
            RecurringRow(
                item_id=row[0],
                name=row[1],
                amount=Decimal(row[2]),
                cadence=row[3],
                # Falls back to the row's creation date for items that predate
                # the column, which is the best available answer for them.
                first_seen_on=row[4] or row[7].date(),
                is_auto_detected=bool(row[5]),
                kind=row[6],
                item_type=row[8],
                next_due_on=row[9],
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def daily_net_flows(
        self, user_id: uuid.UUID, *, days: int = 730
    ) -> list[tuple[date, Decimal]]:
        """Net flow per day, oldest first, with no gaps.

        Every day between the first and last transaction is emitted, including
        quiet ones. A gap in a time series reads as missing data to a model; a
        zero reads as a day nothing happened, which is what it was. Prophet in
        particular will interpolate across gaps and invent a trend from them.
        """
        cutoff = utc_today() - timedelta(days=days)
        signed = func.sum(
            case(
                (Transaction.kind == TransactionKind.INCOME.value, Transaction.amount),
                else_=-Transaction.amount,
            )
        )

        stmt = (
            select(Transaction.occurred_on, signed)
            .where(*self._spendable(user_id), Transaction.occurred_on >= cutoff)
            .group_by(Transaction.occurred_on)
            .order_by(Transaction.occurred_on)
        )
        observed = {row[0]: Decimal(row[1]) for row in (await self.session.execute(stmt)).all()}
        if not observed:
            return []

        first, last = min(observed), max(observed)
        span = (last - first).days + 1
        return [
            (first + timedelta(days=offset), observed.get(first + timedelta(days=offset), ZERO))
            for offset in range(span)
        ]

    async def recurring_candidates(
        self, user_id: uuid.UUID, *, days: int = 730
    ) -> list[RecurringCandidate]:
        """Transactions that could form a recurring pattern.

        Transfers are excluded by `_spendable`, which matters here: a standing
        transfer into savings is regular and is not a commitment leaving the
        household.
        """
        cutoff = utc_today() - timedelta(days=days)
        stmt = (
            select(
                Transaction.id,
                Transaction.merchant_normalized,
                Transaction.occurred_on,
                Transaction.amount,
                Transaction.kind,
                Transaction.category_id,
                Transaction.account_id,
            )
            .where(
                *self._spendable(user_id),
                Transaction.occurred_on >= cutoff,
                Transaction.merchant_normalized.is_not(None),
            )
            .order_by(Transaction.occurred_on)
        )
        return [
            RecurringCandidate(
                transaction_id=row[0],
                merchant=row[1],
                occurred_on=row[2],
                amount=Decimal(row[3]),
                kind=row[4],
                category_id=row[5],
                account_id=row[6],
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def open_budget_progress(self, user_id: uuid.UUID, today: date) -> list[OpenBudget]:
        """Budgets for the period *currently running*, with spend so far.

        Deliberately distinct from `budget_outcomes`, which returns only closed
        periods. That is right for scoring discipline — judging someone four
        days into a month measures the calendar — and wrong for a notification,
        which is only useful while there is still time to act.
        """
        month_start = today.replace(day=1)
        days_in_month = monthrange(today.year, today.month)[1]
        month_end = today.replace(day=days_in_month)

        spent = (
            select(
                Transaction.category_id.label("category_id"),
                func.coalesce(func.sum(Transaction.amount), ZERO).label("total"),
            )
            .where(
                *self._spendable(user_id),
                Transaction.kind == TransactionKind.EXPENSE.value,
                Transaction.occurred_on >= month_start,
                Transaction.occurred_on <= today,
            )
            .group_by(Transaction.category_id)
            .subquery()
        )

        stmt = (
            select(
                Category.name,
                Category.slug,
                Budget.amount_limit,
                func.coalesce(spent.c.total, ZERO),
            )
            .select_from(Budget)
            .join(Category, Category.id == Budget.category_id)
            .outerjoin(spent, spent.c.category_id == Budget.category_id)
            .where(
                Budget.user_id == user_id,
                Budget.deleted_at.is_(None),
                Budget.period == Cadence.MONTHLY.value,
                func.date_trunc("month", Budget.period_start)
                == func.date_trunc("month", literal(today, Date)),
            )
        )
        return [
            OpenBudget(
                category_name=row[0],
                category_slug=row[1],
                limit=Decimal(row[2]),
                spent=Decimal(row[3]),
                period_label=month_start.strftime("%Y-%m"),
                days_left=(month_end - today).days,
            )
            for row in (await self.session.execute(stmt)).all()
        ]

    async def observation_window(self, user_id: uuid.UUID) -> ObservationWindow:
        """How much history this user actually has.

        Every engine's confidence is bounded by this. A score computed from
        eleven days of data is a guess wearing a number's clothes.
        """
        stmt = select(
            func.min(Transaction.occurred_on),
            func.max(Transaction.occurred_on),
            func.count(),
        ).where(*self._spendable(user_id))
        row = (await self.session.execute(stmt)).one()
        return ObservationWindow(
            first_transaction_on=row[0],
            last_transaction_on=row[1],
            transaction_count=int(row[2] or 0),
        )

    # -- composite ---------------------------------------------------------

    async def dashboard(self, user_id: uuid.UUID, period: Period | None = None) -> Dashboard:
        """Everything the dashboard needs, in one round trip.

        Deliberately composite: six parallel requests on a cold mobile
        connection is six round trips for one screen. The whole result is
        cached under the user's data_version, so a write invalidates it.
        """
        window = period or Period.month_of(utc_today())
        total_balance, liquid = await self.net_worth(user_id)
        accounts, transactions = await self._counts(user_id)

        return Dashboard(
            period=window,
            net_worth=total_balance,
            liquid=liquid,
            totals=await self.totals(user_id, window),
            previous_totals=await self.totals(user_id, window.previous()),
            top_categories=await self.categories(user_id, window, limit=6),
            cashflow=await self.cashflow(user_id, months=6),
            net_worth_trend=await self.net_worth_trend(user_id, months=12),
            account_count=accounts,
            transaction_count=transactions,
        )


def _month_labels(period: Period) -> list[str]:
    labels: list[str] = []
    cursor = period.start.replace(day=1)
    while cursor <= period.end:
        labels.append(cursor.strftime("%Y-%m"))
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return labels


__all__ = [
    "AnalyticsService",
    "CategorySlice",
    "Dashboard",
    "Period",
    "SeriesPoint",
    "Totals",
]
