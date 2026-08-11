"""Demo data seeder (FR-2.10).

The answer to cold start. Every engine in Frugal -- health, insights,
forecasting, the advisor -- is meaningless on an empty database, so a new user
who cannot produce a year of history in one click has nothing to look at.

**Plausibility is the requirement, not volume.** Uniform random noise would make
every downstream engine look wrong when the engine is fine and the data is not:
a forecaster needs a real salary rhythm to lock onto, budget-discipline scoring
needs months that are sometimes over and sometimes under, and the insight engine
needs genuine month-over-month movement to detect. So the generator models each
stream the way it actually behaves -- fixed, drifting, bursty, or seasonal --
rather than sampling one distribution for everything.

Deterministic under a fixed seed, so tests can assert on the output.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_today
from app.modules.finance.models import (
    Account,
    AccountType,
    Budget,
    Cadence,
    Category,
    Goal,
    RecurringItem,
    RecurringType,
    Transaction,
    TransactionKind,
    TransactionSource,
)
from app.modules.finance.service import normalize_merchant

MONTHS = 12
SEED = 20260804


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class Vendor:
    name: str
    category: str
    low: float
    high: float
    per_month: tuple[int, int]


# Discretionary streams. `per_month` ranges give each category a realistic
# frequency: groceries weekly-ish, food delivery bursty, fuel fortnightly.
#
# Calibrated so the year lands at roughly a 30% savings rate. That number is
# not cosmetic: fixed commitments already consume ~40% of income, and if
# discretionary spend closes the rest the demo user saves nothing -- which
# makes the savings-rate metric, the emergency-fund score, and every advisor
# verdict degenerate. A demo that cannot afford anything demonstrates nothing.
VENDORS = (
    Vendor("Reliance Fresh", "groceries", 700, 2200, (2, 4)),
    Vendor("BigBasket", "groceries", 900, 2400, (1, 2)),
    Vendor("Swiggy", "food-delivery", 180, 620, (3, 7)),
    Vendor("Zomato", "food-delivery", 200, 700, (1, 4)),
    Vendor("Starbucks", "food-delivery", 250, 500, (0, 3)),
    Vendor("Indian Oil", "fuel", 900, 2200, (1, 3)),
    Vendor("Uber", "transport", 120, 520, (2, 6)),
    Vendor("Metro Card Recharge", "transport", 200, 500, (0, 2)),
    Vendor("Amazon", "shopping", 400, 3200, (0, 3)),
    Vendor("Myntra", "shopping", 700, 2600, (0, 2)),
    Vendor("Apollo Pharmacy", "healthcare", 200, 1200, (0, 2)),
    Vendor("PVR Cinemas", "entertainment", 400, 1100, (0, 2)),
)

# Fixed monthly commitments: the rhythm forecasting locks onto.
SUBSCRIPTIONS = (
    ("Netflix", 649.0, 14),
    ("Spotify", 119.0, 18),
    ("Cult.fit Gym", 1500.0, 7),
    ("Airtel Broadband", 999.0, 22),
)

# Festival season in India. Real spending is seasonal, and a forecaster that
# never sees a seasonal bump cannot be shown degrading honestly on <180 days.
SEASONAL_MULTIPLIER = {10: 1.9, 11: 1.5, 12: 1.25, 3: 1.15}


class DemoSeeder:
    """Generates a year of plausible history for one user."""

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, *, seed: int = SEED) -> None:
        self.session = session
        self.user_id = user_id
        # Not cryptographic: this generates demo data, and a fixed seed is
        # the point -- tests assert on the output.
        self.rng = random.Random(seed)  # noqa: S311
        self.today = utc_today()
        # Twelve months *ending with the current one*. Anchoring on
        # today-minus-365-days instead would stop at last month, leaving the
        # current period empty -- so a user who loads demo data would land on a
        # dashboard showing nothing, which is precisely the cold start this
        # exists to solve.
        self.start = _months_ago(self.today.replace(day=1), MONTHS - 1)

    async def run(self) -> dict[str, int]:
        categories = await self._category_map()
        accounts = await self._create_accounts()
        transactions = self._generate(accounts, categories)

        self.session.add_all(transactions)
        await self.session.flush()

        await self._categorize_pending()
        await self._apply_balances(accounts, transactions)
        await self._create_recurring(accounts, categories)
        await self._create_budgets(categories)
        await self._create_goals(accounts)

        return {
            "accounts": len(accounts),
            "transactions": len(transactions),
            "months": MONTHS,
        }

    async def _categorize_pending(self) -> None:
        """Let the categoriser label the rows `_generate` left blank.

        Runs after the flush because the categoriser reads the taxonomy through
        finance's service, and a demo account's categories have to exist first.
        """
        from app.modules.categorization.service import CategorizationService

        if not getattr(self, "_needs_categorizing", None):
            return

        categorizer = CategorizationService(self.session)
        for txn in self._needs_categorizing:
            suggestion = await categorizer.suggest(self.user_id, txn.merchant_normalized)
            if suggestion is None:
                continue
            txn.category_id = suggestion.category_id
            txn.category_confidence = suggestion.confidence
            txn.categorizer_version = suggestion.version[:32]
            txn.is_reviewed = False
        await self.session.flush()

    # -- reference data ----------------------------------------------------

    async def _category_map(self) -> dict[str, Category]:
        from sqlalchemy import or_, select

        rows = (
            (
                await self.session.execute(
                    select(Category).where(
                        or_(Category.user_id.is_(None), Category.user_id == self.user_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        return {c.slug: c for c in rows}

    async def _create_accounts(self) -> dict[str, Account]:
        specs = [
            ("HDFC Savings", AccountType.BANK, 48_000.0, True, None),
            ("Cash Wallet", AccountType.CASH, 3_500.0, True, None),
            ("HDFC Credit Card", AccountType.CREDIT_CARD, 0.0, False, 200_000.0),
            ("Emergency Fund", AccountType.BANK, 120_000.0, True, None),
        ]
        accounts: dict[str, Account] = {}

        for name, kind, opening, liquid, limit in specs:
            account = Account(
                user_id=self.user_id,
                name=name,
                type=kind.value,
                currency="INR",
                opening_balance=_money(opening),
                current_balance=_money(opening),
                is_liquid=liquid,
                credit_limit=_money(limit) if limit else None,
                institution="HDFC Bank" if "HDFC" in name else None,
            )
            self.session.add(account)
            accounts[name] = account

        await self.session.flush()
        return accounts

    # -- transaction generation -------------------------------------------

    def _generate(
        self, accounts: dict[str, Account], categories: dict[str, Category]
    ) -> list[Transaction]:
        rows: list[Transaction] = []
        bank = accounts["HDFC Savings"]
        card = accounts["HDFC Credit Card"]
        cash = accounts["Cash Wallet"]
        savings = accounts["Emergency Fund"]

        salary = 85_000.0
        cursor = self.start

        for month_index in range(MONTHS):
            year, month = cursor.year, cursor.month
            season = SEASONAL_MULTIPLIER.get(month, 1.0)

            # Salary: fixed day, tiny variance, with a raise partway through --
            # a flat income line is not what real cash flow looks like, and the
            # growth metric needs something to detect.
            if month_index == 7:
                salary *= 1.09
            rows.append(
                self._txn(
                    bank,
                    TransactionKind.INCOME,
                    _money(salary + self.rng.uniform(-250, 250)),
                    date(year, month, 1),
                    "Acme Technologies Salary",
                    categories.get("salary"),
                )
            )

            # Rent: fixed, unvarying. Variance ~0 is what lets the forecaster
            # treat it as certain.
            rows.append(
                self._txn(
                    bank,
                    TransactionKind.EXPENSE,
                    _money(18_000),
                    date(year, month, 3),
                    "Landlord Rent Transfer",
                    categories.get("rent"),
                )
            )

            # EMI: fixed obligation, feeds the debt-to-income metric.
            rows.append(
                self._txn(
                    bank,
                    TransactionKind.EXPENSE,
                    _money(12_400),
                    date(year, month, 5),
                    "HDFC Auto Loan EMI",
                    categories.get("loan-emi"),
                )
            )

            for name, amount, day in SUBSCRIPTIONS:
                rows.append(
                    self._txn(
                        card,
                        TransactionKind.EXPENSE,
                        _money(amount),
                        date(year, month, min(day, 28)),
                        name,
                        categories.get("subscriptions"),
                    )
                )

            # Utilities: genuinely variable, and higher in summer.
            summer = 1.6 if month in (4, 5, 6) else 1.0
            rows.append(
                self._txn(
                    bank,
                    TransactionKind.EXPENSE,
                    _money(self.rng.uniform(900, 1800) * summer),
                    date(year, month, 12),
                    "BESCOM Electricity",
                    categories.get("utilities"),
                )
            )

            # Monthly transfer into the emergency fund, as a linked pair.
            rows.extend(self._transfer(bank, savings, _money(8_000), date(year, month, 2)))

            # Discretionary spend.
            for vendor in VENDORS:
                low, high = vendor.per_month
                count = self.rng.randint(low, high)
                if vendor.category == "shopping":
                    count = round(count * season)

                for _ in range(count):
                    day = self.rng.randint(1, 28)
                    amount = self.rng.uniform(vendor.low, vendor.high)
                    if vendor.category in {"shopping", "food-delivery"}:
                        amount *= season

                    account = card if vendor.category in {"shopping", "food-delivery"} else bank
                    if vendor.name in {"Metro Card Recharge", "Starbucks"}:
                        account = cash

                    rows.append(
                        self._txn(
                            account,
                            TransactionKind.EXPENSE,
                            _money(amount),
                            date(year, month, day),
                            vendor.name,
                            categories.get(vendor.category),
                        )
                    )

            # One unusual purchase every few months, so the anomaly detector
            # and the advisor have something real to reason about.
            if month_index % 4 == 2:
                rows.append(
                    self._txn(
                        card,
                        TransactionKind.EXPENSE,
                        _money(self.rng.uniform(9_000, 22_000)),
                        date(year, month, self.rng.randint(8, 24)),
                        self.rng.choice(["Croma Electronics", "IKEA", "Decathlon"]),
                        categories.get("shopping"),
                    )
                )

            cursor = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)

        # A handful are handed to the categoriser instead of being labelled
        # here, so a fresh demo account shows M5 doing its job: some rows come
        # back as suggestions awaiting review, and whatever the categoriser
        # cannot place stays honestly empty. Both states are worth seeing on day
        # one -- the review queue and the "what is this?" case are different
        # problems with different fixes.
        self._needs_categorizing = [
            txn
            for txn in self.rng.sample(rows, k=min(12, len(rows)))
            if txn.kind == TransactionKind.EXPENSE.value
        ]
        for txn in self._needs_categorizing:
            txn.category_id = None
            txn.category_confidence = None
            txn.is_reviewed = False

        return [r for r in rows if r.occurred_on <= self.today]

    def _txn(
        self,
        account: Account,
        kind: TransactionKind,
        amount: Decimal,
        occurred_on: date,
        merchant: str,
        category: Category | None,
    ) -> Transaction:
        normalized = normalize_merchant(merchant)
        return Transaction(
            # Assigned here rather than left to the column default, which only
            # fires at INSERT. Transfer legs are linked to each other before
            # the flush, so they need identity at construction -- which is the
            # reason UUID keys are generated application-side (ADR / data model).
            id=uuid.uuid4(),
            user_id=self.user_id,
            account_id=account.id,
            category_id=category.id if category else None,
            kind=kind.value,
            amount=amount,
            currency="INR",
            occurred_on=occurred_on,
            merchant_raw=merchant,
            merchant_normalized=normalized,
            source=TransactionSource.DEMO_SEED.value,
            is_reviewed=True,
            content_hash=Transaction.compute_hash(
                self.user_id,
                occurred_on,
                amount,
                normalized,
                account.id,
                # Seeded rows are generated, not observed, so identical
                # amount/merchant/day combinations are expected. A random
                # discriminator keeps them from colliding on the unique index.
                discriminator=uuid.uuid4().hex[:12],
            ),
        )

    def _transfer(
        self, source: Account, destination: Account, amount: Decimal, on: date
    ) -> list[Transaction]:
        # Expense out, income in, linked by transfer_pair_id -- the same shape
        # FinanceService produces, so the seeder cannot drift from the write path.
        out = self._txn(
            source, TransactionKind.EXPENSE, amount, on, "Transfer to Emergency Fund", None
        )
        into = self._txn(
            destination, TransactionKind.INCOME, amount, on, "Transfer from HDFC Savings", None
        )
        out.transfer_pair_id = into.id
        into.transfer_pair_id = out.id
        return [out, into]

    # -- derived state -----------------------------------------------------

    async def _apply_balances(
        self, accounts: dict[str, Account], transactions: list[Transaction]
    ) -> None:
        """Set materialised balances from what was actually generated."""
        by_id = {a.id: a for a in accounts.values()}
        deltas: dict[uuid.UUID, Decimal] = {a.id: Decimal("0") for a in accounts.values()}

        for txn in transactions:
            signed = (
                Decimal(txn.amount)
                if txn.kind == TransactionKind.INCOME.value
                else -Decimal(txn.amount)
            )
            deltas[txn.account_id] += signed

        for account_id, delta in deltas.items():
            account = by_id[account_id]
            account.current_balance = Decimal(account.opening_balance) + delta

    async def _create_recurring(
        self, accounts: dict[str, Account], categories: dict[str, Category]
    ) -> None:
        bank = accounts["HDFC Savings"]
        card = accounts["HDFC Credit Card"]
        next_month = _next_month(self.today)

        items = [
            ("Salary", "income", RecurringType.SALARY, 85_000, 1, bank, "salary", 0.02),
            ("Rent", "expense", RecurringType.RENT, 18_000, 3, bank, "rent", 0.0),
            ("Auto Loan EMI", "expense", RecurringType.EMI, 12_400, 5, bank, "loan-emi", 0.0),
            ("Electricity", "expense", RecurringType.UTILITY, 1_400, 12, bank, "utilities", 0.31),
        ]
        for name, kind, item_type, amount, day, account, slug, variance in items:
            self.session.add(
                RecurringItem(
                    user_id=self.user_id,
                    account_id=account.id,
                    category_id=categories[slug].id if slug in categories else None,
                    name=name,
                    kind=kind,
                    item_type=item_type.value,
                    amount=_money(amount),
                    currency="INR",
                    cadence=Cadence.MONTHLY.value,
                    next_due_on=next_month.replace(day=min(day, 28)),
                    amount_variance=Decimal(str(variance)),
                    is_auto_detected=False,
                )
            )

        for name, sub_amount, day in SUBSCRIPTIONS:
            self.session.add(
                RecurringItem(
                    user_id=self.user_id,
                    account_id=card.id,
                    category_id=categories["subscriptions"].id
                    if "subscriptions" in categories
                    else None,
                    name=name,
                    kind="expense",
                    item_type=RecurringType.SUBSCRIPTION.value,
                    amount=_money(sub_amount),
                    currency="INR",
                    cadence=Cadence.MONTHLY.value,
                    next_due_on=next_month.replace(day=min(day, 28)),
                    amount_variance=Decimal("0"),
                    is_auto_detected=False,
                )
            )

    async def _create_budgets(self, categories: dict[str, Category]) -> None:
        """Three months of budgets, deliberately not all met.

        Budget discipline is one of the six health sub-metrics; a user who has
        never exceeded a budget produces a degenerate score.
        """
        limits = {
            "groceries": 9_000,
            "food-delivery": 6_000,
            "transport": 5_000,
            "shopping": 8_000,
            "subscriptions": 3_500,
        }
        period = self.today.replace(day=1)

        for offset in range(3):
            start = _months_ago(period, offset)
            for slug, limit in limits.items():
                if slug not in categories:
                    continue
                self.session.add(
                    Budget(
                        user_id=self.user_id,
                        category_id=categories[slug].id,
                        period_start=start,
                        amount_limit=_money(limit),
                        currency="INR",
                    )
                )

    async def _create_goals(self, accounts: dict[str, Account]) -> None:
        self.session.add_all(
            [
                Goal(
                    user_id=self.user_id,
                    name="Emergency Fund",
                    target_amount=_money(360_000),
                    current_amount=Decimal(accounts["Emergency Fund"].current_balance),
                    currency="INR",
                    linked_account_id=accounts["Emergency Fund"].id,
                    priority=1,
                    target_date=self.today + timedelta(days=540),
                ),
                Goal(
                    user_id=self.user_id,
                    name="Japan Trip",
                    target_amount=_money(250_000),
                    current_amount=_money(42_000),
                    currency="INR",
                    priority=3,
                    target_date=self.today + timedelta(days=400),
                ),
            ]
        )


def _next_month(value: date) -> date:
    return date(value.year + 1, 1, 1) if value.month == 12 else date(value.year, value.month + 1, 1)


def _months_ago(value: date, months: int) -> date:
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)
