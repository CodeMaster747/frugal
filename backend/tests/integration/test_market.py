"""Wishlist, price history, and drop alerts, end to end.

The rubric and the simulator are covered by unit tests. What only a live
database can settle is the alerting *lifecycle* — that a drop fires once, that
the cooling period holds, and that a price which merely stays low does not
re-alert forever.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration

MARKET = "/api/v1/market"


async def _track(client, headers, query="macbook air", **extra):
    offers = (
        await client.get("/api/v1/advisor/products/search", headers=headers, params={"q": query})
    ).json()
    assert offers, f"catalogue has nothing matching {query!r}"
    return await client.post(
        f"{MARKET}/wishlist",
        headers=headers,
        json={"external_id": offers[0]["external_id"], **extra},
    )


class TestWishlist:
    async def test_tracking_a_product_backfills_its_history(self, client, auth_headers):
        """A chart with one point is not a chart. Because the provider's pricing
        is pure, ninety days of history exist the moment something is tracked."""
        response = await _track(client, auth_headers)

        assert response.status_code == 201
        item = response.json()
        assert Decimal(item["price_when_added"]) > 0
        assert item["lowest_recorded"] is not None

        detail = await client.get(f"{MARKET}/products/{item['product_id']}", headers=auth_headers)
        assert detail.status_code == 200
        assert len(detail.json()["history"]) >= 80

    async def test_the_price_shown_matches_the_advisor(self, client, auth_headers):
        """Two features quoting different prices for one product on one day is
        the bug this pins: the advisor priced at the catalogue's nominal seller
        while the market took the best across sellers."""
        offers = (
            await client.get(
                "/api/v1/advisor/products/search",
                headers=auth_headers,
                params={"q": "macbook air"},
            )
        ).json()
        item = (
            await client.post(
                f"{MARKET}/wishlist",
                headers=auth_headers,
                json={"external_id": offers[0]["external_id"]},
            )
        ).json()

        assert Decimal(item["price_when_added"]) == Decimal(offers[0]["price"])

    async def test_tracking_the_same_thing_twice_is_refused(self, client, auth_headers):
        assert (await _track(client, auth_headers)).status_code == 201
        assert (await _track(client, auth_headers)).status_code == 409

    async def test_the_wishlist_lists_what_is_tracked(self, client, auth_headers):
        await _track(client, auth_headers, query="macbook air")
        await _track(client, auth_headers, query="airpods pro")

        rows = (await client.get(f"{MARKET}/wishlist", headers=auth_headers)).json()
        assert len(rows) == 2
        for row in rows:
            assert row["current_price"] is not None
            assert row["change_since_added"] is not None

    async def test_removing_takes_it_off_the_list(self, client, auth_headers):
        item = (await _track(client, auth_headers)).json()

        assert (
            await client.delete(f"{MARKET}/wishlist/{item['id']}", headers=auth_headers)
        ).status_code == 204
        assert (await client.get(f"{MARKET}/wishlist", headers=auth_headers)).json() == []

    async def test_an_unknown_product_is_not_found(self, client, auth_headers):
        response = await client.post(
            f"{MARKET}/wishlist", headers=auth_headers, json={"external_id": "seed:nope:nope"}
        )
        assert response.status_code == 404

    async def test_it_requires_authentication(self, client):
        assert (await client.get(f"{MARKET}/wishlist")).status_code == 401


class TestPriceHistoryAndReliability:
    async def test_the_detail_view_carries_history_offers_and_scores(self, client, auth_headers):
        item = (await _track(client, auth_headers)).json()

        body = (
            await client.get(f"{MARKET}/products/{item['product_id']}", headers=auth_headers)
        ).json()

        assert body["history"]
        assert body["offers"]
        assert body["market_median"] is not None
        assert body["lowest_recorded"] is not None

        for offer in body["offers"]:
            score = offer["reliability"]
            assert score["signals"], "a score with no signals is a number without a reason"
            assert score["caveats"]
            total = sum(Decimal(s["contribution"]) for s in score["signals"])
            assert total == Decimal(score["score"])

    async def test_offers_are_ordered_cheapest_first(self, client, auth_headers):
        item = (await _track(client, auth_headers)).json()
        body = (
            await client.get(f"{MARKET}/products/{item['product_id']}", headers=auth_headers)
        ).json()

        prices = [Decimal(o["price"]) for o in body["offers"]]
        assert prices == sorted(prices)

    async def test_the_reliability_rubric_is_published(self, client, auth_headers):
        """FR-9.2. A score about a named commercial seller has to be explicit
        about the claim it is *not* making."""
        body = (await client.get(f"{MARKET}/reliability/rubric", headers=auth_headers)).json()

        assert body["total_weight"] == "1.00"
        assert len(body["signals"]) == 6
        assert "not a judgement about the seller" in body["what_this_is_not"]


class TestDropAlerts:
    async def test_a_material_drop_fires_an_alert(self, client, auth_headers, db_session):
        """The M9 exit criterion.

        The price is moved by rewriting what the user was last told, which is
        what the detector compares against — simpler and more honest than
        waiting for the simulator to produce a sale.
        """
        from app.modules.market.models import WishlistItem

        item = (await _track(client, auth_headers)).json()

        row = await db_session.get(WishlistItem, item["id"])
        row.price_when_added = Decimal(item["current_price"]) * Decimal("1.4")
        await db_session.commit()

        alerts = (await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json()

        assert len(alerts) == 1
        assert Decimal(alerts[0]["new_price"]) < Decimal(alerts[0]["previous_price"])
        assert Decimal(alerts[0]["drop_fraction"]) > Decimal("0.05")
        assert alerts[0]["seller_name"]

    async def test_a_trivial_movement_does_not_alert(self, client, auth_headers, db_session):
        """₹300 off a ₹90,000 laptop is not news, and a feed that says it is
        teaches people to stop reading."""
        from app.modules.market.models import WishlistItem

        item = (await _track(client, auth_headers)).json()
        row = await db_session.get(WishlistItem, item["id"])
        row.price_when_added = Decimal(item["current_price"]) + Decimal("200")
        await db_session.commit()

        assert (await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json() == []

    async def test_no_alert_is_raised_without_an_actual_fall(self, client, auth_headers):
        """A target already met when the item was added produced an alert
        reading "₹70,283 → ₹70,283, down 0.0%" — true of nothing."""
        item = (await _track(client, auth_headers)).json()

        # Target far above the current price, so it is "met" immediately.
        await client.delete(f"{MARKET}/wishlist/{item['id']}", headers=auth_headers)
        tracked = (
            await _track(client, auth_headers, target_price=str(Decimal(item["current_price"]) * 2))
        ).json()
        assert tracked["target_price"] is not None

        assert (await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json() == []

    async def test_the_cooling_period_stops_a_repeat(self, client, auth_headers, db_session):
        from app.modules.market.models import WishlistItem

        item = (await _track(client, auth_headers)).json()
        row = await db_session.get(WishlistItem, item["id"])
        row.price_when_added = Decimal(item["current_price"]) * Decimal("1.4")
        await db_session.commit()

        first = (await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json()
        second = (await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json()

        assert len(first) == 1
        assert second == [], "the same drop must not be announced twice"

    async def test_a_further_fall_alerts_again_after_cooling(
        self, client, auth_headers, db_session
    ):
        from app.core.clock import utc_now
        from app.modules.market.models import WishlistItem

        item = (await _track(client, auth_headers)).json()
        row = await db_session.get(WishlistItem, item["id"])
        row.price_when_added = Decimal(item["current_price"]) * Decimal("1.4")
        await db_session.commit()

        assert len((await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json()) == 1

        # Cooling elapsed, and the price has fallen further since.
        row = await db_session.get(WishlistItem, item["id"])
        row.last_alerted_at = utc_now() - timedelta(days=30)
        row.last_alerted_price = Decimal(item["current_price"]) * Decimal("1.3")
        await db_session.commit()

        assert len((await client.post(f"{MARKET}/alerts/check", headers=auth_headers)).json()) == 1

    async def test_alerts_can_be_listed_and_read(self, client, auth_headers, db_session):
        from app.modules.market.models import WishlistItem

        item = (await _track(client, auth_headers)).json()
        row = await db_session.get(WishlistItem, item["id"])
        row.price_when_added = Decimal(item["current_price"]) * Decimal("1.4")
        await db_session.commit()
        await client.post(f"{MARKET}/alerts/check", headers=auth_headers)

        listed = (await client.get(f"{MARKET}/alerts", headers=auth_headers)).json()
        assert listed

        assert (
            await client.post(f"{MARKET}/alerts/{listed[0]['id']}/read", headers=auth_headers)
        ).status_code == 204


class TestTenantIsolation:
    async def test_a_wishlist_does_not_leak(self, client, auth_headers, second_user_headers):
        await _track(client, auth_headers)

        assert (await client.get(f"{MARKET}/wishlist", headers=second_user_headers)).json() == []

    async def test_another_users_item_cannot_be_removed(
        self, client, auth_headers, second_user_headers
    ):
        item = (await _track(client, auth_headers)).json()

        response = await client.delete(
            f"{MARKET}/wishlist/{item['id']}", headers=second_user_headers
        )

        assert response.status_code == 404
        assert (await client.get(f"{MARKET}/wishlist", headers=auth_headers)).json()

    async def test_the_catalogue_is_shared_not_scoped(
        self, client, auth_headers, second_user_headers
    ):
        """`products` has no `user_id` on purpose — a MacBook's price is not a
        fact about one person."""
        item = (await _track(client, auth_headers)).json()

        response = await client.get(
            f"{MARKET}/products/{item['product_id']}", headers=second_user_headers
        )

        assert response.status_code == 200
        assert response.json()["history"]
