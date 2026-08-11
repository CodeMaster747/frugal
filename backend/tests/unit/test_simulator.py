"""The scenario engine's arithmetic and its honesty.

Pure, so no database.

The projection is simple enough that its bugs are invisible: an off-by-one in a
24-month roll-forward still draws a plausible chart. So the tests here check
arithmetic against figures worked out by hand, not against the code's own output.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.modules.simulator.engine import Outlook, compare, evaluate
from app.modules.simulator.scenarios import (
    TEMPLATES,
    Change,
    ChangeKind,
    Position,
    Scenario,
    from_template,
    months_until_shortfall,
    project,
)

TODAY = date(2026, 8, 6)
NOW = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)


def position(
    *, reserves: str = "500000", income: str = "95000", expenses: str = "65000", days: int = 365
) -> Position:
    return Position(
        liquid_reserves=Decimal(reserves),
        monthly_income=Decimal(income),
        monthly_expenses=Decimal(expenses),
        health_score=Decimal("80"),
        observation_days=days,
        window_start=date(2025, 8, 6),
        window_end=TODAY,
    )


class TestProjection:
    def test_a_one_off_is_deducted_exactly_once(self) -> None:
        """The bug this pins.

        The first version applied one-off costs at both month 0 and month 1, so
        a ₹120,000 holiday came out ₹120,000 too expensive and the trough was
        ₹90,000 low. Worked by hand: ₹500,000 − ₹120,000 + ₹30,000 = ₹410,000.
        """
        who = position()
        scenario = Scenario("Holiday", (Change(ChangeKind.ONE_OFF, "Holiday", Decimal("120000")),))

        result = project(who, scenario, today=TODAY)

        assert result.points[0].reserves == Decimal("500000.00")
        assert result.points[1].reserves == Decimal("410000.00")
        # 24 months of ₹30,000 surplus, minus the holiday.
        assert result.ending_reserves == Decimal("1100000.00")

    def test_point_zero_is_today_untouched(self) -> None:
        """Whatever the scenario, the first point is where the user is now."""
        who = position()
        scenario = from_template("income_loss", {"months": Decimal(6)}, position=who)

        result = project(who, scenario, today=TODAY)

        assert result.points[0].on == TODAY
        assert result.points[0].reserves == who.liquid_reserves

    def test_a_recurring_expense_reduces_surplus_for_its_term(self) -> None:
        who = position()
        scenario = Scenario(
            "EMI",
            (Change(ChangeKind.RECURRING_EXPENSE, "EMI", Decimal("12000"), lasts_months=6),),
            horizon_months=12,
        )

        result = project(who, scenario, today=TODAY)

        # ₹18,000 for six months, then ₹30,000 again.
        assert result.points[1].monthly_surplus == Decimal("18000.00")
        assert result.points[7].monthly_surplus == Decimal("30000.00")

    def test_a_reduction_increases_surplus(self) -> None:
        """A rent cut is an expense change that leaves more money, and the sign
        convention has to survive that."""
        who = position()
        scenario = Scenario(
            "Cheaper rent",
            (
                Change(
                    ChangeKind.RECURRING_EXPENSE,
                    "Rent cut",
                    Decimal("5000"),
                    is_reduction=True,
                ),
            ),
        )

        result = project(who, scenario, today=TODAY)
        assert result.points[1].monthly_surplus == Decimal("35000.00")

    def test_income_loss_drains_reserves_at_the_expected_rate(self) -> None:
        """By hand: surplus becomes 30,000 − 95,000 = −65,000 for six months,
        so ₹500,000 → ₹110,000."""
        who = position()
        scenario = from_template("income_loss", {"months": Decimal(6)}, position=who)

        result = project(who, scenario, today=TODAY)

        assert result.points[6].reserves == Decimal("110000.00")
        assert result.points[6].monthly_surplus == Decimal("-65000.00")

    def test_months_advance_by_the_calendar_not_by_thirty_days(self) -> None:
        """`timedelta(days=30 * n)` drifts five days a year, which over a
        24-month horizon lands the final point in the wrong month."""
        who = position()
        scenario = Scenario("Nothing", (), horizon_months=12)

        result = project(who, scenario, today=date(2026, 1, 31))

        assert result.points[1].on == date(2026, 2, 28)  # clamped, not overflowed
        assert result.points[12].on == date(2027, 1, 31)

    def test_a_shortfall_is_recorded_with_its_month(self) -> None:
        who = position(reserves="80000", income="60000", expenses="55000")
        scenario = from_template("income_loss", {"months": Decimal(6)}, position=who)

        result = project(who, scenario, today=TODAY)

        assert result.shortfall_months
        assert months_until_shortfall(result) == result.shortfall_months[0]

    def test_no_shortfall_is_none_not_a_large_number(self) -> None:
        """ "You are fine for two years" is the answer to most scenarios and
        should not be expressible as `999` for the UI to render."""
        assert (
            months_until_shortfall(project(position(), Scenario("Nothing", ()), today=TODAY))
            is None
        )


class TestOutlook:
    def test_a_comfortable_scenario_says_so(self) -> None:
        result = evaluate(
            position(),
            from_template("holiday", {"cost": Decimal("60000")}, position=position()),
            today=TODAY,
            computed_at=NOW,
        )
        assert result.outlook == Outlook.COMFORTABLE

    def test_thin_cover_is_tight_not_a_failure(self) -> None:
        """Six months without income leaves this user with under three months of
        cover — worth flagging, not a refusal."""
        who = position()
        result = evaluate(
            who,
            from_template("income_loss", {"months": Decimal(6)}, position=who),
            today=TODAY,
            computed_at=NOW,
        )
        assert result.outlook == Outlook.TIGHT
        assert result.months_until_shortfall is None

    def test_running_out_is_unsustainable(self) -> None:
        who = position(reserves="60000", income="55000", expenses="52000")
        result = evaluate(
            who,
            from_template("income_loss", {"months": Decimal(6)}, position=who),
            today=TODAY,
            computed_at=NOW,
        )
        assert result.outlook == Outlook.UNSUSTAINABLE
        assert result.months_until_shortfall is not None

    def test_a_scenario_is_never_scored(self) -> None:
        """The user is asking "what happens", not "how well did I do". Grading
        a holiday out of a hundred would invent a precision that does not exist.
        """
        result = evaluate(position(), Scenario("Nothing", ()), today=TODAY, computed_at=NOW)

        assert result.explanation.score is None
        assert result.explanation.verdict in {"COMFORTABLE", "TIGHT", "UNSUSTAINABLE"}


class TestExplanation:
    def test_every_change_appears_as_a_factor(self) -> None:
        """The user typed these in; seeing them reflected back is what makes the
        projection checkable."""
        who = position()
        scenario = from_template(
            "vehicle",
            {"deposit": Decimal("80000"), "monthly": Decimal("14000"), "months": Decimal(36)},
            position=who,
        )

        result = evaluate(who, scenario, today=TODAY, computed_at=NOW)
        names = [f.name for f in result.explanation.factors]

        assert "Down payment" in names
        assert "Monthly EMI" in names
        # Plus the two summary figures.
        assert "Lowest point" in names
        assert "Where you end up" in names

    def test_factors_carry_no_contribution(self) -> None:
        """They are inputs to a projection, not weighted parts of a score, and
        there is no score to decompose. Fabricating contributions so the panel
        looks fuller would break the contract's one invariant (ADR-002)."""
        result = evaluate(
            position(),
            from_template("holiday", {"cost": Decimal("100000")}, position=position()),
            today=TODAY,
            computed_at=NOW,
        )

        assert all(f.contribution == Decimal(0) for f in result.explanation.factors)
        assert result.explanation.total_contribution == Decimal(0)

    def test_a_shortfall_is_stated_in_the_caveats(self) -> None:
        who = position(reserves="60000", income="55000", expenses="52000")
        result = evaluate(
            who,
            from_template("income_loss", {"months": Decimal(6)}, position=who),
            today=TODAY,
            computed_at=NOW,
        )

        caveats = " ".join(result.explanation.caveats)
        assert "savings run out" in caveats
        assert "borrowing" in caveats

    def test_a_permanent_change_is_flagged_as_such(self) -> None:
        """A change modelled as forever, when the user meant "for a year", is
        the easiest way to get a wrong answer that looks right."""
        scenario = Scenario(
            "Forever",
            (Change(ChangeKind.RECURRING_EXPENSE, "Something", Decimal("5000")),),
        )
        result = evaluate(position(), scenario, today=TODAY, computed_at=NOW)

        assert any("permanent" in c for c in result.explanation.caveats)

    def test_thin_history_lowers_confidence_and_says_why(self) -> None:
        result = evaluate(position(days=60), Scenario("Nothing", ()), today=TODAY, computed_at=NOW)

        assert result.explanation.confidence < Decimal("0.2")
        assert any("still settling" in c for c in result.explanation.caveats)


class TestComparison:
    def test_it_evaluates_each_against_the_same_position(self) -> None:
        who = position()
        result = compare(
            who,
            [
                from_template("holiday", {"cost": Decimal("100000")}, position=who),
                from_template("income_loss", {"months": Decimal(6)}, position=who),
            ],
            today=TODAY,
            computed_at=NOW,
        )

        assert len(result.results) == 2
        assert all(r.before.liquid_reserves == who.liquid_reserves for r in result.results)

    def test_safest_prefers_one_that_survives(self) -> None:
        """ "Safest", not "best" — which one is best depends on what the user
        wants, and the software does not know that."""
        who = position(reserves="200000", income="70000", expenses="60000")
        result = compare(
            who,
            [
                from_template("holiday", {"cost": Decimal("30000")}, position=who),
                from_template("income_loss", {"months": Decimal(12)}, position=who),
            ],
            today=TODAY,
            computed_at=NOW,
        )

        assert result.safest is not None
        assert result.safest.months_until_shortfall is None


class TestTemplates:
    def test_every_template_builds_and_projects(self) -> None:
        who = position()
        for template in TEMPLATES:
            scenario = from_template(template.key, {}, position=who)
            result = evaluate(who, scenario, today=TODAY, computed_at=NOW)

            assert scenario.changes, f"{template.key} produced no changes"
            assert result.explanation.factors
            assert len(result.projection.points) == scenario.horizon_months + 1

    def test_a_job_change_models_the_gap_and_the_raise_separately(self) -> None:
        """Two facts, so the user sees both rather than a single net figure that
        hides a month with no income."""
        who = position()
        scenario = from_template(
            "job_change",
            {"new_income": Decimal("130000"), "gap_months": Decimal(2)},
            position=who,
        )

        labels = [c.label for c in scenario.changes]
        assert "No income during the gap" in labels
        assert "New salary" in labels

    def test_a_pay_cut_is_labelled_as_one(self) -> None:
        who = position()
        scenario = from_template(
            "job_change",
            {"new_income": Decimal("60000"), "gap_months": Decimal(0)},
            position=who,
        )

        assert scenario.changes[0].label == "Lower salary"
        assert scenario.changes[0].is_reduction
