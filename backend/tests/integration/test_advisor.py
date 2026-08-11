"""The purchase advisor, end to end.

The rubric and the verdicts are settled by the scenario matrix. What is tested
here is what only a live database and a real request can settle: that the
`wait`-needs-a-date rule is enforced by the schema and not merely by code, that
the simulation reconciles with the health and forecast engines called
independently, that concurrent gathers do not corrupt the session, and that
nothing crosses tenants.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy

pytestmark = pytest.mark.integration

ADVISOR = "/api/v1/advisor"


@pytest.fixture
async def seeded(client, auth_headers):
    response = await client.post("/api/v1/imports/demo-seed", headers=auth_headers)
    assert response.status_code == 201, response.text
    return auth_headers


async def _evaluate(client, headers, *, query="Laptop", price="90000", **extra):
    return await client.post(
        f"{ADVISOR}/evaluate",
        headers=headers,
        json={"product_query": query, "price": price, "consider_emi": True, **extra},
    )


class TestSearch:
    async def test_it_finds_catalogue_products(self, client, auth_headers):
        response = await client.get(
            f"{ADVISOR}/products/search", headers=auth_headers, params={"q": "macbook air"}
        )

        assert response.status_code == 200
        names = [o["name"] for o in response.json()]
        assert names
        assert all("MacBook" in n for n in names)

    async def test_an_unknown_query_is_empty_not_an_error(self, client, auth_headers):
        """The client falls back to manual entry. A user who knows the price
        should never be blocked by a catalogue that has not heard of the thing
        they are buying."""
        response = await client.get(
            f"{ADVISOR}/products/search", headers=auth_headers, params={"q": "zqx9 nonexistent"}
        )

        assert response.status_code == 200
        assert response.json() == []

    async def test_it_requires_authentication(self, client):
        assert (
            await client.get(f"{ADVISOR}/products/search", params={"q": "a"})
        ).status_code == 401


class TestEvaluate:
    async def test_the_flagship_response_is_complete(self, client, seeded):
        response = await _evaluate(client, seeded, query="MacBook Air", price="114900")

        assert response.status_code == 200
        body = response.json()
        assert body["verdict"] in {"buy_now", "buy_on_emi", "wait", "not_recommended"}
        assert body["explanation"]["factors"], "no verdict without factors (ADR-002)"
        assert body["simulation"]["before"]["liquid_savings"]
        assert body["simulation"]["after"]["liquid_savings"]
        assert body["emi_options"]
        assert body["explanation"]["caveats"]

    async def test_contributions_reconstruct_the_score_over_the_wire(self, client, seeded):
        body = (await _evaluate(client, seeded, price="114900")).json()

        contributions = sum(Decimal(f["contribution"]) for f in body["explanation"]["factors"])
        weights = sum(Decimal(f["weight"]) for f in body["explanation"]["factors"])

        assert contributions == Decimal(body["affordability_score"])
        assert weights == Decimal("1.00")

    async def test_the_simulation_reconciles_with_the_health_engine(self, client, seeded):
        """M8 exit criterion.

        The advisor's "before" state must be the same numbers the health page
        shows. Two engines quietly disagreeing about a user's position is worse
        than either being wrong, because there is no way to tell which to trust.
        """
        health = (await client.get("/api/v1/health-score", headers=seeded)).json()
        body = (await _evaluate(client, seeded, price="50000")).json()

        assert Decimal(body["simulation"]["before"]["health_score"]) == Decimal(health["score"])

    async def test_the_simulation_reconciles_with_the_forecast_engine(self, client, seeded):
        forecast = (await client.get("/api/v1/forecast?horizon_days=90", headers=seeded)).json()
        body = (await _evaluate(client, seeded, price="50000")).json()

        expected = Decimal(forecast["trough"]["amount"]) - Decimal("50000")
        assert Decimal(body["simulation"]["forecast_trough_after"]) == expected

    async def test_the_after_state_reflects_the_price(self, client, seeded):
        body = (await _evaluate(client, seeded, price="90000")).json()
        before = body["simulation"]["before"]
        after = body["simulation"]["after"]

        assert Decimal(before["liquid_savings"]) - Decimal(after["liquid_savings"]) == Decimal(
            "90000"
        )
        assert Decimal(after["emergency_fund_months"]) < Decimal(before["emergency_fund_months"])

    async def test_a_capped_verdict_names_the_constraint(self, client, seeded):
        """An unexplained downgrade is indistinguishable from a bug."""
        body = (await _evaluate(client, seeded, price="480000")).json()

        assert body["constraints"], "a purchase this large must trip something"
        for constraint in body["constraints"]:
            assert constraint["code"] and constraint["message"]
        assert "hard limit was crossed" in " ".join(body["explanation"]["caveats"])

    async def test_emi_options_price_the_interest(self, client, seeded):
        """M8 exit criterion: total interest against the cash price.

        A monthly instalment is designed to feel small. Showing only that number
        would help the retailer, not the user.
        """
        body = (await _evaluate(client, seeded, price="114900")).json()

        assert body["emi_options"]
        for option in body["emi_options"]:
            assert Decimal(option["total_interest"]) > 0
            assert Decimal(option["total_payable"]) > Decimal("114900")
            assert Decimal(option["interest_share"]) > 0

        by_tenure = {o["tenure_months"]: o for o in body["emi_options"]}
        if 12 in by_tenure and 24 in by_tenure:
            assert Decimal(by_tenure[24]["monthly"]) < Decimal(by_tenure[12]["monthly"])
            assert Decimal(by_tenure[24]["total_interest"]) > Decimal(
                by_tenure[12]["total_interest"]
            )

    async def test_alternatives_are_scored_not_merely_listed(self, client, seeded):
        """ "Here is something cheaper" leaves the user to guess whether the
        cheaper thing is affordable. Each one carries its own verdict."""
        offers = (
            await client.get(
                f"{ADVISOR}/products/search", headers=seeded, params={"q": "macbook pro"}
            )
        ).json()
        assert offers

        body = (
            await _evaluate(
                client,
                seeded,
                query=offers[0]["name"],
                price=offers[0]["price"],
                external_id=offers[0]["external_id"],
            )
        ).json()

        assert body["alternatives"]
        for alternative in body["alternatives"]:
            assert Decimal(alternative["price"]) < Decimal(body["price"])
            assert alternative["verdict_if_chosen"]
            assert Decimal(alternative["affordability_score"]) >= 0

    async def test_a_price_the_catalogue_does_not_know_still_gets_advice(self, client, seeded):
        response = await _evaluate(
            client, seeded, query="Handmade oak desk from a local carpenter", price="62000"
        )

        assert response.status_code == 200
        assert response.json()["explanation"]["factors"]
        # Nothing to compare against, which is a normal outcome.
        assert response.json()["alternatives"] == []

    async def test_an_invalid_price_is_rejected(self, client, seeded):
        assert (await _evaluate(client, seeded, price="0")).status_code == 400
        assert (await _evaluate(client, seeded, price="-500")).status_code == 400

    async def test_concurrent_evaluations_do_not_corrupt_the_session(self, client, seeded):
        """The advisor gathers two engines concurrently.

        An earlier version shared the request's `AsyncSession` across those
        gathers, which is not safe even for reads: the calls interleave on one
        connection and the session's transaction state goes inconsistent,
        surfacing as `IllegalStateChangeError` from an unrelated line. Running
        several evaluations back to back exercises the path that broke.
        """
        import asyncio

        results = await asyncio.gather(
            *(_evaluate(client, seeded, price=str(30000 + i * 10000)) for i in range(4))
        )

        assert all(r.status_code == 200 for r in results)


class TestTheWaitDateRule:
    async def test_a_wait_verdict_always_carries_a_date(self, client, seeded):
        """M8 exit criterion, enforced by the database.

        A `wait` with no answer to "until when" is a refusal wearing advice's
        clothes.
        """
        # Prices chosen inside this persona's WAIT band. Far above it the
        # *score* itself falls below the wait threshold and the honest answer
        # becomes NOT_RECOMMENDED -- constraints can only make a verdict worse,
        # never rescue one.
        seen_wait = False
        for price in ("350000", "400000", "450000"):
            body = (await _evaluate(client, seeded, price=price)).json()
            if body["verdict"] == "wait":
                seen_wait = True
                assert body["affordable_from"] is not None

        assert seen_wait, "no WAIT produced; the assertion above proved nothing"

    async def test_the_database_refuses_a_wait_without_a_date(self, db_session, registered):
        """The check constraint, tested directly.

        Code can be changed by someone who does not know the rule; the schema
        is where a rule this important belongs.
        """
        import uuid

        from app.modules.advisor.models import PurchaseEvaluation

        db_session.add(
            PurchaseEvaluation(
                user_id=uuid.UUID(str(registered["user"]["id"])),
                product_query="Something",
                price=Decimal("1000"),
                currency="INR",
                verdict="wait",
                affordability_score=Decimal("40"),
                confidence=Decimal("0.5"),
                rubric_version="v1",
                affordable_from=None,
                simulation={},
                explanation={},
            )
        )

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await db_session.flush()


class TestHistory:
    async def test_evaluations_are_stored_and_listed(self, client, seeded):
        await _evaluate(client, seeded, query="Phone", price="40000")
        await _evaluate(client, seeded, query="Laptop", price="90000")

        response = await client.get(f"{ADVISOR}/evaluations", headers=seeded)

        assert response.status_code == 200
        rows = response.json()
        assert len(rows) >= 2
        assert {r["product_query"] for r in rows} >= {"Phone", "Laptop"}

    async def test_a_stored_evaluation_reads_back_as_answered(self, client, seeded):
        """Revisiting a decision shows what was said at the time, not what
        today's numbers would say."""
        created = (await _evaluate(client, seeded, query="Camera", price="67990")).json()

        fetched = (
            await client.get(f"{ADVISOR}/evaluations/{created['id']}", headers=seeded)
        ).json()

        assert fetched["verdict"] == created["verdict"]
        assert fetched["affordability_score"] == created["affordability_score"]
        assert fetched["explanation"]["factors"]

    async def test_another_users_evaluation_is_not_found(self, client, seeded, second_user_headers):
        mine = (await _evaluate(client, seeded, price="50000")).json()["id"]

        response = await client.get(f"{ADVISOR}/evaluations/{mine}", headers=second_user_headers)

        assert response.status_code == 404

    async def test_history_does_not_leak_across_tenants(self, client, seeded, second_user_headers):
        await _evaluate(client, seeded, price="50000")

        theirs = await client.get(f"{ADVISOR}/evaluations", headers=second_user_headers)
        assert theirs.json() == []


class TestPublishedRubric:
    async def test_the_rubric_is_inspectable(self, client, auth_headers):
        response = await client.get(f"{ADVISOR}/rubric", headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["total_weight"] == "1.00"
        assert len(body["factors"]) == 7
        # The constraints are the part most likely to surprise a user, so they
        # are the part that most needs publishing.
        assert body["hard_constraints"]
        for constraint in body["hard_constraints"]:
            assert constraint["code"] and constraint["rule"] and constraint["caps_at"]
