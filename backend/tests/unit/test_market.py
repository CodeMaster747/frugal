"""The reliability rubric, the price simulator, and the port's substitutability.

Pure, so no database.

The most important tests here are not about arithmetic. They are about **what
this feature is allowed to say**. FR-9.2 replaced a "scam risk" score for a
reason, and a later change that quietly reintroduces a judgement about a named
business would undo that deliberately. `TestItMakesNoAccusations` is a guard
against that, not a formality.
"""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.adapters.pricing.catalog import BY_ID, CATALOG
from app.adapters.pricing.seed_catalog import SeedCatalogProvider
from app.adapters.pricing.simulated_market import SimulatedMarketProvider, price_on
from app.modules.market import reliability
from app.modules.market.reliability import Confidence, SignalKey, score_offer

TODAY = date(2026, 8, 6)


def offer(**overrides) -> dict:
    base = dict(
        seller_rating=Decimal("4.4"),
        rating_count=2400,
        return_window_days=14,
        warranty_months=12,
        fulfillment_type="platform",
        price=Decimal("100000"),
        market_median=Decimal("102000"),
    )
    return {**base, **overrides}


class TestTheRubric:
    def test_weights_sum_to_one(self) -> None:
        assert reliability.total_weight() == Decimal("1.00")

    def test_there_are_six_observable_signals(self) -> None:
        """Exactly the six FR-9.2 permits, and no others.

        The list is the boundary of what this score is allowed to consider. A
        seventh signal is a product decision, not an implementation detail.
        """
        assert {s.key for s in reliability.SIGNALS} == set(SignalKey)
        assert len(reliability.SIGNALS) == 6

    def test_contributions_reconstruct_the_score(self) -> None:
        result = score_offer(**offer())
        assert sum((s.contribution for s in result.signals), Decimal(0)) == result.score

    def test_effective_weights_sum_to_one_even_with_signals_missing(self) -> None:
        result = score_offer(**offer(seller_rating=None, warranty_months=None))
        assert sum((s.weight for s in result.signals), Decimal(0)) == Decimal("1.0000")

    def test_the_published_rubric_states_what_it_is_not(self) -> None:
        """The disclaimer is part of the contract, not decoration."""
        doc = reliability.published()

        assert doc["total_weight"] == "1.00"
        assert len(doc["signals"]) == 6
        assert "not a judgement about the seller" in doc["what_this_is_not"]
        assert doc["missing_signals"]


class TestMissingSignals:
    def test_a_missing_signal_is_excluded_not_scored_zero(self) -> None:
        """Silence is not evidence of absence.

        A listing that does not state a warranty has not told us there is none.
        Scoring it zero would penalise sellers for the platform's data quality
        and would make the number claim something it cannot support.
        """
        stated = score_offer(**offer(warranty_months=24))
        silent = score_offer(**offer(warranty_months=None))
        none_offered = score_offer(**offer(warranty_months=0))

        assert len(silent.signals) == 5
        assert all(s.key is not SignalKey.WARRANTY for s in silent.signals)
        # Explicitly "no warranty" is a real, worse answer than silence.
        assert none_offered.score < silent.score
        assert stated.score > none_offered.score

    def test_every_omission_is_named_in_the_caveats(self) -> None:
        result = score_offer(**offer(seller_rating=None, rating_count=None))
        caveats = " ".join(result.caveats)

        assert "Seller rating is not stated" in caveats
        assert "Number of ratings is not stated" in caveats

    def test_confidence_falls_as_signals_go_missing(self) -> None:
        assert score_offer(**offer()).confidence is Confidence.HIGH
        assert (
            score_offer(**offer(seller_rating=None, warranty_months=None)).confidence
            is Confidence.MODERATE
        )
        assert (
            score_offer(
                **offer(
                    seller_rating=None,
                    rating_count=None,
                    warranty_months=None,
                    fulfillment_type=None,
                )
            ).confidence
            is Confidence.LOW
        )

    def test_a_thin_listing_is_not_called_well_protected(self) -> None:
        """A high score from one signal is the same overclaim the health engine
        makes when it calls a six-week-old account low-risk."""
        result = score_offer(
            seller_rating=None,
            rating_count=None,
            return_window_days=30,
            warranty_months=None,
            fulfillment_type=None,
            price=Decimal("100000"),
            market_median=None,
        )

        assert result.score >= Decimal("80")
        assert result.confidence is Confidence.LOW
        assert result.band != "well protected"

    def test_a_listing_stating_nothing_scores_nothing(self) -> None:
        result = score_offer(
            seller_rating=None,
            rating_count=None,
            return_window_days=None,
            warranty_months=None,
            fulfillment_type=None,
            price=Decimal("100000"),
            market_median=None,
        )

        assert result.signals == ()
        assert result.band == "not enough information"


class TestSignalBehaviour:
    def test_protections_outweigh_reputation(self) -> None:
        """A 30-day return window is a contractual fact; a 4.9-star average is
        an aggregate of opinions. The rubric weights them accordingly."""
        protections = score_offer(
            **offer(
                return_window_days=30,
                warranty_months=24,
                seller_rating=Decimal("3.6"),
                rating_count=90,
            )
        )
        reputation = score_offer(
            **offer(
                return_window_days=0,
                warranty_months=0,
                seller_rating=Decimal("4.9"),
                rating_count=20000,
            )
        )

        assert protections.score > reputation.score

    def test_a_perfect_rating_from_nine_people_does_not_carry_the_score(self) -> None:
        thin = score_offer(**offer(seller_rating=Decimal("5.0"), rating_count=9))
        established = score_offer(**offer(seller_rating=Decimal("4.2"), rating_count=9000))

        assert established.score > thin.score

    def test_an_implausibly_low_price_lowers_the_score(self) -> None:
        """The honest proxy for the risk the removed "scam score" reached for —
        and a statement about a number, not a person."""
        market = score_offer(**offer(price=Decimal("100000"), market_median=Decimal("102000")))
        outlier = score_offer(**offer(price=Decimal("38000"), market_median=Decimal("102000")))

        assert outlier.score < market.score
        deviation = next(s for s in outlier.signals if s.key is SignalKey.PRICE_DEVIATION)
        assert "below median" in deviation.value

    def test_a_price_above_the_median_is_not_penalised(self) -> None:
        """The signal is about implausible *cheapness*. Charging more than
        average is a pricing decision, not a risk."""
        result = score_offer(**offer(price=Decimal("120000"), market_median=Decimal("102000")))
        deviation = next(s for s in result.signals if s.key is SignalKey.PRICE_DEVIATION)

        assert deviation.raw_value == Decimal(0)
        assert deviation.points == Decimal("100")


class TestItMakesNoAccusations:
    """FR-9.2's whole point, guarded.

    The original brief asked for "Review Authenticity" and "Scam Risk". Both
    were removed: neither is honestly measurable at this data scale, and
    publishing either about a named commercial seller is a defamation exposure.
    A later change that reintroduces the language would undo a deliberate
    decision, so it fails here.
    """

    FORBIDDEN = (
        "scam",
        "fraud",
        "fraudulent",
        "fake",
        "counterfeit",
        "dishonest",
        "untrustworthy",
        "risky seller",
        "suspicious",
        "disreputable",
    )

    def test_no_band_accuses_anyone(self) -> None:
        for _, label in reliability.BANDS:
            assert not any(word in label.lower() for word in self.FORBIDDEN), label

    def test_no_signal_label_accuses_anyone(self) -> None:
        for signal in reliability.SIGNALS:
            for band in signal.bands:
                assert not any(word in band.label.lower() for word in self.FORBIDDEN), band.label

    def test_no_generated_sentence_accuses_anyone(self) -> None:
        """Across the whole space of inputs, including the worst-looking one."""
        worst = score_offer(
            seller_rating=Decimal("1.0"),
            rating_count=3,
            return_window_days=0,
            warranty_months=0,
            fulfillment_type="third_party",
            price=Decimal("9000"),
            market_median=Decimal("102000"),
        )
        prose = " ".join([worst.band, *(s.detail for s in worst.signals), *worst.caveats]).lower()

        for word in self.FORBIDDEN:
            assert word not in prose, f"{word!r} appears in reliability output"

    def test_the_module_source_carries_no_accusatory_output(self) -> None:
        """A guard against the wording drifting back in over time.

        Reads the source rather than the output because a new band or message
        would not be covered by the cases above. The docstring names the removed
        features to explain *why*, so only string literals are checked.
        """
        source = inspect.getsource(reliability)
        literals = [
            line
            for line in source.splitlines()
            if ('"' in line or "'" in line) and not line.strip().startswith(("#", "*"))
        ]
        for line in literals:
            lowered = line.lower()
            if "forbidden" in lowered or "scam risk" in lowered or "authenticity" in lowered:
                continue  # the docstring explaining the removal
            for word in ("scam", "fraudulent", "counterfeit", "untrustworthy"):
                assert word not in lowered, line.strip()


class TestThePriceSimulator:
    def test_prices_are_deterministic_for_a_day(self) -> None:
        """A chart that flickers on reload is worse than no chart."""
        item = CATALOG[0]
        first = price_on(item, "Amazon", TODAY)
        second = price_on(item, "Amazon", TODAY)

        assert first == second

    def test_different_sellers_differ_but_stay_plausible(self) -> None:
        item = BY_ID["seed:apple:macbook-air-m3-13-inch-8gb-256gb"]
        prices = [
            price_on(item, seller, TODAY)
            for seller, *_ in __import__(
                "app.adapters.pricing.simulated_market", fromlist=["x"]
            ).COMPETING_SELLERS
        ]

        assert len(set(prices)) > 1, "every seller quoting the same price is not a market"
        spread = (max(prices) - min(prices)) / max(prices)
        assert spread < Decimal("0.5"), "spread is too wide to be believable"

    def test_history_can_be_generated_backwards(self) -> None:
        """A fresh database has no history, and a chart with one point is not a
        chart. Because pricing is pure, yesterday is as computable as today."""
        provider = SimulatedMarketProvider(today=TODAY)
        history = provider.history(CATALOG[0].external_id, days=30)

        assert len(history) == 30
        assert history[0][0] == TODAY - timedelta(days=29)
        assert history[-1][0] == TODAY
        assert all(offers for _, offers in history)

    def test_it_produces_sale_events(self) -> None:
        """Drop alerts need something to fire on."""
        item = CATALOG[0]
        year = [price_on(item, "Amazon", TODAY - timedelta(days=d)) for d in range(365)]
        cheapest, dearest = min(year), max(year)

        assert (dearest - cheapest) / dearest > Decimal("0.08")


class TestThePortIsSubstitutable:
    """The M9 exit criterion: adding an adapter requires no advisor change.

    Both providers satisfy the same protocol, are constructed the same way, and
    return the same shape. Nothing under `app/modules/advisor/` imports either.
    """

    @pytest.mark.parametrize(
        "provider", [SeedCatalogProvider(), SimulatedMarketProvider(today=TODAY)]
    )
    async def test_both_providers_answer_the_same_questions(self, provider) -> None:
        results = await provider.search("macbook air", limit=5)
        assert results
        assert all(o.external_id and o.name and o.price > 0 for o in results)

        fetched = await provider.get(results[0].external_id)
        assert fetched is not None
        assert fetched.external_id == results[0].external_id

        alternatives = await provider.alternatives(results[0], max_price=results[0].price, limit=3)
        assert all(o.price <= results[0].price for o in alternatives)

    def test_the_advisor_imports_neither_adapter(self) -> None:
        """The check that makes "no advisor change" verifiable rather than
        asserted. The advisor knows the *port*; the factory knows the adapters.
        """
        import pathlib

        advisor = pathlib.Path(__file__).parents[2] / "app" / "modules" / "advisor"
        for path in advisor.glob("*.py"):
            source = path.read_text()
            assert "simulated_market" not in source, path.name
            assert "SeedCatalogProvider" not in source, path.name
