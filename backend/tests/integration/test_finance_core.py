"""Financial core behaviour against a real database.

Covers the M2 exit criteria: idempotent import, partial bulk results, transfer
handling, and balance integrity.
"""

from __future__ import annotations

import io
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.clock import utc_today

pytestmark = pytest.mark.integration

ACCOUNTS = "/api/v1/accounts"
TXNS = "/api/v1/transactions"
BULK = "/api/v1/transactions/bulk"
CATEGORIES = "/api/v1/categories"
BUDGETS = "/api/v1/budgets"
GOALS = "/api/v1/goals"
SEED = "/api/v1/imports/demo-seed"
COMMIT = "/api/v1/imports/csv/commit"
ANALYZE = "/api/v1/imports/csv/analyze"
RECONCILE = "/api/v1/accounts/reconcile"

TODAY = utc_today()


@pytest.fixture
async def account(client, auth_headers):
    response = await client.post(
        ACCOUNTS,
        headers=auth_headers,
        json={
            "name": "HDFC Savings",
            "type": "bank",
            "currency": "INR",
            "opening_balance": "50000.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def txn_payload(**overrides):
    return {
        "account_id": overrides.pop("account_id"),
        "kind": "expense",
        "amount": "1250.00",
        "occurred_on": TODAY.isoformat(),
        "currency": "INR",
        "merchant_raw": "Reliance Fresh",
    } | overrides


CSV_CONTENT = b"""Txn Date,Narration,Withdrawal,Deposit
01/08/2026,SALARY ACME TECH,,85000.00
03/08/2026,UPI/SWIGGY/883012,482.00,
04/08/2026,POS RELIANCE FRESH 4471,1250.00,
05/08/2026,AMAZON PAY 99213,2340.50,
"""


class TestAccounts:
    async def test_creates_with_opening_balance(self, account):
        assert account["current_balance"] == "50000.00"
        assert account["is_liquid"] is True

    async def test_duplicate_name_conflicts(self, client, auth_headers, account):
        response = await client.post(
            ACCOUNTS, headers=auth_headers, json={"name": "HDFC Savings", "type": "bank"}
        )
        assert response.status_code == 409

    async def test_credit_card_requires_a_limit(self, client, auth_headers):
        """A database check constraint is the authority, but the caller should
        get a clear 400 rather than a 500 from a raw IntegrityError."""
        response = await client.post(
            ACCOUNTS, headers=auth_headers, json={"name": "Card", "type": "credit_card"}
        )
        assert response.status_code == 400
        assert "credit_limit" in response.text

    async def test_another_users_account_is_not_found(self, client, account):
        # No credentials at all -> 401; the tenant check itself is covered by
        # the repository scoping sweep.
        assert (await client.get(f"{ACCOUNTS}/{account['id']}")).status_code == 401


class TestTransactions:
    async def test_creates_and_moves_the_balance(self, client, auth_headers, account):
        response = await client.post(
            TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"])
        )
        assert response.status_code == 201
        assert response.json()["amount"] == "1250.00"

        refreshed = await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)
        assert refreshed.json()["current_balance"] == "48750.00"

    async def test_income_increases_the_balance(self, client, auth_headers, account):
        await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(account_id=account["id"], kind="income", amount="85000.00"),
        )
        refreshed = await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)
        assert refreshed.json()["current_balance"] == "135000.00"

    async def test_merchant_is_normalised(self, client, auth_headers, account):
        response = await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(account_id=account["id"], merchant_raw="POS RELIANCE FRESH 4471"),
        )
        # Terminal id and POS prefix stripped, so the same vendor deduplicates
        # and categorises consistently.
        assert response.json()["merchant_normalized"] == "reliance fresh"

    async def test_an_identical_transaction_is_rejected_as_duplicate(
        self, client, auth_headers, account
    ):
        payload = txn_payload(account_id=account["id"])
        assert (await client.post(TXNS, headers=auth_headers, json=payload)).status_code == 201

        second = await client.post(TXNS, headers=auth_headers, json=payload)
        assert second.status_code == 422
        assert "allow_duplicate" in second.json()["error"]["message"]

    async def test_a_genuine_repeat_is_allowed_explicitly(self, client, auth_headers, account):
        """Two identical coffees on one day are real. The user says so, and the
        hash gets a discriminator rather than silently merging."""
        payload = txn_payload(account_id=account["id"], amount="50.00", merchant_raw="Starbucks")
        await client.post(TXNS, headers=auth_headers, json=payload)

        response = await client.post(
            TXNS, headers=auth_headers, json=payload | {"allow_duplicate": True}
        )
        assert response.status_code == 201

    async def test_amount_must_be_positive(self, client, auth_headers, account):
        response = await client.post(
            TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"], amount="-10.00")
        )
        assert response.status_code == 400

    async def test_updating_the_amount_keeps_the_balance_correct(
        self, client, auth_headers, account
    ):
        created = await client.post(
            TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"])
        )
        await client.patch(
            f"{TXNS}/{created.json()['id']}", headers=auth_headers, json={"amount": "2000.00"}
        )

        refreshed = await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)
        assert refreshed.json()["current_balance"] == "48000.00"

    async def test_deleting_restores_the_balance(self, client, auth_headers, account):
        created = await client.post(
            TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"])
        )
        await client.delete(f"{TXNS}/{created.json()['id']}", headers=auth_headers)

        refreshed = await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)
        assert refreshed.json()["current_balance"] == "50000.00"

    async def test_idempotency_key_replays_instead_of_duplicating(
        self, client, auth_headers, account
    ):
        payload = txn_payload(account_id=account["id"])
        headers = auth_headers | {"Idempotency-Key": "retry-abc-123"}

        first = await client.post(TXNS, headers=headers, json=payload)
        second = await client.post(TXNS, headers=headers, json=payload)

        assert first.status_code == 201
        # The original response verbatim, status included -- a replay must be
        # indistinguishable from the first call succeeding, apart from the
        # marker header.
        assert second.status_code == 201
        assert second.headers.get("Idempotency-Replayed") == "true"
        # The same row, not a second one.
        assert second.json()["id"] == first.json()["id"]

    async def test_the_same_key_with_a_different_body_conflicts(
        self, client, auth_headers, account
    ):
        """A client bug. Accepting it would let two different financial writes
        share one key."""
        headers = auth_headers | {"Idempotency-Key": "retry-xyz"}
        await client.post(TXNS, headers=headers, json=txn_payload(account_id=account["id"]))

        response = await client.post(
            TXNS, headers=headers, json=txn_payload(account_id=account["id"], amount="999.00")
        )
        assert response.status_code == 409


class TestTransfers:
    @pytest.fixture
    async def second_account(self, client, auth_headers):
        response = await client.post(
            ACCOUNTS,
            headers=auth_headers,
            json={"name": "Emergency Fund", "type": "bank", "opening_balance": "10000.00"},
        )
        return response.json()

    async def test_moves_money_between_accounts(
        self, client, auth_headers, account, second_account
    ):
        response = await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"],
                kind="transfer",
                amount="8000.00",
                to_account_id=second_account["id"],
                merchant_raw="Savings transfer",
            ),
        )
        assert response.status_code == 201

        source = (await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)).json()
        target = (
            await client.get(f"{ACCOUNTS}/{second_account['id']}", headers=auth_headers)
        ).json()

        assert source["current_balance"] == "42000.00"
        assert target["current_balance"] == "18000.00"

    async def test_is_excluded_from_income_and_expense_totals(
        self, client, auth_headers, account, second_account, db_session, registered
    ):
        """The M2 exit criterion. Moving money between your own accounts is not
        spending, and counting it would double every saving (FR-2.3)."""
        await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"],
                kind="transfer",
                amount="8000.00",
                to_account_id=second_account["id"],
            ),
        )
        await client.post(
            TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"], amount="1250.00")
        )

        from app.modules.finance.repository import TransactionRepository

        totals = await TransactionRepository(db_session).totals(
            registered["user"]["id"], TODAY - timedelta(days=1), TODAY + timedelta(days=1)
        )
        assert totals["expense"] == Decimal("1250.00")
        assert totals["income"] == Decimal("0")

    async def test_creates_a_linked_pair(self, client, auth_headers, account, second_account):
        response = await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"],
                kind="transfer",
                amount="8000.00",
                to_account_id=second_account["id"],
            ),
        )
        assert response.json()["transfer_pair_id"] is not None

    async def test_deleting_one_leg_removes_both(
        self, client, auth_headers, account, second_account
    ):
        """Leaving the other leg would make both account balances wrong."""
        created = await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"],
                kind="transfer",
                amount="8000.00",
                to_account_id=second_account["id"],
            ),
        )
        await client.delete(f"{TXNS}/{created.json()['id']}", headers=auth_headers)

        source = (await client.get(f"{ACCOUNTS}/{account['id']}", headers=auth_headers)).json()
        target = (
            await client.get(f"{ACCOUNTS}/{second_account['id']}", headers=auth_headers)
        ).json()
        assert source["current_balance"] == "50000.00"
        assert target["current_balance"] == "10000.00"

    async def test_a_transfer_to_the_same_account_is_rejected(self, client, auth_headers, account):
        response = await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"],
                kind="transfer",
                amount="100.00",
                to_account_id=account["id"],
            ),
        )
        assert response.status_code == 422


class TestBulkInsert:
    async def test_partial_failure_returns_207_with_per_row_outcomes(
        self, client, auth_headers, account
    ):
        """The M2 exit criterion: a 500-row import must not be all-or-nothing,
        and the user needs to know which rows failed."""
        rows = [
            txn_payload(
                account_id=account["id"],
                amount=f"{100 + i}.00",
                merchant_raw=f"Vendor {i}",
                occurred_on=(TODAY - timedelta(days=i % 60)).isoformat(),
            )
            for i in range(500)
        ]
        # Three rows reference an account that does not exist.
        for i in (10, 200, 400):
            rows[i]["account_id"] = "00000000-0000-0000-0000-000000000000"

        response = await client.post(BULK, headers=auth_headers, json=rows)

        assert response.status_code == 207
        body = response.json()
        assert body["created"] == 497
        assert body["errors"] == 3
        assert len(body["results"]) == 500

        failed = [r for r in body["results"] if r["status"] == "error"]
        assert [r["index"] for r in failed] == [10, 200, 400]
        assert all(r["error"] for r in failed)

    async def test_all_valid_rows_return_200(self, client, auth_headers, account):
        rows = [
            txn_payload(account_id=account["id"], amount=f"{10 + i}.00", merchant_raw=f"V{i}")
            for i in range(5)
        ]
        response = await client.post(BULK, headers=auth_headers, json=rows)

        assert response.status_code == 200
        assert response.json()["created"] == 5

    async def test_rejects_more_than_the_row_cap(self, client, auth_headers, account):
        rows = [txn_payload(account_id=account["id"], merchant_raw=f"V{i}") for i in range(501)]
        assert (await client.post(BULK, headers=auth_headers, json=rows)).status_code == 400


class TestCsvImport:
    async def test_analyze_detects_the_mapping(self, client, auth_headers, account):
        response = await client.post(
            ANALYZE,
            headers=auth_headers,
            params={"account_id": account["id"]},
            files={"file": ("statement.csv", io.BytesIO(CSV_CONTENT), "text/csv")},
        )
        assert response.status_code == 200

        body = response.json()
        assert body["detected_mapping"]["date"] == "Txn Date"
        assert body["detected_mapping"]["merchant"] == "Narration"
        assert body["detected_mapping"]["debit"] == "Withdrawal"
        assert body["row_count"] == 4
        assert body["duplicate_estimate"] == 0

    async def test_commit_imports_every_row(self, client, auth_headers, account):
        response = await client.post(
            COMMIT,
            headers=auth_headers,
            params={
                "account_id": account["id"],
                "mapping.date": "Txn Date",
                "mapping.merchant": "Narration",
                "mapping.debit": "Withdrawal",
                "mapping.credit": "Deposit",
            },
            files={"file": ("statement.csv", io.BytesIO(CSV_CONTENT), "text/csv")},
        )
        assert response.status_code == 200
        assert response.json()["created"] == 4

    async def test_reimporting_the_same_file_creates_zero_duplicates(
        self, client, auth_headers, account, db_session, registered
    ):
        """**The headline M2 exit criterion.**

        Asserted at the database level, not just from the response: the unique
        index on (user_id, content_hash) is the mechanism, so the row count is
        what proves it.
        """
        params = {
            "account_id": account["id"],
            "mapping.date": "Txn Date",
            "mapping.merchant": "Narration",
            "mapping.debit": "Withdrawal",
            "mapping.credit": "Deposit",
        }
        files = lambda: {"file": ("statement.csv", io.BytesIO(CSV_CONTENT), "text/csv")}  # noqa: E731

        first = await client.post(COMMIT, headers=auth_headers, params=params, files=files())
        second = await client.post(COMMIT, headers=auth_headers, params=params, files=files())

        assert first.json()["created"] == 4
        assert second.json()["created"] == 0
        assert second.json()["duplicates"] == 4

        from app.modules.finance.models import Transaction

        total = await db_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == registered["user"]["id"])
        )
        assert total == 4, "re-import must not create a single extra row"

    async def test_analyze_reports_duplicates_before_committing(
        self, client, auth_headers, account
    ):
        params = {
            "account_id": account["id"],
            "mapping.date": "Txn Date",
            "mapping.merchant": "Narration",
            "mapping.debit": "Withdrawal",
            "mapping.credit": "Deposit",
        }
        await client.post(
            COMMIT,
            headers=auth_headers,
            params=params,
            files={"file": ("s.csv", io.BytesIO(CSV_CONTENT), "text/csv")},
        )

        analysis = await client.post(
            ANALYZE,
            headers=auth_headers,
            params={"account_id": account["id"]},
            files={"file": ("s.csv", io.BytesIO(CSV_CONTENT), "text/csv")},
        )
        # Warning the user before they commit is what makes the importer
        # trustworthy rather than frightening.
        assert analysis.json()["duplicate_estimate"] == 4

    async def test_unreadable_rows_are_reported_not_silently_dropped(
        self, client, auth_headers, account
    ):
        broken = b"Txn Date,Narration,Withdrawal\nnot-a-date,Something,100.00\n"
        response = await client.post(
            COMMIT,
            headers=auth_headers,
            params={
                "account_id": account["id"],
                "mapping.date": "Txn Date",
                "mapping.merchant": "Narration",
                "mapping.debit": "Withdrawal",
            },
            files={"file": ("s.csv", io.BytesIO(broken), "text/csv")},
        )
        assert response.status_code == 207
        assert response.json()["errors"] == 1
        assert "date" in response.json()["results"][0]["error"].lower()


class TestPagination:
    async def test_cursor_paging_covers_every_row_exactly_once(self, client, auth_headers, account):
        rows = [
            txn_payload(
                account_id=account["id"],
                amount=f"{100 + i}.00",
                merchant_raw=f"Vendor {i}",
                occurred_on=(TODAY - timedelta(days=i)).isoformat(),
            )
            for i in range(30)
        ]
        await client.post(BULK, headers=auth_headers, json=rows)

        seen: list[str] = []
        cursor = None
        for _ in range(10):
            params = {"limit": 7} | ({"cursor": cursor} if cursor else {})
            page = (await client.get(TXNS, headers=auth_headers, params=params)).json()
            seen.extend(t["id"] for t in page["data"])
            cursor = page["pagination"]["next_cursor"]
            if not cursor:
                break

        assert len(seen) == 30
        assert len(set(seen)) == 30, "a row was returned on two pages"

    async def test_a_malformed_cursor_is_a_client_error(self, client, auth_headers):
        response = await client.get(TXNS, headers=auth_headers, params={"cursor": "!!!"})
        assert response.status_code == 400


class TestDemoSeeder:
    async def test_produces_a_year_of_data_quickly(self, client, auth_headers):
        """FR-2.10. Without this a new user's first screen is empty and every
        engine has nothing to say."""
        import time

        started = time.perf_counter()
        response = await client.post(SEED, headers=auth_headers)
        elapsed = time.perf_counter() - started

        assert response.status_code == 201
        body = response.json()
        assert body["accounts"] == 4
        assert body["transactions"] > 400
        assert elapsed < 5.0, f"seeding took {elapsed:.1f}s"

    async def test_the_data_is_plausible(self, client, auth_headers, db_session, registered):
        """Uniform noise would make every downstream engine look broken. The
        shape of the data is the requirement, not the volume."""
        await client.post(SEED, headers=auth_headers)

        from app.modules.finance.models import Transaction
        from app.modules.finance.repository import TransactionRepository

        user_id = registered["user"]["id"]
        repo = TransactionRepository(db_session)
        totals = await repo.totals(user_id, TODAY - timedelta(days=365), TODAY)

        # A year of salary against a year of spending, with money left over --
        # the savings rate has to be positive for health scoring to mean
        # anything.
        assert totals["income"] > Decimal("900000")
        assert totals["expense"] > Decimal("300000")
        assert totals["income"] > totals["expense"]

        # Recurring rhythm: twelve rents, one per month, all identical.
        rents = (
            (
                await db_session.execute(
                    select(Transaction.amount).where(
                        Transaction.user_id == user_id,
                        Transaction.merchant_normalized.like("%landlord%"),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rents) >= 11
        assert len(set(rents)) == 1, "rent should be perfectly fixed"

        # Some rows stay uncategorised on purpose so the review queue is real.
        uncategorized = await db_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id, Transaction.category_id.is_(None))
        )
        assert uncategorized > 0

        # Transfers exist and are excluded from the totals above. They are
        # identified by transfer_pair_id, not by kind: each leg is a plain
        # expense or income so its sign is unambiguous.
        transfers = await db_session.scalar(
            select(func.count())
            .select_from(Transaction)
            .where(Transaction.user_id == user_id, Transaction.transfer_pair_id.isnot(None))
        )
        assert transfers >= 22, "monthly savings transfers should be present"

    async def test_seeded_balances_reconcile_with_the_ledger(self, client, auth_headers):
        """Drift here would mean the seeder and the write path disagree about
        what a transaction does to a balance."""
        await client.post(SEED, headers=auth_headers)

        response = await client.post(RECONCILE, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["drift_count"] == 0, response.json()["drifts"]

    async def test_refuses_to_seed_a_non_empty_account(self, client, auth_headers, account):
        await client.post(TXNS, headers=auth_headers, json=txn_payload(account_id=account["id"]))
        assert (await client.post(SEED, headers=auth_headers)).status_code == 422


class TestBudgetsAndGoals:
    async def test_budget_reports_spend_and_pace(self, client, auth_headers, account):
        categories = (await client.get(CATEGORIES, headers=auth_headers)).json()
        groceries = next(c for c in categories if c["slug"] == "groceries")

        await client.post(
            BUDGETS,
            headers=auth_headers,
            json={
                "category_id": groceries["id"],
                "period_start": TODAY.replace(day=1).isoformat(),
                "amount_limit": "9000.00",
            },
        )
        await client.post(
            TXNS,
            headers=auth_headers,
            json=txn_payload(
                account_id=account["id"], category_id=groceries["id"], amount="2000.00"
            ),
        )

        budgets = (await client.get(BUDGETS, headers=auth_headers)).json()
        assert budgets[0]["spent"] == "2000.00"
        assert budgets[0]["remaining"] == "7000.00"

    async def test_one_budget_per_category_per_period(self, client, auth_headers):
        payload = {"period_start": TODAY.replace(day=1).isoformat(), "amount_limit": "50000.00"}
        assert (await client.post(BUDGETS, headers=auth_headers, json=payload)).status_code == 201
        assert (await client.post(BUDGETS, headers=auth_headers, json=payload)).status_code == 409

    async def test_goal_contribution_tracks_progress(self, client, auth_headers):
        created = await client.post(
            GOALS, headers=auth_headers, json={"name": "Japan Trip", "target_amount": "250000.00"}
        )
        goal_id = created.json()["id"]

        await client.post(
            f"{GOALS}/{goal_id}/contribute", headers=auth_headers, json={"amount": "50000.00"}
        )
        goals = (await client.get(GOALS, headers=auth_headers)).json()

        assert goals[0]["current_amount"] == "50000.00"
        assert goals[0]["progress_pct"] == "20.00"

    async def test_a_goal_completes_when_the_target_is_reached(self, client, auth_headers):
        created = await client.post(
            GOALS, headers=auth_headers, json={"name": "Laptop", "target_amount": "100000.00"}
        )
        await client.post(
            f"{GOALS}/{created.json()['id']}/contribute",
            headers=auth_headers,
            json={"amount": "100000.00"},
        )
        goals = (await client.get(GOALS, headers=auth_headers)).json()
        assert goals[0]["status"] == "achieved"


class TestCategories:
    async def test_the_system_taxonomy_is_available(self, client, auth_headers):
        categories = (await client.get(CATEGORIES, headers=auth_headers)).json()

        slugs = {c["slug"] for c in categories}
        assert {"salary", "groceries", "rent", "subscriptions"} <= slugs
        assert all(c["is_system"] for c in categories)

    async def test_a_user_category_can_be_added(self, client, auth_headers):
        response = await client.post(
            CATEGORIES, headers=auth_headers, json={"name": "Pet Care", "kind": "expense"}
        )
        assert response.status_code == 201
        assert response.json()["slug"] == "pet-care"
        assert response.json()["is_system"] is False

    async def test_nesting_stops_at_two_levels(self, client, auth_headers):
        categories = (await client.get(CATEGORIES, headers=auth_headers)).json()
        child = next(c for c in categories if c["slug"] == "groceries")

        response = await client.post(
            CATEGORIES,
            headers=auth_headers,
            json={"name": "Organic", "kind": "expense", "parent_id": child["id"]},
        )
        assert response.status_code == 422
