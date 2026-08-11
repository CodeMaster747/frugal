"""Detector thresholds, and the ways they are supposed to stay quiet.

Every detector is pure, so this needs no database. Most of these tests assert an
*absence* -- that a technically-true finding was suppressed. That is the harder
half of an insight engine: anyone can detect a 3% change, and a feed that
reports one teaches users to ignore all of them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from app.core.explanation import DataWindow
from app.modules.analytics.service import BudgetOutcome, CategorySlice, SeriesPoint
from app.modules.insights import detectors
from app.modules.insights.models import Severity

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
WINDOW = DataWindow(start=date(2025, 8, 5), end=date(2026, 8, 5), observation_days=365)
SHARED = {"window": WINDOW, "computed_at": NOW}


def slice_(name: str, amount: str, previous: str) -> CategorySlice:
    """A category slice with `change_pct` in the units analytics really uses.

    Analytics returns a *percentage* (261.33), not a fraction. Constructing it
    the same way here is the point: a test that used fractions would pass while
    production divided by a hundred too few times.
    """
    current, before = Decimal(amount), Decimal(previous)
    return CategorySlice(
        category_id=uuid.uuid4(),
        name=name,
        slug=name.lower().replace(" ", "-"),
        amount=current,
        share_pct=Decimal("10"),
        previous_amount=before,
        change_pct=((current - before) / before * 100).quantize(Decimal("0.01"))
        if before > 0
        else None,
    )


class TestCategorySpikes:
    def test_a_material_jump_is_reported(self) -> None:
        found = detectors.category_spikes(
            [slice_("Dining out", "9000", "5000")], period_label="2026-08", **SHARED
        )

        assert len(found) == 1
        assert "80.0%" in found[0].title
        assert found[0].impact_amount == Decimal("4000")

    def test_a_large_but_proportionally_small_move_is_ignored(self) -> None:
        """The regression this guards.

        `change_pct` is a percentage and every threshold in the detector is a
        fraction. Comparing them directly made `261.33 < 0.40` false for
        *everything*, so the percentage gate silently did nothing and any
        category clearing the rupee floor fired. Groceries up 5% on a large base
        clears ₹2,000 easily and is not news.
        """
        found = detectors.category_spikes(
            [slice_("Groceries", "42000", "40000")], period_label="2026-08", **SHARED
        )

        assert found == []

    def test_a_large_percentage_on_a_trivial_base_is_ignored(self) -> None:
        found = detectors.category_spikes(
            [slice_("Gifts", "900", "100")], period_label="2026-08", **SHARED
        )

        assert found == []

    def test_a_tiny_baseline_gets_rupees_rather_than_an_absurd_percentage(self) -> None:
        """₹46 -> ₹12,082 is "up 26,133%", which reads as a glitch.

        Sporadic categories -- a laptop, a flight -- have near-zero baselines
        constantly, so this is the common case rather than an edge one.
        """
        found = detectors.category_spikes(
            [slice_("Electronics", "12082", "46")], period_label="2026-08", **SHARED
        )

        assert len(found) == 1
        assert "%" not in found[0].title
        assert "₹" in found[0].title
        # And it is not dressed up as urgent, because a lumpy category is not.
        assert found[0].severity is Severity.INFO

    def test_a_category_with_no_history_is_not_a_spike(self) -> None:
        """A first month is new information, not a 100% increase."""
        assert (
            detectors.category_spikes(
                [slice_("Travel", "20000", "0")], period_label="2026-08", **SHARED
            )
            == []
        )


class TestBudgetBreaches:
    def test_a_material_overspend_is_reported(self) -> None:
        found = detectors.budget_breaches(
            [BudgetOutcome("Groceries", Decimal("15000"), Decimal("19000"))],
            period_label="2026-08",
            **SHARED,
        )

        assert len(found) == 1
        assert found[0].impact_amount == Decimal("4000")
        assert found[0].severity is Severity.WARNING

    def test_a_budget_missed_by_pennies_is_not_a_breach(self) -> None:
        assert (
            detectors.budget_breaches(
                [BudgetOutcome("Groceries", Decimal("15000"), Decimal("15120"))],
                period_label="2026-08",
                **SHARED,
            )
            == []
        )

    def test_a_kept_budget_is_silent(self) -> None:
        assert (
            detectors.budget_breaches(
                [BudgetOutcome("Groceries", Decimal("15000"), Decimal("11000"))],
                period_label="2026-08",
                **SHARED,
            )
            == []
        )


class TestEmergencyFund:
    def test_thin_reserves_are_raised_as_critical_below_a_month(self) -> None:
        found = detectors.emergency_fund_low(
            Decimal("20000"), Decimal("60000"), period_label="2026-08", **SHARED
        )

        assert len(found) == 1
        assert found[0].severity is Severity.CRITICAL
        assert found[0].impact_amount == Decimal("160000")

    def test_adequate_reserves_are_silent(self) -> None:
        assert (
            detectors.emergency_fund_low(
                Decimal("400000"), Decimal("60000"), period_label="2026-08", **SHARED
            )
            == []
        )

    def test_no_spending_baseline_yields_nothing_rather_than_a_divide_by_zero(self) -> None:
        assert (
            detectors.emergency_fund_low(
                Decimal("10000"), Decimal("0"), period_label="2026-08", **SHARED
            )
            == []
        )


class TestSavingsRateChange:
    def test_a_sustained_shift_is_reported(self) -> None:
        trend = [(f"2026-{i:02d}", Decimal("0.10")) for i in range(1, 4)] + [
            (f"2026-{i:02d}", Decimal("0.30")) for i in range(4, 7)
        ]
        found = detectors.savings_rate_change(trend, period_label="2026-08", **SHARED)

        assert len(found) == 1
        assert "more" in found[0].title
        assert found[0].explanation.verdict == "IMPROVED"

    def test_month_to_month_noise_is_ignored(self) -> None:
        trend = [(f"2026-{i:02d}", Decimal("0.22")) for i in range(1, 4)] + [
            (f"2026-{i:02d}", Decimal("0.25")) for i in range(4, 7)
        ]
        assert detectors.savings_rate_change(trend, period_label="2026-08", **SHARED) == []

    def test_too_few_months_yields_nothing(self) -> None:
        trend = [(f"2026-{i:02d}", Decimal("0.30")) for i in range(1, 4)]
        assert detectors.savings_rate_change(trend, period_label="2026-08", **SHARED) == []

    def test_months_without_income_do_not_count_as_zero(self) -> None:
        """A None rate is "undefined", and treating it as 0% would manufacture a
        collapse the user never experienced."""
        trend: list[tuple[str, Decimal | None]] = [
            ("2026-01", Decimal("0.25")),
            ("2026-02", None),
            ("2026-03", Decimal("0.25")),
        ]
        assert detectors.savings_rate_change(trend, period_label="2026-08", **SHARED) == []


class TestCashflowShortfall:
    def _months(self, nets: list[tuple[str, str]]) -> list[SeriesPoint]:
        return [
            SeriesPoint(
                period=f"2026-{i + 1:02d}",
                income=Decimal(income),
                expense=Decimal(expense),
                net=Decimal(income) - Decimal(expense),
            )
            for i, (income, expense) in enumerate(nets)
        ]

    def test_two_negative_months_in_three_is_a_pattern(self) -> None:
        found = detectors.cashflow_shortfall(
            self._months([("50000", "70000"), ("50000", "45000"), ("50000", "62000")]),
            period_label="2026-08",
            **SHARED,
        )

        assert len(found) == 1
        assert found[0].severity is Severity.CRITICAL

    def test_one_negative_month_is_a_large_purchase(self) -> None:
        assert (
            detectors.cashflow_shortfall(
                self._months([("50000", "40000"), ("50000", "45000"), ("50000", "72000")]),
                period_label="2026-08",
                **SHARED,
            )
            == []
        )


class TestAnomalousTransactions:
    def _outlier(self, amount: str, median: str) -> detectors.OutlierTransaction:
        return detectors.OutlierTransaction(
            transaction_id=uuid.uuid4(),
            merchant="Croma",
            category_name="Electronics",
            amount=Decimal(amount),
            category_median=Decimal(median),
            occurred_on=date(2026, 8, 1),
        )

    def test_a_large_multiple_is_flagged(self) -> None:
        found = detectors.anomalous_transactions([self._outlier("40000", "5000")], **SHARED)

        assert len(found) == 1
        assert found[0].subject_id is not None
        assert "prompt, not a judgement" in " ".join(found[0].explanation.caveats)

    def test_a_small_absolute_amount_is_ignored_however_large_the_multiple(self) -> None:
        """20× a ₹50 median is ₹1,000. True, and not worth a notification."""
        assert detectors.anomalous_transactions([self._outlier("1000", "50")], **SHARED) == []

    def test_it_is_keyed_on_the_transaction_so_it_is_raised_once_ever(self) -> None:
        outlier = self._outlier("40000", "5000")
        found = detectors.anomalous_transactions([outlier], **SHARED)

        assert found[0].dedup_key == f"anomalous_transaction:{outlier.transaction_id}"


class TestNewRecurring:
    def _item(self, *, auto: bool, seen: date) -> detectors.RecurringItem:
        return detectors.RecurringItem(
            item_id=uuid.uuid4(),
            name="Netflix",
            amount=Decimal("499"),
            cadence="monthly",
            first_seen_on=seen,
            is_auto_detected=auto,
        )

    def test_an_auto_detected_commitment_is_reported_annualised(self) -> None:
        found = detectors.new_recurring(
            [self._item(auto=True, seen=date(2026, 7, 20))], since=date(2026, 7, 1), **SHARED
        )

        assert len(found) == 1
        # ₹499/month is easy to wave through; ₹5,988/year is not.
        assert found[0].impact_amount == Decimal("5988")
        assert "5,988" in found[0].body

    def test_an_item_the_user_typed_in_is_not_news_to_them(self) -> None:
        assert (
            detectors.new_recurring(
                [self._item(auto=False, seen=date(2026, 7, 20))],
                since=date(2026, 7, 1),
                **SHARED,
            )
            == []
        )

    def test_an_old_commitment_is_not_new(self) -> None:
        assert (
            detectors.new_recurring(
                [self._item(auto=True, seen=date(2025, 3, 1))],
                since=date(2026, 7, 1),
                **SHARED,
            )
            == []
        )


class TestGoalsAtRisk:
    def _goal(self, current: str, target: str, target_date: date, surplus: str):
        return detectors.GoalProgress(
            goal_id=uuid.uuid4(),
            name="Japan trip",
            target_amount=Decimal(target),
            current_amount=Decimal(current),
            target_date=target_date,
            monthly_surplus=Decimal(surplus),
        )

    def test_a_goal_the_surplus_cannot_reach_is_flagged(self) -> None:
        found = detectors.goals_at_risk(
            [self._goal("50000", "300000", date(2026, 12, 1), "10000")],
            today=date(2026, 8, 5),
            **SHARED,
        )

        assert len(found) == 1
        assert found[0].severity is Severity.WARNING
        assert "behind schedule" in found[0].title

    def test_a_goal_on_track_is_silent(self) -> None:
        assert (
            detectors.goals_at_risk(
                [self._goal("50000", "100000", date(2027, 8, 1), "20000")],
                today=date(2026, 8, 5),
                **SHARED,
            )
            == []
        )

    def test_a_goal_already_met_is_silent(self) -> None:
        assert (
            detectors.goals_at_risk(
                [self._goal("120000", "100000", date(2026, 12, 1), "0")],
                today=date(2026, 8, 5),
                **SHARED,
            )
            == []
        )

    def test_a_goal_with_no_deadline_cannot_be_behind(self) -> None:
        assert (
            detectors.goals_at_risk(
                [self._goal("0", "100000", None, "0")], today=date(2026, 8, 5), **SHARED
            )
            == []
        )


class TestMateriality:
    def test_ranking_is_impact_times_confidence(self) -> None:
        big = detectors.budget_breaches(
            [BudgetOutcome("Rent", Decimal("30000"), Decimal("50000"))],
            period_label="2026-08",
            **SHARED,
        )[0]
        small = detectors.budget_breaches(
            [BudgetOutcome("Coffee", Decimal("2000"), Decimal("2900"))],
            period_label="2026-08",
            **SHARED,
        )[0]

        assert big.materiality > small.materiality

    def test_an_unquantifiable_finding_still_outranks_nothing(self) -> None:
        """Without a nominal base, "your savings rate collapsed" would rank
        below every ₹300 category wobble, because it carries no rupee figure."""
        trend = [(f"2026-{i:02d}", Decimal("0.30")) for i in range(1, 4)] + [
            (f"2026-{i:02d}", Decimal("0.05")) for i in range(4, 7)
        ]
        finding = detectors.savings_rate_change(trend, period_label="2026-08", **SHARED)[0]

        assert finding.impact_amount is None
        assert finding.materiality > Decimal("0")


class TestEveryCandidateIsExplained:
    def test_no_detector_emits_a_verdict_without_factors(self) -> None:
        """ADR-002 enforces this at construction, so reaching this assertion at
        all means every detector built a real Explanation rather than a stub."""
        candidates = [
            *detectors.category_spikes(
                [slice_("Dining out", "9000", "5000")], period_label="2026-08", **SHARED
            ),
            *detectors.budget_breaches(
                [BudgetOutcome("Rent", Decimal("30000"), Decimal("50000"))],
                period_label="2026-08",
                **SHARED,
            ),
            *detectors.emergency_fund_low(
                Decimal("20000"), Decimal("60000"), period_label="2026-08", **SHARED
            ),
        ]

        assert candidates
        for candidate in candidates:
            assert candidate.explanation.verdict
            assert candidate.explanation.factors
            assert candidate.explanation.method == "rule_v1"
            for factor in candidate.explanation.factors:
                assert factor.name and factor.value and factor.explanation
