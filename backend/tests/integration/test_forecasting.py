"""Forecasting, end to end.

The tiers and the detector are covered by unit tests and the backtest. What is
tested here is what only a live database and a real request can settle: that the
503 has a body worth reading, that the cache respects `data_version`, that a
scenario never becomes the user's stored forecast, and that none of it crosses
tenants.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration

FORECAST = "/api/v1/forecast"


@pytest.fixture
async def seeded(client, auth_headers):
    response = await client.post("/api/v1/imports/demo-seed", headers=auth_headers)
    assert response.status_code == 201, response.text
    return auth_headers


class TestInsufficientData:
    async def test_a_new_user_gets_503_with_a_reason(self, client, auth_headers):
        """M7 exit criterion: under 14 days, decline rather than invent.

        The body matters as much as the status. A bare 503 leaves the client to
        invent an explanation, and the one it invents will be worse than ours.
        """
        response = await client.get(FORECAST, headers=auth_headers)

        assert response.status_code == 503
        body = response.json()["error"]
        assert body["code"] == "INSUFFICIENT_DATA"
        assert body["caveats"], "declining is an answer and needs a reason"
        assert "14 days" in " ".join(body["caveats"])

    async def test_no_series_is_fabricated(self, client, auth_headers):
        assert "series" not in response_json(await client.get(FORECAST, headers=auth_headers))

    async def test_shortfalls_declines_the_same_way(self, client, auth_headers):
        response = await client.get(f"{FORECAST}/shortfalls", headers=auth_headers)
        assert response.status_code == 503


def response_json(response):
    return response.json()


class TestForecast:
    async def test_it_returns_a_full_projection(self, client, seeded):
        response = await client.get(f"{FORECAST}?horizon_days=90", headers=seeded)

        assert response.status_code == 200
        body = response.json()
        assert len(body["series"]) == 90
        assert body["method"] in {"recurring_projection", "ewma_seasonal", "prophet"}
        assert body["observation_days"] > 0
        assert body["trough"] is not None

    async def test_the_response_always_names_method_window_and_confidence(self, client, seeded):
        """M7 exit criterion. A chart cannot tell the user which model drew it,
        so the response has to."""
        body = (await client.get(FORECAST, headers=seeded)).json()

        assert body["method"]
        assert Decimal(body["confidence"]) > 0
        assert body["explanation"]["data_window"]["observation_days"] > 0
        assert body["explanation"]["caveats"], "every tier states its limits"

    async def test_the_band_is_ordered_over_the_wire(self, client, seeded):
        """Decimals are strings on the wire (ADR-003); a float round-trip would
        break the ordering in a way that is invisible until it renders."""
        body = (await client.get(FORECAST, headers=seeded)).json()

        for point in body["series"]:
            assert Decimal(point["p10"]) <= Decimal(point["p50"]) <= Decimal(point["p90"])

    async def test_the_horizon_is_honoured(self, client, seeded):
        for horizon in (30, 60, 180):
            body = (await client.get(f"{FORECAST}?horizon_days={horizon}", headers=seeded)).json()
            assert len(body["series"]) == horizon

    async def test_an_absurd_horizon_is_rejected(self, client, seeded):
        # 400 VALIDATION_ERROR is this API's shape for a malformed request;
        # 422 is reserved for a well-formed request that breaks a business rule.
        assert (
            await client.get(f"{FORECAST}?horizon_days=5000", headers=seeded)
        ).status_code == 400
        assert (await client.get(f"{FORECAST}?horizon_days=1", headers=seeded)).status_code == 400

    async def test_the_projection_does_not_double_count_commitments(self, client, seeded):
        """The bug the backtest caught.

        The tiers lay scheduled commitments over a statistical baseline. If that
        baseline still contains salary and rent, both are counted twice and the
        balance climbs at roughly double the true savings rate — plausible on a
        chart, badly wrong in fact.

        The seeded persona saves around a third of a ~₹95k income, so three
        months should add well under ₹150,000. Double-counting produced ₹268,000.
        """
        dashboard = (await client.get("/api/v1/analytics/dashboard", headers=seeded)).json()
        body = (await client.get(f"{FORECAST}?horizon_days=90", headers=seeded)).json()

        growth = Decimal(body["projected_balance_end"]["amount"]) - Decimal(dashboard["net_worth"])
        assert growth < Decimal("150000"), (
            f"90 days added {growth}, which is more than this persona earns — "
            "recurring commitments are being counted twice"
        )

    async def test_it_requires_authentication(self, client):
        assert (await client.get(FORECAST)).status_code == 401


class TestCaching:
    async def test_a_repeat_read_is_served_from_cache(self, client, seeded):
        first = (await client.get(FORECAST, headers=seeded)).json()
        second = (await client.get(FORECAST, headers=seeded)).json()

        assert first["series"] == second["series"]
        assert first["explanation"]["computed_at"] == second["explanation"]["computed_at"]

    async def test_a_write_invalidates_the_forecast(self, client, seeded):
        """A projection built on superseded data is worse than a slow one."""
        before = (await client.get(FORECAST, headers=seeded)).json()

        accounts = (await client.get("/api/v1/accounts", headers=seeded)).json()
        created = await client.post(
            "/api/v1/transactions",
            headers=seeded,
            json={
                "account_id": accounts[0]["id"],
                "kind": "expense",
                "amount": "42000.00",
                "currency": "INR",
                "merchant_raw": "SUDDEN LARGE PURCHASE",
                "occurred_on": date.today().isoformat(),
            },
        )
        assert created.status_code == 201

        after = (await client.get(FORECAST, headers=seeded)).json()
        assert after["explanation"]["computed_at"] != before["explanation"]["computed_at"]


class TestScenario:
    async def test_a_hypothetical_moves_the_projection(self, client, seeded):
        base = (await client.get(f"{FORECAST}?horizon_days=90", headers=seeded)).json()

        response = await client.post(
            f"{FORECAST}/scenario",
            headers=seeded,
            json={
                "horizon_days": 90,
                "events": [
                    {
                        "on": (date.today() + timedelta(days=20)).isoformat(),
                        "amount": "-150000",
                        "label": "Laptop",
                    }
                ],
            },
        )

        assert response.status_code == 200
        scenario = response.json()
        assert Decimal(scenario["projected_balance_end"]["amount"]) == Decimal(
            base["projected_balance_end"]["amount"]
        ) - Decimal("150000")

    async def test_a_scenario_never_becomes_the_stored_forecast(self, client, seeded):
        """A what-if is a question, not the user's forecast. Caching it would
        make "what is my forecast" answer with someone's musing about a car."""
        base = (await client.get(FORECAST, headers=seeded)).json()

        await client.post(
            f"{FORECAST}/scenario",
            headers=seeded,
            json={
                "horizon_days": 90,
                "events": [
                    {"on": (date.today() + timedelta(days=5)).isoformat(), "amount": "-400000"}
                ],
            },
        )

        after = (await client.get(FORECAST, headers=seeded)).json()
        assert after["projected_balance_end"] == base["projected_balance_end"]


class TestRecurringDetection:
    async def test_it_finds_the_obvious_commitments(self, client, seeded):
        response = await client.get(f"{FORECAST}/recurring", headers=seeded)

        assert response.status_code == 200
        rows = response.json()
        assert rows, "twelve months of salary and rent should be detectable"

        merchants = " ".join(r["merchant"] for r in rows)
        assert "salary" in merchants
        assert "rent" in merchants

    async def test_every_pattern_reports_its_own_uncertainty(self, client, seeded):
        for pattern in (await client.get(f"{FORECAST}/recurring", headers=seeded)).json():
            assert Decimal(pattern["confidence"]) >= Decimal("0.5")
            assert Decimal(pattern["amount_variance"]) >= 0
            assert pattern["cadence"]
            assert date.fromisoformat(pattern["next_due_on"]) >= date.today()

    async def test_it_is_ranked_by_what_costs_most(self, client, seeded):
        rows = (await client.get(f"{FORECAST}/recurring", headers=seeded)).json()
        monthly = [Decimal(r["monthly_equivalent"]) for r in rows]

        assert monthly == sorted(monthly, reverse=True)


class TestTenantIsolation:
    async def test_one_users_forecast_is_not_anothers(self, client, seeded, second_user_headers):
        mine = await client.get(FORECAST, headers=seeded)
        theirs = await client.get(FORECAST, headers=second_user_headers)

        assert mine.status_code == 200
        # No data of their own, so no forecast — not a copy of mine.
        assert theirs.status_code == 503

    async def test_recurring_patterns_do_not_leak(self, client, seeded, second_user_headers):
        theirs = await client.get(f"{FORECAST}/recurring", headers=second_user_headers)
        assert theirs.json() == []
