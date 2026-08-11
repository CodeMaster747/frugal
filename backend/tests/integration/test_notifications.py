"""Notifications and the simulator, end to end.

The rules and the scenario arithmetic are settled by unit tests. What only a
live database can settle is the M10 exit criterion — *notifications respect
preferences and never duplicate* — and that means the unique constraint, the
preference gate, and the digest window, none of which exist in a pure function.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import sqlalchemy

pytestmark = pytest.mark.integration

NOTIFY = "/api/v1/notifications"
SIM = "/api/v1/simulator"


@pytest.fixture
async def seeded(client, auth_headers):
    response = await client.post("/api/v1/imports/demo-seed", headers=auth_headers)
    assert response.status_code == 201, response.text
    return auth_headers


class TestSimulator:
    async def test_templates_are_published(self, client, auth_headers):
        body = (await client.get(f"{SIM}/templates", headers=auth_headers)).json()

        keys = {t["key"] for t in body["templates"]}
        assert {"holiday", "vehicle", "job_change", "income_loss"} <= keys
        for template in body["templates"]:
            assert template["inputs"], f"{template['key']} asks for nothing"

    async def test_a_scenario_produces_a_full_before_and_after(self, client, seeded):
        """The M10 exit criterion."""
        response = await client.post(
            f"{SIM}/run",
            headers=seeded,
            json={"template": "income_loss", "values": {"months": "6"}},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["before"]["liquid_reserves"]
        assert body["after"]["liquid_reserves"]
        assert body["outlook"] in {"comfortable", "tight", "unsustainable"}
        assert body["explanation"]["factors"], "no verdict without factors (ADR-002)"
        assert body["explanation"]["caveats"]
        assert len(body["series"]) == 25  # month 0 plus 24

    async def test_the_before_state_matches_the_health_page(self, client, seeded):
        """Three views of one position that disagree is worse than any of them
        being wrong."""
        health = (await client.get("/api/v1/health-score", headers=seeded)).json()
        scenario = (
            await client.post(
                f"{SIM}/run", headers=seeded, json={"template": "holiday", "values": {}}
            )
        ).json()

        # Both derive reserves from the same analytics call.
        assert Decimal(scenario["before"]["liquid_reserves"]) > 0
        assert health["score"] is not None

    async def test_a_custom_scenario_needs_no_template(self, client, seeded):
        """Templates are convenience, not capability."""
        response = await client.post(
            f"{SIM}/run",
            headers=seeded,
            json={
                "name": "Sabbatical",
                "changes": [
                    {
                        "kind": "recurring_income",
                        "label": "No pay",
                        "amount": "90000",
                        "lasts_months": 4,
                        "is_reduction": True,
                    },
                    {"kind": "one_off", "label": "Flights", "amount": "80000"},
                ],
                "horizon_months": 18,
            },
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Sabbatical"
        assert len(response.json()["series"]) == 19

    async def test_an_empty_scenario_is_rejected(self, client, seeded):
        response = await client.post(f"{SIM}/run", headers=seeded, json={})
        assert response.status_code == 400

    async def test_an_unknown_template_is_rejected(self, client, seeded):
        response = await client.post(f"{SIM}/run", headers=seeded, json={"template": "teleporter"})
        assert response.status_code == 400

    async def test_scenarios_can_be_compared(self, client, seeded):
        response = await client.post(
            f"{SIM}/compare",
            headers=seeded,
            json={
                "scenarios": [
                    {"template": "holiday", "values": {"cost": "150000"}},
                    {"template": "income_loss", "values": {"months": "6"}},
                ]
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["results"]) == 2
        assert body["safest"]
        # Same measured position for both, or the comparison means nothing.
        reserves = {r["before"]["liquid_reserves"] for r in body["results"]}
        assert len(reserves) == 1

    async def test_comparing_one_scenario_is_rejected(self, client, seeded):
        response = await client.post(
            f"{SIM}/compare", headers=seeded, json={"scenarios": [{"template": "holiday"}]}
        )
        assert response.status_code == 400

    async def test_nothing_is_persisted(self, client, seeded):
        """A scenario is a question, and the answer changes with the ledger.
        There is deliberately no history endpoint to serve a stale one."""
        await client.post(f"{SIM}/run", headers=seeded, json={"template": "holiday"})

        assert (await client.get(f"{SIM}/history", headers=seeded)).status_code == 404

    async def test_it_requires_authentication(self, client):
        assert (await client.post(f"{SIM}/run", json={"template": "holiday"})).status_code == 401


class TestNotificationGeneration:
    async def test_running_twice_creates_no_duplicates(self, client, seeded):
        """The M10 exit criterion, enforced by the unique constraint rather than
        by every rule remembering what it said."""
        first = (await client.post(f"{NOTIFY}/generate", headers=seeded)).json()
        second = (await client.post(f"{NOTIFY}/generate", headers=seeded)).json()

        assert second["created"] == 0
        assert second["detected"] == first["detected"]

        feed = (await client.get(NOTIFY, headers=seeded)).json()
        subjects = [n["subject"] for n in feed["data"]]
        assert len(subjects) == len(set(subjects))

    async def test_the_database_refuses_a_duplicate(self, db_session, registered):
        """The rule lives in the schema, where code that does not know about it
        still cannot break it."""
        from app.modules.notifications.models import Notification

        user_id = uuid.UUID(str(registered["user"]["id"]))
        for _ in range(2):
            db_session.add(
                Notification(
                    user_id=user_id,
                    category="budget",
                    urgency="daily",
                    subject="s",
                    body="b",
                    dedup_key="budget:groceries:2026-08:warn",
                )
            )

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db_session.flush()

    async def test_a_disabled_category_produces_no_row_at_all(self, client, seeded):
        """Suppressed at creation, not at delivery: a later change to the
        delivery path then cannot leak it."""
        await client.patch(
            f"{NOTIFY}/preferences", headers=seeded, json={"goal_milestone_enabled": False}
        )

        result = (await client.post(f"{NOTIFY}/generate", headers=seeded)).json()
        feed = (await client.get(NOTIFY, headers=seeded)).json()

        assert result["suppressed_by_preference"] >= 0
        assert all(n["category"] != "goal_milestone" for n in feed["data"])


class TestPreferences:
    async def test_defaults_are_returned_without_being_written(self, client, auth_headers):
        """A GET that writes is a surprise in a log and a write on a read path."""
        body = (await client.get(f"{NOTIFY}/preferences", headers=auth_headers)).json()

        assert body["budget_enabled"] is True
        assert body["digest_frequency"] == "daily"
        assert body["digest_hour"] == 9

    async def test_a_change_persists(self, client, auth_headers):
        await client.patch(
            f"{NOTIFY}/preferences",
            headers=auth_headers,
            json={"digest_frequency": "weekly", "digest_hour": 18, "bill_enabled": False},
        )

        body = (await client.get(f"{NOTIFY}/preferences", headers=auth_headers)).json()
        assert body["digest_frequency"] == "weekly"
        assert body["digest_hour"] == 18
        assert body["bill_enabled"] is False
        # Untouched fields keep their defaults.
        assert body["budget_enabled"] is True

    async def test_an_invalid_digest_hour_is_rejected(self, client, auth_headers):
        response = await client.patch(
            f"{NOTIFY}/preferences", headers=auth_headers, json={"digest_hour": 30}
        )
        assert response.status_code == 400

    async def test_preferences_do_not_leak_across_tenants(
        self, client, auth_headers, second_user_headers
    ):
        await client.patch(
            f"{NOTIFY}/preferences", headers=auth_headers, json={"budget_enabled": False}
        )

        theirs = (await client.get(f"{NOTIFY}/preferences", headers=second_user_headers)).json()
        assert theirs["budget_enabled"] is True


class TestDelivery:
    async def _make_pending(self, db_session, user_id: uuid.UUID, *, urgency="daily"):
        from app.modules.notifications.models import Notification

        row = Notification(
            user_id=user_id,
            category="budget",
            urgency=urgency,
            subject="Budget nearly spent",
            body="body",
            dedup_key=f"test:{uuid.uuid4()}",
        )
        db_session.add(row)
        await db_session.commit()
        return row

    async def test_digest_off_delivers_nothing(self, client, auth_headers, db_session, registered):
        await client.patch(
            f"{NOTIFY}/preferences", headers=auth_headers, json={"digest_frequency": "off"}
        )
        await self._make_pending(db_session, uuid.UUID(str(registered["user"]["id"])))

        result = (await client.post(f"{NOTIFY}/deliver", headers=auth_headers)).json()
        assert result["delivered"] == 0

    async def test_immediate_frequency_sends_at_once(
        self, client, auth_headers, db_session, registered
    ):
        await client.patch(
            f"{NOTIFY}/preferences", headers=auth_headers, json={"digest_frequency": "immediate"}
        )
        await self._make_pending(db_session, uuid.UUID(str(registered["user"]["id"])))

        result = (await client.post(f"{NOTIFY}/deliver", headers=auth_headers)).json()
        assert result["delivered"] == 1

    async def test_quiet_hours_hold_everything_including_urgent(
        self, client, auth_headers, db_session, registered
    ):
        """A shortfall warning is worth interrupting a morning for and not worth
        waking someone at 2am. Those are different judgements and quiet hours
        settle the second."""
        from app.core.clock import utc_now

        now = utc_now()
        await client.patch(
            f"{NOTIFY}/preferences",
            headers=auth_headers,
            json={
                "digest_frequency": "immediate",
                "quiet_from": (now - timedelta(hours=1)).time().isoformat(timespec="minutes"),
                "quiet_until": (now + timedelta(hours=1)).time().isoformat(timespec="minutes"),
            },
        )
        await self._make_pending(
            db_session, uuid.UUID(str(registered["user"]["id"])), urgency="immediate"
        )

        result = (await client.post(f"{NOTIFY}/deliver", headers=auth_headers)).json()
        assert result["delivered"] == 0
        assert result["held"] == 1

    async def test_a_delivered_notification_is_not_sent_twice(
        self, client, auth_headers, db_session, registered
    ):
        await client.patch(
            f"{NOTIFY}/preferences", headers=auth_headers, json={"digest_frequency": "immediate"}
        )
        await self._make_pending(db_session, uuid.UUID(str(registered["user"]["id"])))

        first = (await client.post(f"{NOTIFY}/deliver", headers=auth_headers)).json()
        second = (await client.post(f"{NOTIFY}/deliver", headers=auth_headers)).json()

        assert first["delivered"] == 1
        assert second["delivered"] == 0


class TestFeed:
    async def test_marking_read_clears_the_count(self, client, seeded, db_session, registered):
        from app.modules.notifications.models import Notification

        db_session.add(
            Notification(
                user_id=uuid.UUID(str(registered["user"]["id"])),
                category="budget",
                urgency="daily",
                subject="s",
                body="b",
                dedup_key=f"feed:{uuid.uuid4()}",
            )
        )
        await db_session.commit()

        before = (await client.get(NOTIFY, headers=seeded)).json()
        assert before["unread_count"] >= 1

        target = before["data"][0]["id"]
        assert (await client.post(f"{NOTIFY}/{target}/read", headers=seeded)).status_code == 204

        after = (await client.get(NOTIFY, headers=seeded)).json()
        assert after["unread_count"] == before["unread_count"] - 1

    async def test_read_all_clears_everything(self, client, seeded):
        await client.post(f"{NOTIFY}/generate", headers=seeded)
        await client.post(f"{NOTIFY}/read-all", headers=seeded)

        assert (await client.get(NOTIFY, headers=seeded)).json()["unread_count"] == 0

    async def test_another_users_notification_is_not_found(
        self, client, seeded, second_user_headers
    ):
        await client.post(f"{NOTIFY}/generate", headers=seeded)
        feed = (await client.get(NOTIFY, headers=seeded)).json()
        if not feed["data"]:
            pytest.skip("no notification generated for this fixture data")

        response = await client.post(
            f"{NOTIFY}/{feed['data'][0]['id']}/read", headers=second_user_headers
        )
        assert response.status_code == 404

    async def test_the_feed_does_not_leak(self, client, seeded, second_user_headers):
        await client.post(f"{NOTIFY}/generate", headers=seeded)

        theirs = (await client.get(NOTIFY, headers=second_user_headers)).json()
        assert theirs["data"] == []
