"""The health rubric's arithmetic and its honesty about thin data.

No database: `rubric`, `measure`, and `scoring` are pure, which is the point of
separating them from the service. Every band and every abstention is reachable
here in milliseconds.

The two tests that matter most are `test_weights_sum_to_one` and
`test_contributions_reconstruct_the_score`. A rubric whose parts do not sum to
the whole is a decoration with a number attached, and ADR-002 exists to stop
that shipping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.modules.analytics.service import BudgetOutcome, SeriesPoint
from app.modules.health import measure
from app.modules.health.rubric import (
    METRICS,
    RISK_BANDS,
    MetricKey,
    RiskLevel,
    published,
    risk_level,
    total_weight,
)
from app.modules.health.scoring import (
    MIN_OBSERVATION_DAYS,
    HealthInputs,
    MetricInput,
    confidence_for,
    score,
)

NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def months(spec: list[tuple[str, str, str]]) -> list[SeriesPoint]:
    """`[(label, income, expense)]` -> cash-flow points."""
    return [
        SeriesPoint(
            period=label,
            income=Decimal(income),
            expense=Decimal(expense),
            net=Decimal(income) - Decimal(expense),
        )
        for label, income, expense in spec
    ]


def healthy_months(n: int = 12) -> list[SeriesPoint]:
    return months([(f"2026-{i:02d}", "100000", "70000") for i in range(1, n + 1)])


def inputs(
    metrics: dict[MetricKey, MetricInput],
    *,
    observation_days: int = 365,
    transaction_count: int = 400,
) -> HealthInputs:
    return HealthInputs(
        window_start=date(2025, 8, 5),
        window_end=date(2026, 8, 5),
        observation_days=observation_days,
        transaction_count=transaction_count,
        computed_at=NOW,
        metrics=metrics,
    )


def all_measured() -> dict[MetricKey, MetricInput]:
    return measure.measure_all(
        cashflow=healthy_months(),
        liquid=Decimal("420000"),
        debt_paid=Decimal("120000"),
        budget_outcomes=[
            BudgetOutcome("Groceries", Decimal("15000"), Decimal("14000")),
            BudgetOutcome("Dining", Decimal("6000"), Decimal("5000")),
        ],
        net_worth_trend=[(f"2026-{i:02d}", Decimal(400000 + i * 9000)) for i in range(1, 13)],
    )


class TestTheRubricItself:
    def test_weights_sum_to_one(self) -> None:
        """M6 exit criterion.

        Not "approximately one": these are Decimals chosen by hand, and a rubric
        that sums to 0.99 silently caps every user at 99.
        """
        assert total_weight() == Decimal("1.00")

    def test_there_are_six_sub_metrics(self) -> None:
        assert len(METRICS) == 6
        assert {m.key for m in METRICS} == set(MetricKey)

    def test_every_band_ladder_descends(self) -> None:
        """Bands are walked best-first, so a ladder out of order would silently
        return the wrong rung for every value below the fault."""
        for metric in METRICS:
            points = [b.points for b in metric.bands]
            assert points == sorted(points, reverse=True), f"{metric.key} bands out of order"

    def test_every_metric_can_score_its_worst_case(self) -> None:
        """A value below every threshold must return a number, not raise."""
        for metric in METRICS:
            worst = Decimal("-999999") if metric.higher_is_better else Decimal("999999")
            points, label = metric.score(worst)
            assert Decimal(0) <= points <= Decimal(100)
            assert label

    def test_risk_bands_cover_the_whole_range(self) -> None:
        assert risk_level(Decimal("100")) is RiskLevel.LOW
        assert risk_level(Decimal("0")) is RiskLevel.HIGH
        assert RISK_BANDS[-1][0] == Decimal("0")

    def test_the_published_rubric_is_serialisable_and_complete(self) -> None:
        """`GET /health-score/rubric` is the promise that the score is
        inspectable. If it omits a metric, the promise is broken."""
        doc = published()
        assert doc["version"]
        assert doc["total_weight"] == "1.00"
        assert len(doc["metrics"]) == 6
        for entry in doc["metrics"]:
            assert entry["bands"], f"{entry['key']} published with no bands"
            # Decimals must be strings on the wire, never floats (ADR-003).
            assert isinstance(entry["weight"], str)


class TestScoreArithmetic:
    def test_contributions_reconstruct_the_score(self) -> None:
        """M6 exit criterion, and the central claim of ADR-002."""
        result = score(inputs(all_measured()))

        assert result.score is not None
        assert result.explanation.is_arithmetically_consistent()
        assert result.explanation.total_contribution == result.score

    def test_effective_weights_sum_to_one(self) -> None:
        result = score(inputs(all_measured()))
        assert abs(result.explanation.total_weight - Decimal(1)) <= Decimal("0.0001")

    def test_every_factor_is_fully_populated(self) -> None:
        """M6 exit criterion: value, weight, contribution, direction, and plain
        language on every one. A factor with an empty explanation is a factor
        the user cannot act on."""
        result = score(inputs(all_measured()))

        assert len(result.explanation.factors) == 6
        for factor in result.explanation.factors:
            assert factor.name
            assert factor.value
            assert factor.explanation
            assert Decimal(0) <= factor.weight <= Decimal(1)
            assert factor.direction in {"positive", "negative", "neutral"}

    def test_the_score_stays_in_range(self) -> None:
        best = score(
            inputs(
                measure.measure_all(
                    cashflow=months([(f"2026-{i:02d}", "200000", "40000") for i in range(1, 13)]),
                    liquid=Decimal("5000000"),
                    debt_paid=Decimal("0"),
                    budget_outcomes=[BudgetOutcome("Food", Decimal("10000"), Decimal("1000"))],
                    net_worth_trend=[
                        (f"2026-{i:02d}", Decimal(100000 * (i + 1))) for i in range(1, 13)
                    ],
                )
            )
        )
        worst = score(
            inputs(
                measure.measure_all(
                    cashflow=months([(f"2026-{i:02d}", "50000", "90000") for i in range(1, 13)]),
                    liquid=Decimal("0"),
                    debt_paid=Decimal("300000"),
                    budget_outcomes=[BudgetOutcome("Food", Decimal("5000"), Decimal("20000"))],
                    net_worth_trend=[
                        (f"2026-{i:02d}", Decimal(500000 - i * 30000)) for i in range(1, 13)
                    ],
                )
            )
        )

        assert best.score is not None and worst.score is not None
        assert Decimal(0) <= worst.score < best.score <= Decimal(100)
        assert best.risk is RiskLevel.LOW
        assert worst.risk in {RiskLevel.ELEVATED, RiskLevel.HIGH}


class TestHonestyAboutThinData:
    def test_too_little_history_yields_no_score_at_all(self) -> None:
        """M6 exit criterion: never a fabricated number.

        Eleven days cannot support a claim about someone's finances, and a
        number produced from them would be believed anyway.
        """
        result = score(inputs(all_measured(), observation_days=11, transaction_count=9))

        assert result.score is None
        assert result.risk is None
        assert result.explanation.verdict is None
        assert result.explanation.factors == []
        assert result.explanation.caveats, "an absent score must say why"
        assert "11 days" in result.explanation.caveats[0]

    def test_the_threshold_is_the_boundary_not_a_suggestion(self) -> None:
        just_under = score(inputs(all_measured(), observation_days=MIN_OBSERVATION_DAYS - 1))
        just_over = score(inputs(all_measured(), observation_days=MIN_OBSERVATION_DAYS))

        assert just_under.score is None
        assert just_over.score is not None

    def test_an_unmeasurable_metric_is_excluded_not_zeroed(self) -> None:
        """The distinction the whole module turns on.

        Scoring an unknown as zero produces a confident, precise, wrong number.
        Excluding it and redistributing the weight keeps the score on the same
        scale and tells the user what was left out.
        """
        metrics = all_measured()
        metrics[MetricKey.BUDGET_DISCIPLINE] = MetricInput(
            raw=None,
            display="—",
            detail="no budgets",
            available=False,
            unavailable_because="no budgets set",
        )

        result = score(inputs(metrics))

        assert result.score is not None
        assert len(result.explanation.factors) == 5
        assert all(f.name != "Budget discipline" for f in result.explanation.factors)
        # Redistributed, so the surviving weights still total 1.00.
        assert abs(result.explanation.total_weight - Decimal(1)) <= Decimal("0.0001")
        assert result.explanation.is_arithmetically_consistent()
        assert any("Budget discipline" in c for c in result.explanation.caveats)

    def test_excluding_a_metric_does_not_cap_the_score(self) -> None:
        """A user with no budgets should still be able to score 100.

        If the missing weight were simply dropped, they would be capped at 85
        forever -- which reads as a judgement on them rather than as missing
        data.
        """
        perfect = {
            key: MetricInput(raw=Decimal("999"), display="x", detail="x")
            for key in MetricKey
            if key not in {MetricKey.DEBT_TO_INCOME, MetricKey.CASHFLOW_STABILITY}
        }
        perfect[MetricKey.DEBT_TO_INCOME] = MetricInput(Decimal("0"), "0%", "none")
        perfect[MetricKey.CASHFLOW_STABILITY] = MetricInput(Decimal("0"), "steady", "steady")
        del perfect[MetricKey.BUDGET_DISCIPLINE]

        result = score(inputs(perfect))

        assert result.score == Decimal("100.00")

    def test_no_measurable_metrics_yields_no_score(self) -> None:
        blank = {
            key: MetricInput(None, "—", "no data", available=False, unavailable_because="empty")
            for key in MetricKey
        }
        result = score(inputs(blank))

        assert result.score is None
        assert result.explanation.factors == []
        assert result.explanation.caveats

    def test_low_confidence_is_stated_rather_than_hidden(self) -> None:
        result = score(inputs(all_measured(), observation_days=60, transaction_count=40))

        assert result.score is not None
        assert result.explanation.confidence < Decimal("0.5")
        assert any("not enough to be confident" in c for c in result.explanation.caveats)

    def test_confidence_is_bounded_by_whichever_evidence_is_thinner(self) -> None:
        """A year of history with nine transactions is not a year of evidence."""
        assert confidence_for(365, 9) < Decimal("0.1")
        assert confidence_for(14, 500) < Decimal("0.1")
        assert confidence_for(365, 400) == Decimal("1.000")


class TestMeasurement:
    def test_a_savings_rate_needs_a_denominator(self) -> None:
        """Zero income means undefined, not 0%. Reporting 0% would read as
        "you saved nothing" rather than "we cannot say"."""
        result = measure.savings_rate(months([("2026-01", "0", "5000"), ("2026-02", "0", "6000")]))

        assert not result.available
        assert result.raw is None
        assert "no income" in result.unavailable_because

    def test_overspending_produces_a_negative_rate_not_an_error(self) -> None:
        result = measure.savings_rate(
            months([("2026-01", "50000", "70000"), ("2026-02", "50000", "70000")])
        )

        assert result.available
        assert result.raw is not None and result.raw < 0
        assert "more than you earned" in result.detail

    def test_no_budgets_is_unavailable_not_perfect(self) -> None:
        """Scoring an absent budget as 100% kept would reward not trying."""
        result = measure.budget_discipline([])

        assert not result.available
        assert result.raw is None

    def test_zero_debt_is_measured_rather_than_skipped(self) -> None:
        """Unlike the others, absence of debt rows is information."""
        result = measure.debt_to_income(Decimal("0"), healthy_months())

        assert result.available
        assert result.raw == Decimal("0")
        assert "No debt repayments" in result.detail

    def test_growth_ignores_steps_from_a_non_positive_base(self) -> None:
        """A ratio against zero is meaningless, not infinite -- and one such
        step would dominate the average."""
        result = measure.growth(
            [
                ("2026-01", Decimal("0")),
                ("2026-02", Decimal("10000")),
                ("2026-03", Decimal("11000")),
                ("2026-04", Decimal("12100")),
            ]
        )

        assert result.available
        assert result.raw is not None
        # ~10% per month from the two valid steps, not an astronomical figure
        # from the 0 -> 10000 one.
        assert Decimal("0.05") < result.raw < Decimal("0.15")

    def test_stability_measures_against_spending_not_net(self) -> None:
        """A household whose net is near zero every month is stable, not
        infinitely volatile. Dividing by net would say the opposite."""
        steady = measure.cashflow_stability(
            months([(f"2026-{i:02d}", "50000", "50000") for i in range(1, 7)])
        )

        assert steady.available
        assert steady.raw == Decimal("0")
        assert steady.display == "steady"

    def test_emergency_fund_needs_a_spending_baseline(self) -> None:
        result = measure.emergency_fund(Decimal("100000"), months([("2026-01", "5000", "0")]))

        assert not result.available
        assert "spending" in result.unavailable_because
