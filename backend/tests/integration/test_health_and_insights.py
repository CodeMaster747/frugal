"""Health and insights, end to end.

The rubric arithmetic and the detector thresholds are covered by the unit tests.
What is tested here is what only a live database can settle: that the dedup
constraint actually holds under a repeated run, that dismissal survives, that
snapshots accumulate one per day, and that none of it leaks across tenants.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration

HEALTH = "/api/v1/health-score"
INSIGHTS = "/api/v1/insights"


@pytest.fixture
async def seeded(client, auth_headers):
    """A user with twelve months of demo history."""
    response = await client.post("/api/v1/imports/demo-seed", headers=auth_headers)
    assert response.status_code == 201, response.text
    return auth_headers


class TestHealthScore:
    async def test_it_returns_a_score_with_a_full_decomposition(self, client, seeded):
        response = await client.get(HEALTH, headers=seeded)

        assert response.status_code == 200
        body = response.json()
        assert body["score"] is not None
        assert body["risk_level"] in {"low", "moderate", "elevated", "high"}
        assert body["rubric_version"]
        assert len(body["explanation"]["factors"]) == 6

    async def test_contributions_reconstruct_the_score_over_the_wire(self, client, seeded):
        """The invariant has to survive serialisation, not just hold in Python.

        Amounts are strings on the wire (ADR-003); a float round-trip here would
        break the reconciliation in exactly the way that is hardest to notice.
        """
        body = (await client.get(HEALTH, headers=seeded)).json()

        contributions = sum(Decimal(f["contribution"]) for f in body["explanation"]["factors"])
        weights = sum(Decimal(f["weight"]) for f in body["explanation"]["factors"])

        assert contributions == Decimal(body["score"])
        assert weights == Decimal("1.0000")

    async def test_a_brand_new_user_gets_no_score_rather_than_a_bad_one(self, client, auth_headers):
        """M6 exit criterion: never a fabricated number."""
        response = await client.get(HEALTH, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["score"] is None
        assert body["risk_level"] is None
        assert body["explanation"]["factors"] == []
        assert body["explanation"]["caveats"], "an absent score must say why"

    async def test_the_rubric_is_published(self, client, auth_headers):
        """A user who disagrees with their score can read what produced it."""
        response = await client.get(f"{HEALTH}/rubric", headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total_weight"] == "1.00"
        assert len(body["metrics"]) == 6
        assert all(m["bands"] for m in body["metrics"])

    async def test_reading_the_score_records_a_snapshot(self, client, seeded):
        await client.get(HEALTH, headers=seeded)

        history = await client.get(f"{HEALTH}/history", headers=seeded)
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert history.json()[0]["rubric_version"]

    async def test_reading_twice_in_a_day_does_not_double_the_trend(self, client, seeded):
        """A user who opens the app six times should not get six data points."""
        for _ in range(3):
            await client.get(HEALTH, headers=seeded)

        history = await client.get(f"{HEALTH}/history", headers=seeded)
        assert len(history.json()) == 1

    async def test_it_requires_authentication(self, client):
        assert (await client.get(HEALTH)).status_code == 401
        assert (await client.get(f"{HEALTH}/rubric")).status_code == 401


class TestInsightGeneration:
    async def test_refresh_produces_findings(self, client, seeded):
        response = await client.post(f"{INSIGHTS}/refresh", headers=seeded)

        assert response.status_code == 200
        assert response.json()["created"] > 0

    async def test_running_twice_creates_no_duplicates(self, client, seeded):
        """M6 exit criterion.

        The unique constraint on (user, dedup_key, period_start) is what makes
        this true by construction rather than by every detector remembering what
        it already said.
        """
        first = await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        second = await client.post(f"{INSIGHTS}/refresh", headers=seeded)

        assert first.json()["created"] > 0
        assert second.json()["created"] == 0

        feed = await client.get(INSIGHTS, headers=seeded)
        titles = [i["title"] for i in feed.json()["data"]]
        assert len(titles) == len(set(titles)), "the same finding appeared twice"

    async def test_the_feed_is_ranked_by_materiality(self, client, seeded):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)

        rows = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        materiality = [Decimal(i["materiality"]) for i in rows]

        assert materiality == sorted(materiality, reverse=True)

    async def test_every_insight_carries_a_populated_explanation(self, client, seeded):
        """ADR-002 over the wire: no finding reaches a user unexplained."""
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)

        for insight in (await client.get(INSIGHTS, headers=seeded)).json()["data"]:
            explanation = insight["explanation"]
            assert explanation["verdict"]
            assert explanation["factors"], f"{insight['title']} has no factors"
            for factor in explanation["factors"]:
                assert factor["name"] and factor["value"] and factor["explanation"]

    async def test_the_feed_is_capped(self, client, seeded):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)

        rows = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        assert len(rows) <= 8, "a wall of findings is a feed nobody reads"


class TestInsightLifecycle:
    async def test_marking_read_clears_it_from_the_unread_count(self, client, seeded):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        feed = (await client.get(INSIGHTS, headers=seeded)).json()
        before = feed["unread_count"]
        target = feed["data"][0]["id"]

        assert (await client.post(f"{INSIGHTS}/{target}/read", headers=seeded)).status_code == 204

        after = (await client.get(INSIGHTS, headers=seeded)).json()
        assert after["unread_count"] == before - 1
        # Still in the feed: read is not dismissed.
        assert any(i["id"] == target for i in after["data"])

    async def test_dismissing_removes_it_from_the_feed(self, client, seeded):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        target = (await client.get(INSIGHTS, headers=seeded)).json()["data"][0]["id"]

        assert (
            await client.post(f"{INSIGHTS}/{target}/dismiss", headers=seeded)
        ).status_code == 204

        rows = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        assert all(i["id"] != target for i in rows)

    async def test_a_dismissed_finding_does_not_come_back_on_the_next_run(self, client, seeded):
        """The cooling period. Without it, "not interested" would mean "not
        interested until the period rolls over"."""
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        feed = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        target = feed[0]
        await client.post(f"{INSIGHTS}/{target['id']}/dismiss", headers=seeded)

        again = await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        assert again.json()["suppressed"] >= 1

        rows = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        assert all(i["title"] != target["title"] for i in rows)

    async def test_marking_read_twice_is_not_an_error(self, client, seeded):
        """The client may retry; idempotence is cheaper than a special case."""
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        target = (await client.get(INSIGHTS, headers=seeded)).json()["data"][0]["id"]

        assert (await client.post(f"{INSIGHTS}/{target}/read", headers=seeded)).status_code == 204
        assert (await client.post(f"{INSIGHTS}/{target}/read", headers=seeded)).status_code == 204

    async def test_unread_filter_works(self, client, seeded):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        target = (await client.get(INSIGHTS, headers=seeded)).json()["data"][0]["id"]
        await client.post(f"{INSIGHTS}/{target}/read", headers=seeded)

        unread = (await client.get(INSIGHTS, headers=seeded, params={"unread": "true"})).json()[
            "data"
        ]
        assert all(i["id"] != target for i in unread)


class TestTenantIsolation:
    async def test_another_users_insight_cannot_be_dismissed(
        self, client, seeded, second_user_headers
    ):
        await client.post(f"{INSIGHTS}/refresh", headers=seeded)
        mine = (await client.get(INSIGHTS, headers=seeded)).json()["data"][0]["id"]

        response = await client.post(f"{INSIGHTS}/{mine}/dismiss", headers=second_user_headers)

        assert response.status_code == 404
        # And it is still mine, undismissed.
        rows = (await client.get(INSIGHTS, headers=seeded)).json()["data"]
        assert any(i["id"] == mine for i in rows)

    async def test_one_users_history_is_not_anothers(self, client, seeded, second_user_headers):
        await client.get(HEALTH, headers=seeded)

        theirs = await client.get(f"{HEALTH}/history", headers=second_user_headers)
        assert theirs.json() == []
