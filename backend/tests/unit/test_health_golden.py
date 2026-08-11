"""Golden-file tests for health explanations (M6 exit criterion).

Three fixture personas, each pinned to exact expected output. The point is not
to re-verify the arithmetic -- `test_health_scoring.py` does that -- but to make
any change in *user-visible wording, weights, or bands* show up as a failing
diff rather than as a number that quietly moved.

**A failure here is not necessarily a bug.** Changing the rubric is allowed; it
is changing it *without noticing* that is not. When a change is intentional,
update the expectation in the same commit, which is what makes the rubric change
reviewable.

Written as inline expectations rather than JSON files on disk: the diff shows up
in review next to the change that caused it, and there is no separate artefact
to regenerate and forget.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.analytics.service import BudgetOutcome, SeriesPoint
from app.modules.health import measure
from app.modules.health.rubric import RUBRIC_VERSION
from app.modules.health.scoring import HealthInputs, score

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def _months(spec: list[tuple[str, str]]) -> list[SeriesPoint]:
    return [
        SeriesPoint(
            period=f"2026-{i + 1:02d}",
            income=Decimal(income),
            expense=Decimal(expense),
            net=Decimal(income) - Decimal(expense),
        )
        for i, (income, expense) in enumerate(spec)
    ]


#: A salaried professional with a real emergency fund and a home loan.
PRUDENT = dict(
    cashflow=_months([("120000", "78000")] * 12),
    liquid=Decimal("400000"),
    debt_paid=Decimal("216000"),
    budget_outcomes=[
        BudgetOutcome("Groceries", Decimal("18000"), Decimal("17200")),
        BudgetOutcome("Dining out", Decimal("6000"), Decimal("6800")),
        BudgetOutcome("Shopping", Decimal("10000"), Decimal("9100")),
    ],
    net_worth_trend=[(f"2026-{i:02d}", Decimal(600000 + i * 12000)) for i in range(1, 13)],
)

#: Living to the edge of income, no cushion, heavy EMIs.
STRETCHED = dict(
    cashflow=_months([("70000", "68000")] * 12),
    liquid=Decimal("25000"),
    debt_paid=Decimal("300000"),
    budget_outcomes=[
        BudgetOutcome("Groceries", Decimal("12000"), Decimal("15400")),
        BudgetOutcome("Dining out", Decimal("4000"), Decimal("7300")),
    ],
    net_worth_trend=[(f"2026-{i:02d}", Decimal(200000 - i * 4000)) for i in range(1, 13)],
)

#: Six weeks in. Enough to score, not enough to be confident, and several
#: metrics genuinely unmeasurable.
NEWCOMER = dict(
    cashflow=_months([("90000", "62000"), ("90000", "71000")]),
    liquid=Decimal("140000"),
    debt_paid=Decimal("0"),
    budget_outcomes=[],
    net_worth_trend=[("2026-07", Decimal("140000")), ("2026-08", Decimal("168000"))],
)


def _score(persona: dict, *, days: int, count: int):
    return score(
        HealthInputs(
            window_start=date(2025, 8, 5),
            window_end=date(2026, 8, 5),
            observation_days=days,
            transaction_count=count,
            computed_at=NOW,
            metrics=measure.measure_all(**persona),  # type: ignore[arg-type]
        )
    )


@pytest.fixture(scope="module")
def prudent():
    return _score(PRUDENT, days=365, count=420)


@pytest.fixture(scope="module")
def stretched():
    return _score(STRETCHED, days=365, count=380)


@pytest.fixture(scope="module")
def newcomer():
    return _score(NEWCOMER, days=44, count=38)


class TestPrudentSaver:
    def test_the_headline(self, prudent) -> None:
        assert prudent.score == Decimal("87.25")
        assert prudent.risk.value == "low"
        assert prudent.explanation.verdict == "LOW"
        assert prudent.explanation.confidence == Decimal("1.000")
        assert prudent.explanation.method == f"rubric_{RUBRIC_VERSION}"

    def test_the_factors(self, prudent) -> None:
        assert [(f.name, f.value, str(f.contribution)) for f in prudent.explanation.factors] == [
            ("Savings rate", "35.0%", "25.00"),
            ("Emergency fund", "5.1 months", "21.25"),
            ("Debt-to-income", "15.0%", "17.00"),
            ("Budget discipline", "2 of 3 kept", "9.75"),
            ("Cash-flow stability", "steady", "10.00"),
            ("Financial growth", "+1.8%/mo", "4.25"),
        ]

    def test_the_wording_a_user_actually_reads(self, prudent) -> None:
        by_name = {f.name: f.explanation for f in prudent.explanation.factors}
        assert by_name["Savings rate"] == (
            "You keep 35.0% of what you earn, at or above the 20% healthy mark."
        )
        assert by_name["Emergency fund"] == (
            "Liquid savings cover 5.1 months; 6 months is the target."
        )
        assert by_name["Budget discipline"] == "You kept 2 of 3 budgets. Over on: Dining out."
        # The unit belongs once. "+1.8%/mo per month" shipped to a screenshot
        # before anyone read it aloud.
        assert by_name["Financial growth"] == "Net worth is growing about 1.8% per month."

    def test_a_healthy_user_gets_no_caveats(self, prudent) -> None:
        assert prudent.explanation.caveats == []


class TestStretchedBorrower:
    def test_the_headline(self, stretched) -> None:
        assert stretched.score == Decimal("29.50")
        assert stretched.risk.value == "high"

    def test_the_factors(self, stretched) -> None:
        assert [(f.name, f.value, str(f.contribution)) for f in stretched.explanation.factors] == [
            ("Savings rate", "2.9%", "3.75"),
            ("Emergency fund", "0.4 months", "0.00"),
            ("Debt-to-income", "35.7%", "13.00"),
            ("Budget discipline", "0 of 2 kept", "2.25"),
            ("Cash-flow stability", "steady", "10.00"),
            ("Financial growth", "-2.3%/mo", "0.50"),
        ]

    def test_the_weak_factors_are_marked_negative(self, stretched) -> None:
        """Direction is what the UI colours on. A factor dragging the score down
        while labelled positive would be actively misleading."""
        by_name = {f.name: f.direction.value for f in stretched.explanation.factors}
        assert by_name["Emergency fund"] == "negative"
        assert by_name["Savings rate"] == "negative"
        assert by_name["Financial growth"] == "negative"

    def test_the_advice_is_specific_not_scolding(self, stretched) -> None:
        by_name = {f.name: f.explanation for f in stretched.explanation.factors}
        assert "Three months is the point" in by_name["Emergency fund"]
        assert "usually set too low rather than ignored" in by_name["Budget discipline"]


class TestNewcomer:
    def test_a_score_is_offered_but_hedged(self, newcomer) -> None:
        assert newcomer.score == Decimal("75.00")
        assert newcomer.explanation.confidence == Decimal("0.121")

    def test_a_high_score_on_thin_data_is_not_sold_as_low_risk(self, newcomer) -> None:
        """75 would normally be `low`. Reassurance we have not earned is the one
        thing a financial tool must not overclaim, so it is capped at moderate
        and the reason is stated."""
        assert newcomer.risk.value == "moderate"
        assert any("moderate rather than low risk" in c for c in newcomer.explanation.caveats)

    def test_unmeasurable_metrics_are_named_in_the_caveats(self, newcomer) -> None:
        """M6 exit criterion: a partial score with explicit caveats, never a
        fabricated number."""
        caveats = " ".join(newcomer.explanation.caveats)
        assert "Budget discipline could not be measured" in caveats
        assert "Cash-flow stability could not be measured" in caveats
        assert "Financial growth could not be measured" in caveats
        assert "not enough to be confident" in caveats

    def test_only_the_measurable_factors_appear(self, newcomer) -> None:
        assert [f.name for f in newcomer.explanation.factors] == [
            "Savings rate",
            "Emergency fund",
            "Debt-to-income",
        ]

    def test_the_arithmetic_still_reconciles(self, newcomer) -> None:
        """The invariant must survive weight redistribution, which is exactly
        where it is most likely to break."""
        assert newcomer.explanation.total_contribution == newcomer.score
        assert newcomer.explanation.total_weight == Decimal("1.0000")
