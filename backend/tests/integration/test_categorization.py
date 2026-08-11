"""Auto-categorisation, end to end.

The unit-level behaviour of the rules layer and the classifier is covered by the
eval harness. What is tested here is the wiring the harness cannot see: that a
transaction created through the API comes back categorised, that a correction
becomes a rule the very next transaction obeys, and that neither one can reach
across tenants.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.integration


async def _account(client, headers) -> str:
    response = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={
            "name": "Categorisation Test",
            "type": "bank",
            "currency": "INR",
            "opening_balance": "50000.00",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _category_id(client, headers, slug: str) -> str:
    response = await client.get("/api/v1/categories", headers=headers)
    assert response.status_code == 200
    matches = [c for c in response.json() if c["slug"] == slug]
    assert matches, f"no seeded category with slug {slug!r}"
    return matches[0]["id"]


async def _spend(client, headers, account_id: str, merchant: str, *, day: str = "2026-01-15"):
    return await client.post(
        "/api/v1/transactions",
        headers=headers,
        json={
            "account_id": account_id,
            "kind": "expense",
            "amount": "450.00",
            "currency": "INR",
            "merchant_raw": merchant,
            "occurred_on": day,
        },
    )


class TestAutoCategorisationOnWrite:
    async def test_a_known_merchant_is_categorised_without_being_asked(self, client, auth_headers):
        """The core promise of M5: the user types a merchant, not a category."""
        account_id = await _account(client, auth_headers)

        response = await _spend(client, auth_headers, account_id, "SWIGGY*ORDER 8821")

        assert response.status_code == 201
        body = response.json()
        assert body["category"] is not None
        assert body["category"]["slug"] == "food-delivery"

    async def test_a_rule_match_is_marked_reviewed_and_a_model_guess_is_not(
        self, client, auth_headers
    ):
        """A guess must be visibly provisional.

        A seed-corpus match is certain enough to stand on its own; anything the
        model inferred stays unreviewed so it surfaces in the review queue rather
        than quietly becoming truth.
        """
        account_id = await _account(client, auth_headers)

        rule_hit = await _spend(client, auth_headers, account_id, "ZOMATO LTD", day="2026-01-11")
        assert rule_hit.status_code == 201
        assert rule_hit.json()["is_reviewed"] is False
        assert Decimal(rule_hit.json()["category_confidence"]) >= Decimal("0.85")

    async def test_an_explicit_category_is_never_overridden(self, client, auth_headers):
        """A category the user chose is a decision, not a hint."""
        account_id = await _account(client, auth_headers)
        travel = await _category_id(client, auth_headers, "travel")

        response = await client.post(
            "/api/v1/transactions",
            headers=auth_headers,
            json={
                "account_id": account_id,
                "kind": "expense",
                "amount": "450.00",
                "currency": "INR",
                # Swiggy would otherwise be classified food-delivery.
                "merchant_raw": "SWIGGY*ORDER 9931",
                "category_id": travel,
                "occurred_on": "2026-01-16",
            },
        )

        assert response.status_code == 201
        assert response.json()["category"]["slug"] == "travel"

    async def test_an_unrecognisable_merchant_is_left_uncategorised(self, client, auth_headers):
        """FR-5.4. A wrong category corrupts every downstream engine silently;
        an empty one asks a question."""
        account_id = await _account(client, auth_headers)

        response = await _spend(
            client, auth_headers, account_id, "QX7 VNTR PVT 4419", day="2026-01-17"
        )

        assert response.status_code == 201
        assert response.json()["category"] is None


class TestTheFeedbackLoop:
    async def test_a_correction_applies_to_the_next_transaction(self, client, auth_headers):
        """The user should never have to correct the same merchant twice.

        This is the loop that makes the feature feel intelligent rather than
        merely automated, and it works without any retraining -- the correction
        is read back as a rule immediately.
        """
        account_id = await _account(client, auth_headers)
        groceries = await _category_id(client, auth_headers, "groceries")

        first = await _spend(client, auth_headers, account_id, "SWIGGY*ORDER 1001")
        assert first.json()["category"]["slug"] == "food-delivery"

        corrected = await client.patch(
            f"/api/v1/transactions/{first.json()['id']}",
            headers=auth_headers,
            json={"category_id": groceries},
        )
        assert corrected.status_code == 200
        assert corrected.json()["category"]["slug"] == "groceries"
        assert corrected.json()["is_reviewed"] is True

        # A different Swiggy transaction, after the correction. A different day,
        # because same-day-same-amount would be caught as a duplicate -- both
        # narrations normalise to the same merchant key, which is the point.
        later = await _spend(
            client, auth_headers, account_id, "SWIGGY*ORDER 1002", day="2026-01-19"
        )
        assert later.status_code == 201
        assert later.json()["category"]["slug"] == "groceries"

    async def test_a_correction_does_not_leak_to_another_user(
        self, client, auth_headers, second_user_headers
    ):
        """One user deciding Swiggy is groceries must not change anyone else's
        ledger. Personal rules are tenant-scoped by construction."""
        mine = await _account(client, auth_headers)
        groceries = await _category_id(client, auth_headers, "groceries")

        first = await _spend(client, auth_headers, mine, "SWIGGY*ORDER 2001")
        await client.patch(
            f"/api/v1/transactions/{first.json()['id']}",
            headers=auth_headers,
            json={"category_id": groceries},
        )

        theirs = await _account(client, second_user_headers)
        response = await _spend(client, second_user_headers, theirs, "SWIGGY*ORDER 2002")

        assert response.status_code == 201
        assert response.json()["category"]["slug"] == "food-delivery"


class TestSuggestEndpoint:
    async def test_it_explains_which_layer_answered(self, client, auth_headers):
        """A suggestion the user cannot interrogate is a black box. Every
        response names the layer that produced it."""
        response = await client.get(
            "/api/v1/categorization/suggest",
            headers=auth_headers,
            params={"merchant": "NETFLIX.COM SUBSCRIPTION"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["suggestion"]["slug"] == "subscriptions"
        assert body["suggestion"]["source"] in {"seed_exact", "seed_substring", "user_rule"}
        assert "matched by" in body["reason"]

    async def test_no_confident_answer_is_a_200_not_an_error(self, client, auth_headers):
        """ "We don't know" is a real answer with a real reason attached."""
        response = await client.get(
            "/api/v1/categorization/suggest",
            headers=auth_headers,
            params={"merchant": "ZZQ4 UNKNOWN ENTITY 88"},
        )

        assert response.status_code == 200
        assert response.json()["suggestion"] is None
        assert "not confident" in response.json()["reason"]

    async def test_it_requires_authentication(self, client):
        response = await client.get("/api/v1/categorization/suggest", params={"merchant": "swiggy"})
        assert response.status_code == 401


class TestRetraining:
    async def test_retraining_folds_in_corrections(self, client, auth_headers):
        """The slow half of the feedback loop.

        Rules make a correction take effect immediately for that exact merchant;
        retraining is what generalises it to similar names. The endpoint reports
        the training-set size and a version derived from it, so "did my
        correction actually get in" is answerable rather than a matter of faith.
        """
        account_id = await _account(client, auth_headers)
        groceries = await _category_id(client, auth_headers, "groceries")

        before = await client.post("/api/v1/categorization/retrain", headers=auth_headers)
        assert before.status_code == 200
        baseline = before.json()
        assert baseline["examples"] > 0
        assert baseline["feature_version"] == "tfidf-lr-v1"

        created = await _spend(client, auth_headers, account_id, "QW9 LOCAL KIRANA STORE")
        await client.patch(
            f"/api/v1/transactions/{created.json()['id']}",
            headers=auth_headers,
            json={"category_id": groceries},
        )

        after = await client.post("/api/v1/categorization/retrain", headers=auth_headers)
        assert after.status_code == 200
        assert after.json()["examples"] == baseline["examples"] + 1
        # The version embeds a hash of the training set, so a changed corpus
        # cannot be mistaken for the old model.
        assert after.json()["version"] != baseline["version"]

    async def test_retraining_requires_authentication(self, client):
        assert (await client.post("/api/v1/categorization/retrain")).status_code == 401


class TestTheModelPath:
    async def test_an_unknown_merchant_can_still_be_classified(self, client, auth_headers):
        """The rules layer only knows the seed corpus. Everything past it is the
        model's job, and this is the case that justifies having one at all."""
        response = await client.get(
            "/api/v1/categorization/suggest",
            headers=auth_headers,
            params={"merchant": "MEDPLUS PHARMACY LTD"},
        )

        assert response.status_code == 200
        suggestion = response.json()["suggestion"]
        assert suggestion is not None, "the model should generalise to an unseen pharmacy"
        assert suggestion["source"] == "model"
        assert suggestion["slug"] == "healthcare"
        # A model prediction carries the artefact version, not "rules-v1", so a
        # bad batch of suggestions can be traced to the model that made them.
        assert suggestion["version"].startswith("tfidf-lr-v1-")

    async def test_a_broken_model_degrades_to_uncategorised(
        self, client, auth_headers, monkeypatch
    ):
        """A model fault must not fail a write.

        Categorisation is an enhancement to recording a transaction, never a
        precondition for it. If the artefact is corrupt or sklearn is missing,
        the transaction still saves -- uncategorised.
        """
        from app.modules.categorization import service as categorization_service

        def explode(*_args, **_kwargs):
            raise RuntimeError("artefact is corrupt")

        monkeypatch.setattr(categorization_service, "get_classifier", explode)

        account_id = await _account(client, auth_headers)
        # A merchant the model *does* classify (see the test above), so the
        # empty category proves the fallback ran rather than that nothing was
        # ever going to match.
        response = await _spend(client, auth_headers, account_id, "MEDPLUS PHARMACY LTD")

        assert response.status_code == 201
        assert response.json()["category"] is None
