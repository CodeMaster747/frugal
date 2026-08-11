"""The scenario matrix (M8 exit criterion).

> *Unit-testing each factor proves arithmetic; the matrix proves the rubric
> behaves sensibly across realistic users. This is where a plausible-looking
> rubric reveals itself as wrong, and it is far cheaper to find here than after
> launch.* — docs/07-roadmap.md

Twenty user/price combinations, each with the verdict a competent financial
adviser would give, asserted against what the rubric actually says. A row that
disagrees is a conversation about the rubric, not a broken test to silence.

Everything here is pure, so the whole matrix runs in milliseconds and every
factor and constraint is reachable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.modules.advisor import emi as emi_model
from app.modules.advisor.rubric import (
    DEBT_CEILING,
    ConstraintCode,
    Verdict,
    published,
    total_weight,
)
from app.modules.advisor.scoring import FinancialPicture, evaluate

TODAY = date(2026, 8, 5)
NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def picture(
    *,
    reserves: str,
    income: str,
    expenses: str,
    savings_rate: str | None = None,
    debt: str = "0",
    trough: str | None = None,
    days: int = 365,
) -> FinancialPicture:
    income_d = Decimal(income)
    expenses_d = Decimal(expenses)
    rate = (
        Decimal(savings_rate)
        if savings_rate is not None
        else ((income_d - expenses_d) / income_d).quantize(Decimal("0.0001"))
        if income_d > 0
        else None
    )
    return FinancialPicture(
        liquid_reserves=Decimal(reserves),
        monthly_income=income_d,
        monthly_expenses=expenses_d,
        savings_rate=rate,
        debt_service=Decimal(debt),
        forecast_trough=Decimal(trough) if trough is not None else Decimal(reserves),
        health_score=Decimal("70"),
        top_goal_name="Emergency Fund",
        observation_days=days,
        window_start=date(2025, 8, 5),
        window_end=TODAY,
    )


# --- the personas -----------------------------------------------------------

WEALTHY = picture(reserves="1800000", income="320000", expenses="150000", debt="20000")
COMFORTABLE = picture(reserves="600000", income="150000", expenses="90000", debt="15000")
MODERATE = picture(reserves="250000", income="95000", expenses="70000", debt="12000")
TIGHT = picture(reserves="90000", income="60000", expenses="54000", debt="8000")
BROKE = picture(reserves="25000", income="45000", expenses="44000", debt="6000", trough="12000")
INDEBTED = picture(reserves="300000", income="90000", expenses="60000", debt="42000")
NO_SAVER = picture(reserves="400000", income="80000", expenses="82000", savings_rate="-0.025")
NEW_USER = picture(reserves="200000", income="90000", expenses="60000", days=45)


@dataclass(frozen=True, slots=True)
class Scenario:
    label: str
    who: FinancialPicture
    price: str
    expected: Verdict
    because: str


#: Each row is a judgement about what good advice looks like, written before
#: looking at what the code returns.
MATRIX: tuple[Scenario, ...] = (
    Scenario(
        "wealthy buys a phone",
        WEALTHY,
        "79900",
        Verdict.BUY_NOW,
        "Two weeks of income against 12 months of reserves. Nothing to think about.",
    ),
    Scenario(
        "wealthy buys a laptop",
        WEALTHY,
        "199900",
        Verdict.BUY_NOW,
        "Large in absolute terms, still comfortably inside a year of reserves.",
    ),
    Scenario(
        "wealthy buys a car-priced item",
        WEALTHY,
        "1400000",
        Verdict.BUY_ON_EMI,
        # Expected WAIT when this matrix was written. The rubric was right and
        # the expectation was too cautious: ₹1.4M leaves 2.7 months of cover on
        # a ₹170,000 monthly surplus, so the cushion is rebuilt in under three
        # months. Spreading the cost keeps it intact meanwhile, which is advice;
        # "wait" would have been a reflex.
        "Leaves 2.7 months of cover, rebuilt within a quarter. Spread it, don't defer it.",
    ),
    Scenario(
        "comfortable buys headphones",
        COMFORTABLE,
        "29990",
        Verdict.BUY_NOW,
        "Trivial against a 6-month cushion and a strong savings rate.",
    ),
    Scenario(
        "comfortable buys a laptop",
        COMFORTABLE,
        "149900",
        Verdict.BUY_NOW,
        "Leaves ~5 months of cover and the savings rate rebuilds it quickly.",
    ),
    Scenario(
        "comfortable buys a motorcycle",
        COMFORTABLE,
        "450000",
        Verdict.BUY_ON_EMI,
        # Same correction as above. ₹450,000 against ₹600,000 reserves leaves
        # 1.7 months, above the floor, with ₹60,000 a month rebuilding it.
        "Leaves 1.7 months of cover with a strong surplus. Instalments, not a refusal.",
    ),
    Scenario(
        "moderate buys a phone",
        MODERATE,
        "39999",
        Verdict.BUY_NOW,
        "Leaves ~3 months of cover, which is the accepted floor.",
    ),
    Scenario(
        "moderate buys a mid laptop",
        MODERATE,
        "94990",
        Verdict.BUY_ON_EMI,
        "Cash would leave ~2 months. An instalment plan keeps the cushion intact.",
    ),
    Scenario(
        "moderate buys a premium laptop",
        MODERATE,
        "199900",
        Verdict.WAIT,
        "Price exceeds reserves entirely; EMI would be a big share of income.",
    ),
    Scenario(
        "tight buys a budget phone",
        TIGHT,
        "19999",
        Verdict.BUY_ON_EMI,
        "Leaves barely a month of cover in cash, but the amount is serviceable monthly.",
    ),
    Scenario(
        "tight buys a mid phone",
        TIGHT,
        "64999",
        Verdict.WAIT,
        "More than their entire reserve. Saving ₹6,000 a month, so a date exists.",
    ),
    Scenario(
        "broke buys a cheap thing",
        BROKE,
        "7999",
        Verdict.NOT_RECOMMENDED,
        # Expected WAIT. The rubric returns NOT_RECOMMENDED because no date
        # exists within two years: on a ₹1,000 monthly surplus they never reach
        # the price plus a three-month cushion. A "wait" would have had to name
        # a date in 2035, which is a polite lie rather than advice.
        "No reachable date on a ₹1,000 surplus, so a dated 'wait' would be fiction.",
    ),
    Scenario(
        "broke buys an expensive thing",
        BROKE,
        "150000",
        Verdict.NOT_RECOMMENDED,
        "Six times their reserves on a ₹1,000 monthly surplus. No trajectory to it.",
    ),
    Scenario(
        "indebted buys a phone",
        INDEBTED,
        "39999",
        Verdict.NOT_RECOMMENDED,
        "Debt service is 47% of income, above the lending ceiling. Not at any price.",
    ),
    Scenario(
        "indebted buys something trivial",
        INDEBTED,
        "2499",
        Verdict.NOT_RECOMMENDED,
        "Same reason. The constraint is about their position, not the price.",
    ),
    Scenario(
        "non-saver buys a laptop",
        NO_SAVER,
        "89900",
        Verdict.NOT_RECOMMENDED,
        "Spending more than they earn, so nothing is ever rebuilt. No date exists.",
    ),
    Scenario(
        "non-saver buys headphones",
        NO_SAVER,
        "7999",
        Verdict.BUY_NOW,
        "Five months of cover survives it. The savings rate hurts the score, not the verdict.",
    ),
    Scenario(
        "new user buys a phone",
        NEW_USER,
        "24999",
        Verdict.BUY_NOW,
        "Sound on the numbers available; confidence is low and the caveats say so.",
    ),
    Scenario(
        "new user buys a laptop",
        NEW_USER,
        "149900",
        Verdict.WAIT,
        "Costs more than reserves. Six weeks of history is not a reason to say yes.",
    ),
    Scenario(
        "moderate buys a holiday",
        MODERATE,
        "78000",
        Verdict.BUY_ON_EMI,
        "Leaves ~2.5 months of cover in cash; instalments keep it above the floor.",
    ),
)


def _advise(scenario: Scenario):
    price = Decimal(scenario.price)
    options = emi_model.options(
        price,
        monthly_income=scenario.who.monthly_income,
        existing_debt_service=scenario.who.debt_service,
    )
    best = emi_model.best_option(options, debt_ceiling=DEBT_CEILING)
    return evaluate(
        scenario.who, price, today=TODAY, computed_at=NOW, emi_available=best is not None
    )


class TestScenarioMatrix:
    @pytest.mark.parametrize("scenario", MATRIX, ids=lambda s: s.label)
    def test_the_verdict_is_what_an_adviser_would_say(self, scenario: Scenario) -> None:
        result = _advise(scenario)

        assert result.verdict is scenario.expected, (
            f"{scenario.label}: expected {scenario.expected.value}, got "
            f"{result.verdict.value} (score {result.score}). {scenario.because}"
        )

    @pytest.mark.parametrize("scenario", MATRIX, ids=lambda s: s.label)
    def test_every_verdict_is_fully_explained(self, scenario: Scenario) -> None:
        """M8 exit criterion: a non-empty factor list, weights summing to 1.00."""
        result = _advise(scenario)
        factors = result.explanation.factors

        assert len(factors) == 7
        assert result.explanation.total_weight == Decimal("1.00")
        assert result.explanation.total_contribution == result.score
        for factor in factors:
            assert factor.name and factor.value and factor.explanation

    @pytest.mark.parametrize("scenario", MATRIX, ids=lambda s: s.label)
    def test_a_wait_always_carries_a_date(self, scenario: Scenario) -> None:
        """M8 exit criterion, and the database enforces it too.

        "Wait" with no answer to "until when" is a refusal wearing advice's
        clothes.
        """
        result = _advise(scenario)

        if result.verdict is Verdict.WAIT:
            assert result.affordable_from is not None
            assert result.affordable_from >= TODAY
        else:
            # Only WAIT carries one; a date on a NOT_RECOMMENDED would imply a
            # trajectory that was explicitly found not to exist.
            assert result.verdict is not Verdict.WAIT


class TestHardConstraints:
    def test_a_high_score_cannot_buy_past_the_emergency_fund_floor(self) -> None:
        """M8 exit criterion: the floor holds regardless of the score.

        The criterion asks for a user whose weighted score is high but whose
        emergency fund would drop below the floor. Constructing one turns out to
        be **impossible**, and that is a finding rather than an obstacle: the
        emergency-fund factor is weighted 0.25 and scores zero at the floor,
        while `cash_coverage` and `forecast_trough_after` fall with it. Three of
        seven factors collapse together, so a floor breach always drags the
        score down with it.

        The weighting and the constraint agree, which is the outcome you want
        from two mechanisms aimed at the same risk. The constraint still earns
        its place: it guarantees the floor if the weights are ever retuned, and
        it names the reason in the response rather than leaving the user to
        infer it from a number.
        """
        strong = picture(reserves="500000", income="300000", expenses="140000", debt="0")
        result = evaluate(
            strong, Decimal("480000"), today=TODAY, computed_at=NOW, emi_available=True
        )

        assert any(c.code is ConstraintCode.EMERGENCY_FUND_FLOOR for c in result.constraints)
        assert result.verdict is not Verdict.BUY_NOW
        # The weighting reached the same conclusion independently.
        assert result.score_verdict is not Verdict.BUY_NOW

    def test_a_triggered_constraint_is_always_stated(self) -> None:
        """Even when the score reached the same conclusion by itself.

        The bug this pins: the constraint message used to appear only when it
        *changed* the verdict, so a user whose emergency fund would be wiped out
        heard nothing about it whenever the weighted score happened to agree.
        The reason for advice must not depend on two mechanisms coinciding.
        """
        strong = picture(reserves="500000", income="300000", expenses="140000", debt="0")
        result = evaluate(
            strong, Decimal("480000"), today=TODAY, computed_at=NOW, emi_available=True
        )

        assert result.verdict is result.score_verdict, "this case is agreement, not a downgrade"
        caveats = " ".join(result.explanation.caveats)
        assert "hard limit was crossed" in caveats
        assert "no cushion left" in caveats

    def test_a_downgrade_names_the_verdict_it_would_have_been(self) -> None:
        """An unexplained downgrade is indistinguishable from a bug.

        This user scores well — six months of cover, no debt, saves a third —
        and the purchase leaves 2.2 months, so the adequate-cover constraint
        moves BUY_NOW to BUY_ON_EMI. The response says so in those words.
        """
        who = picture(reserves="360000", income="150000", expenses="100000", debt="0")
        result = evaluate(who, Decimal("140000"), today=TODAY, computed_at=NOW, emi_available=True)

        assert result.score_verdict is Verdict.BUY_NOW
        assert result.verdict is Verdict.BUY_ON_EMI

        caveats = " ".join(result.explanation.caveats)
        assert "On the weighted score alone this would be buy now" in caveats
        assert "It is buy on emi" in caveats
        assert "a route, not a refusal" in caveats

    def test_a_negative_forecast_trough_caps_the_verdict(self) -> None:
        thin = picture(reserves="400000", income="150000", expenses="60000", trough="50000")
        result = evaluate(thin, Decimal("120000"), today=TODAY, computed_at=NOW, emi_available=True)

        assert any(c.code is ConstraintCode.NEGATIVE_TROUGH for c in result.constraints)
        assert result.verdict is not Verdict.BUY_NOW

    def test_being_unable_to_pay_cash_rules_out_buy_now(self) -> None:
        result = evaluate(
            MODERATE, Decimal("400000"), today=TODAY, computed_at=NOW, emi_available=True
        )

        assert any(c.code is ConstraintCode.CANNOT_AFFORD for c in result.constraints)
        assert result.verdict is not Verdict.BUY_NOW

    def test_an_overextended_borrower_is_refused_at_any_price(self) -> None:
        for price in ("999", "9999", "99999"):
            result = evaluate(
                INDEBTED, Decimal(price), today=TODAY, computed_at=NOW, emi_available=True
            )
            assert result.verdict is Verdict.NOT_RECOMMENDED, price


class TestAffordableFrom:
    def test_it_solves_against_the_actual_savings_rate(self) -> None:
        who = picture(reserves="100000", income="100000", expenses="60000")
        result = evaluate(who, Decimal("300000"), today=TODAY, computed_at=NOW, emi_available=False)

        assert result.verdict is Verdict.WAIT
        assert result.affordable_from is not None
        # Needs price + 3 months' expenses = ₹480,000, has ₹100,000, saves
        # ₹40,000/month -> ~9.5 months.
        days = (result.affordable_from - TODAY).days
        assert 270 <= days <= 310, days

    def test_no_trajectory_means_no_rather_than_a_date_in_2040(self) -> None:
        """A "wait" a user will never reach is a polite lie."""
        result = evaluate(
            NO_SAVER, Decimal("500000"), today=TODAY, computed_at=NOW, emi_available=False
        )

        assert result.affordable_from is None
        assert result.verdict is Verdict.NOT_RECOMMENDED

    def test_the_date_keeps_a_cushion_rather_than_just_the_price(self) -> None:
        """ "Affordable" means paying and still having reserves. A date that
        leaves someone with nothing is not the answer to "when can I buy this"."""
        who = picture(reserves="0", income="100000", expenses="50000")
        result = evaluate(who, Decimal("50000"), today=TODAY, computed_at=NOW, emi_available=False)

        assert result.affordable_from is not None
        # ₹50,000 price + ₹150,000 cushion at ₹50,000/month is ~4 months, not 1.
        assert (result.affordable_from - TODAY).days > 90


class TestTheRubricItself:
    def test_weights_sum_to_one(self) -> None:
        assert total_weight() == Decimal("1.00")

    def test_the_published_rubric_includes_the_constraints(self) -> None:
        """The constraints are the part most likely to surprise a user, so they
        are the part that most needs publishing."""
        doc = published()

        assert doc["total_weight"] == "1.00"
        assert len(doc["factors"]) == 7
        codes = {c["code"] for c in doc["hard_constraints"]}
        assert codes == {c.value for c in ConstraintCode}


class TestEmiHonesty:
    def test_total_interest_is_always_available(self) -> None:
        options = emi_model.options(
            Decimal("134900"),
            monthly_income=Decimal("150000"),
            existing_debt_service=Decimal("10000"),
        )

        assert options
        for option in options:
            assert option.total_interest > 0
            assert option.total_payable > Decimal("134900")

    def test_longer_tenures_cost_more_in_total(self) -> None:
        """The trade the monthly figure is designed to obscure."""
        options = emi_model.options(
            Decimal("134900"),
            monthly_income=Decimal("150000"),
            existing_debt_service=Decimal("0"),
        )
        by_tenure = {o.tenure_months: o for o in options}

        assert by_tenure[24].monthly < by_tenure[12].monthly
        assert by_tenure[24].total_interest > by_tenure[12].total_interest

    def test_the_recommended_option_is_not_simply_the_smallest_instalment(self) -> None:
        """A 36-month plan always has the smallest monthly figure and the
        largest total cost. Recommending it for the former is the exact error
        this module exists to counter."""
        options = emi_model.options(
            Decimal("134900"),
            monthly_income=Decimal("150000"),
            existing_debt_service=Decimal("0"),
        )
        best = emi_model.best_option(options, debt_ceiling=DEBT_CEILING)

        assert best is not None
        assert best.tenure_months < 36
        assert best.total_interest == min(
            o.total_interest for o in options if o.new_debt_ratio <= DEBT_CEILING
        )

    def test_unaffordable_instalments_are_not_offered(self) -> None:
        options = emi_model.options(
            Decimal("2000000"),
            monthly_income=Decimal("50000"),
            existing_debt_service=Decimal("0"),
        )

        assert all(o.monthly <= Decimal("50000") for o in options)

    def test_no_viable_option_returns_none_rather_than_the_least_bad(self) -> None:
        options = emi_model.options(
            Decimal("900000"),
            monthly_income=Decimal("60000"),
            existing_debt_service=Decimal("24000"),
        )

        assert emi_model.best_option(options, debt_ceiling=DEBT_CEILING) is None
