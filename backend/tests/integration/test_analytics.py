"""Analytics aggregation against a real database.

Covers the M3 exit criteria: aggregates match hand-computed values on a fixture
dataset, and a write invalidates the cache.

The fixture is deliberately small and fully enumerated below, so every expected
number in this file can be checked by hand. A test that asserts the code agrees
with itself proves nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.clock import utc_today

pytestmark = pytest.mark.integration

ACCOUNTS = "/api/v1/accounts"
TXNS = "/api/v1/transactions"
DASHBOARD = "/api/v1/analytics/dashboard"
CATEGORIES = "/api/v1/analytics/categories"
CASHFLOW = "/api/v1/analytics/cashflow"
NET_WORTH = "/api/v1/analytics/net-worth"
SAVINGS = "/api/v1/analytics/savings-rate"

TODAY = utc_today()
THIS_MONTH = TODAY.replace(day=1)


def _prev_month(day: date) -> date:
    return date(day.year - 1, 12, 1) if day.month == 1 else date(day.year, day.month - 1, 1)


PREV_MONTH = _prev_month(THIS_MONTH)

# --- the fixture, stated once ----------------------------------------------
#
# This month:   income 80,000 | groceries 5,000 | food-delivery 3,000
#                             -> expense 8,000, net 72,000, savings rate 0.90
# Last month:   income 60,000 | groceries 4,000
#                             -> expense 4,000
# Plus a 10,000 transfer this month, which must not appear in either total.

THIS_MONTH_INCOME = Decimal("80000")
THIS_MONTH_EXPENSE = Decimal("8000")
PREV_MONTH_GROCERIES = Decimal("4000")


@pytest.fixture
async def accounts(client, auth_headers):
    main = (
        await client.post(
            ACCOUNTS,
            headers=auth_headers,
            json={"name": "Main", "type": "bank", "opening_balance": "10000.00"},
        )
    ).json()
    savings = (
        await client.post(
            ACCOUNTS,
            headers=auth_headers,
            json={"name": "Savings", "type": "bank", "opening_balance": "5000.00"},
        )
    ).json()
    return main, savings


@pytest.fixture
async def seeded(client, auth_headers, accounts):
    """Builds exactly the fixture described above."""
    main, savings = accounts
    cats = {
        c["slug"]: c["id"]
        for c in (await client.get("/api/v1/categories", headers=auth_headers)).json()
    }

    async def txn(**kw):
        payload = {"account_id": main["id"], "currency": "INR"} | kw
        response = await client.post(TXNS, headers=auth_headers, json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    await txn(
        kind="income",
        amount="80000.00",
        occurred_on=THIS_MONTH.isoformat(),
        merchant_raw="Salary",
        category_id=cats["salary"],
    )
    await txn(
        kind="expense",
        amount="5000.00",
        occurred_on=THIS_MONTH.isoformat(),
        merchant_raw="Reliance Fresh",
        category_id=cats["groceries"],
    )
    await txn(
        kind="expense",
        amount="3000.00",
        occurred_on=THIS_MONTH.isoformat(),
        merchant_raw="Swiggy",
        category_id=cats["food-delivery"],
    )

    await txn(
        kind="income",
        amount="60000.00",
        occurred_on=PREV_MONTH.isoformat(),
        merchant_raw="Salary",
        category_id=cats["salary"],
    )
    await txn(
        kind="expense",
        amount="4000.00",
        occurred_on=PREV_MONTH.isoformat(),
        merchant_raw="Reliance Fresh",
        category_id=cats["groceries"],
    )

    # A transfer, which is neither income nor expense.
    await txn(
        kind="transfer",
        amount="10000.00",
        occurred_on=THIS_MONTH.isoformat(),
        merchant_raw="To savings",
        to_account_id=savings["id"],
    )

    return main, savings, cats


class TestTotals:
    async def test_income_and_expense_match_the_fixture(self, client, auth_headers, seeded):
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert Decimal(body["totals"]["income"]) == THIS_MONTH_INCOME
        assert Decimal(body["totals"]["expense"]) == THIS_MONTH_EXPENSE
        assert Decimal(body["totals"]["net"]) == Decimal("72000")

    async def test_transfers_are_excluded_from_both_totals(self, client, auth_headers, seeded):
        """The 10,000 transfer moves money between the user's own accounts.
        Counting it would inflate income and expense by the same amount and
        make the savings rate meaningless."""
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert Decimal(body["totals"]["income"]) == THIS_MONTH_INCOME  # not 90,000
        assert Decimal(body["totals"]["expense"]) == THIS_MONTH_EXPENSE  # not 18,000

    async def test_savings_rate_is_computed_from_the_totals(self, client, auth_headers, seeded):
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()
        # (80000 - 8000) / 80000
        assert Decimal(body["totals"]["savings_rate"]) == Decimal("0.9000")

    async def test_savings_rate_is_null_without_income(self, client, auth_headers, accounts):
        """Undefined, not zero. Reporting 0% would read as 'you saved nothing'
        rather than 'we cannot say'."""
        main, _ = accounts
        await client.post(
            TXNS,
            headers=auth_headers,
            json={
                "account_id": main["id"],
                "kind": "expense",
                "amount": "500.00",
                "occurred_on": TODAY.isoformat(),
                "merchant_raw": "Shop",
            },
        )
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()
        assert body["totals"]["savings_rate"] is None

    async def test_the_previous_period_is_reported_for_comparison(
        self, client, auth_headers, seeded
    ):
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert Decimal(body["previous_totals"]["income"]) == Decimal("60000")
        assert Decimal(body["previous_totals"]["expense"]) == Decimal("4000")


class TestCategories:
    async def test_breakdown_matches_the_fixture(self, client, auth_headers, seeded):
        rows = (await client.get(CATEGORIES, headers=auth_headers)).json()
        by_slug = {r["slug"]: r for r in rows}

        assert Decimal(by_slug["groceries"]["amount"]) == Decimal("5000")
        assert Decimal(by_slug["food-delivery"]["amount"]) == Decimal("3000")
        # 5000 / 8000 and 3000 / 8000
        assert Decimal(by_slug["groceries"]["share_pct"]) == Decimal("62.50")
        assert Decimal(by_slug["food-delivery"]["share_pct"]) == Decimal("37.50")

    async def test_shares_sum_to_one_hundred(self, client, auth_headers, seeded):
        rows = (await client.get(CATEGORIES, headers=auth_headers)).json()
        assert sum(Decimal(r["share_pct"]) for r in rows) == Decimal("100.00")

    async def test_change_against_the_previous_period(self, client, auth_headers, seeded):
        """4,000 last month to 5,000 this month is +25%. The comparison is what
        makes the number actionable."""
        rows = (await client.get(CATEGORIES, headers=auth_headers)).json()
        groceries = next(r for r in rows if r["slug"] == "groceries")

        assert Decimal(groceries["previous_amount"]) == PREV_MONTH_GROCERIES
        assert Decimal(groceries["change_pct"]) == Decimal("25.00")

    async def test_change_is_null_for_a_brand_new_category(self, client, auth_headers, seeded):
        """'New this month' and 'unchanged' are different facts, so a category
        with no prior spend reports null rather than 0%."""
        rows = (await client.get(CATEGORIES, headers=auth_headers)).json()
        food = next(r for r in rows if r["slug"] == "food-delivery")
        assert food["change_pct"] is None

    async def test_sorted_by_amount(self, client, auth_headers, seeded):
        rows = (await client.get(CATEGORIES, headers=auth_headers)).json()
        amounts = [Decimal(r["amount"]) for r in rows]
        assert amounts == sorted(amounts, reverse=True)


class TestSeries:
    async def test_cashflow_has_a_point_for_every_month(self, client, auth_headers, seeded):
        """Including months with no activity. A gap in a time series reads as
        missing data; a zero reads as a quiet month, which is the truth."""
        rows = (await client.get(CASHFLOW, headers=auth_headers, params={"months": 6})).json()

        assert len(rows) == 6
        assert [r["period"] for r in rows] == sorted(r["period"] for r in rows)

    async def test_cashflow_values_match_the_fixture(self, client, auth_headers, seeded):
        rows = (await client.get(CASHFLOW, headers=auth_headers, params={"months": 6})).json()
        current = next(r for r in rows if r["period"] == THIS_MONTH.strftime("%Y-%m"))

        assert Decimal(current["income"]) == THIS_MONTH_INCOME
        assert Decimal(current["expense"]) == THIS_MONTH_EXPENSE
        assert Decimal(current["net"]) == Decimal("72000")

    async def test_net_worth_includes_opening_balances(self, client, auth_headers, seeded):
        """Opening balances are money the user already had; a trend that starts
        at zero would show phantom growth."""
        rows = (await client.get(NET_WORTH, headers=auth_headers, params={"months": 3})).json()
        latest = Decimal(rows[-1]["value"])

        # 15,000 opening + 140,000 income - 12,000 expense. The transfer nets to
        # zero across the two accounts.
        assert latest == Decimal("143000")

    async def test_net_worth_carries_forward_through_quiet_months(
        self, client, auth_headers, seeded
    ):
        rows = (await client.get(NET_WORTH, headers=auth_headers, params={"months": 12})).json()
        values = [Decimal(r["value"]) for r in rows]
        # A month with no activity holds the previous value rather than
        # dropping to zero.
        assert all(v > 0 for v in values)

    async def test_savings_rate_is_null_in_a_month_without_income(
        self, client, auth_headers, seeded
    ):
        rows = (await client.get(SAVINGS, headers=auth_headers, params={"months": 6})).json()
        current = next(r for r in rows if r["period"] == THIS_MONTH.strftime("%Y-%m"))

        assert Decimal(current["value"]) == Decimal("0.9000")
        # Earlier months in the window have no data at all.
        assert any(r["value"] is None for r in rows)


class TestNetWorth:
    async def test_liquid_excludes_illiquid_accounts(self, client, auth_headers):
        """The advisor and the emergency-fund metric need spendable money, not
        paper wealth -- so an investment account is in net worth but not in
        liquid."""
        await client.post(
            ACCOUNTS,
            headers=auth_headers,
            json={"name": "Cash", "type": "bank", "opening_balance": "20000.00"},
        )
        await client.post(
            ACCOUNTS,
            headers=auth_headers,
            json={
                "name": "Locked FD",
                "type": "investment",
                "opening_balance": "100000.00",
                "is_liquid": False,
            },
        )
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert Decimal(body["net_worth"]) == Decimal("120000")
        assert Decimal(body["liquid"]) == Decimal("20000")


class TestCaching:
    async def test_a_write_bumps_the_data_version(self, client, auth_headers, accounts):
        """The M3 exit criterion.

        Version-based invalidation rather than a TTL: a TTL guarantees a window
        where the dashboard disagrees with the ledger the user just changed.
        """
        main, _ = accounts
        before = (await client.get(DASHBOARD, headers=auth_headers)).json()["data_version"]

        await client.post(
            TXNS,
            headers=auth_headers,
            json={
                "account_id": main["id"],
                "kind": "expense",
                "amount": "1500.00",
                "occurred_on": TODAY.isoformat(),
                "merchant_raw": "New Shop",
            },
        )
        after = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert after["data_version"] > before

    async def test_the_dashboard_reflects_a_write_immediately(self, client, auth_headers, accounts):
        main, _ = accounts
        await client.get(DASHBOARD, headers=auth_headers)  # populate the cache

        await client.post(
            TXNS,
            headers=auth_headers,
            json={
                "account_id": main["id"],
                "kind": "expense",
                "amount": "2500.00",
                "occurred_on": TODAY.isoformat(),
                "merchant_raw": "Later Shop",
            },
        )
        body = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert Decimal(body["totals"]["expense"]) == Decimal("2500")

    async def test_repeated_reads_are_stable(self, client, auth_headers, seeded):
        first = (await client.get(DASHBOARD, headers=auth_headers)).json()
        second = (await client.get(DASHBOARD, headers=auth_headers)).json()

        assert first["data_version"] == second["data_version"]
        assert first["totals"] == second["totals"]


class TestEmptyState:
    async def test_a_new_user_gets_zeroes_not_an_error(self, client, auth_headers):
        """An empty dashboard is a legitimate state, not a failure."""
        response = await client.get(DASHBOARD, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert Decimal(body["net_worth"]) == Decimal("0")
        assert body["top_categories"] == []
        assert body["totals"]["savings_rate"] is None
        assert body["transaction_count"] == 0

    async def test_requires_authentication(self, client):
        assert (await client.get(DASHBOARD)).status_code == 401
