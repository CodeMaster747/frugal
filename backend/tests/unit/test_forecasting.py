"""Recurring detection and the three forecasting tiers.

Pure, so no database. The detector and the tiers are deliberately clock-free and
session-free precisely so this file can exercise every branch in milliseconds
and so the backtest harness can drive them from fixtures.

The tests that matter most are the ones asserting a *refusal*: that an irregular
series is not called recurring, that a habit is not called a commitment, and
that fourteen days of data produce no forecast at all.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.adapters.ports import ForecastRequest
from app.modules.forecasting import recurring
from app.modules.forecasting.tiers import (
    ABSOLUTE_MINIMUM_DAYS,
    TIER2_MINIMUM_DAYS,
    TIER3_MINIMUM_DAYS,
    EwmaSeasonal,
    RecurringProjection,
    select_tier,
)

TODAY = date(2026, 8, 5)


def occurrences(
    merchant: str,
    *,
    start: date,
    every: int,
    times: int,
    amount: str,
    jitter: list[int] | None = None,
    amounts: list[str] | None = None,
    kind: str = "expense",
) -> list[recurring.Occurrence]:
    rows: list[recurring.Occurrence] = []
    for index in range(times):
        offset = every * index + (jitter[index] if jitter else 0)
        rows.append(
            recurring.Occurrence(
                transaction_id=uuid.uuid4(),
                merchant=merchant,
                occurred_on=start + timedelta(days=offset),
                amount=Decimal(amounts[index]) if amounts else Decimal(amount),
                kind=kind,
            )
        )
    return rows


class TestRecurringDetection:
    def test_a_clean_monthly_charge_is_detected(self) -> None:
        found = recurring.detect(
            occurrences("netflix", start=date(2025, 9, 1), every=30, times=11, amount="649"),
            today=TODAY,
        )

        assert len(found) == 1
        pattern = found[0]
        assert pattern.cadence == "monthly"
        assert pattern.amount == Decimal("649.00")
        assert pattern.confidence > Decimal("0.9")
        assert pattern.amount_variance == Decimal("0")

    def test_two_occurrences_are_a_coincidence_not_a_rhythm(self) -> None:
        """One interval is indistinguishable from chance."""
        assert (
            recurring.detect(
                occurrences("gym", start=date(2026, 6, 1), every=30, times=2, amount="1500"),
                today=TODAY,
            )
            == []
        )

    def test_irregular_spacing_is_not_recurring(self) -> None:
        """Groceries every few days is a habit. Regular *enough* is not the
        same as regular, and projecting it as a scheduled event would put money
        on specific days it never leaves on."""
        assert (
            recurring.detect(
                occurrences(
                    "big bazaar",
                    start=date(2026, 1, 1),
                    every=30,
                    times=8,
                    amount="2000",
                    jitter=[0, 9, -8, 12, -11, 7, -9, 10],
                ),
                today=TODAY,
            )
            == []
        )

    def test_wildly_varying_amounts_are_a_habit_not_a_commitment(self) -> None:
        """Perfectly regular timing does not make it a commitment. Six charges
        on the 1st for wildly different sums is a card being used, not a bill."""
        assert (
            recurring.detect(
                occurrences(
                    "amazon",
                    start=date(2026, 1, 1),
                    every=30,
                    times=7,
                    amount="0",
                    amounts=["500", "8000", "1200", "15000", "300", "9500", "2200"],
                ),
                today=TODAY,
            )
            == []
        )

    def test_a_utility_bill_that_swings_seasonally_still_counts(self) -> None:
        """Electricity doubles in summer and is unquestionably a commitment.
        The amount tolerance is generous on purpose."""
        found = recurring.detect(
            occurrences(
                "bescom",
                start=date(2025, 9, 1),
                every=30,
                times=11,
                amount="0",
                amounts=[
                    "1400",
                    "1500",
                    "1600",
                    "1900",
                    "2400",
                    "2600",
                    "2300",
                    "1800",
                    "1500",
                    "1400",
                    "1450",
                ],
            ),
            today=TODAY,
        )

        assert len(found) == 1
        assert found[0].cadence == "monthly"
        # Real variance, honestly reported rather than hidden.
        assert found[0].amount_variance > Decimal("0.15")

    def test_a_late_payment_does_not_break_the_pattern(self) -> None:
        found = recurring.detect(
            occurrences(
                "landlord",
                start=date(2025, 9, 1),
                every=30,
                times=11,
                amount="18000",
                jitter=[0, 0, 2, 0, -1, 3, 0, 0, 1, 0, 2],
            ),
            today=TODAY,
        )

        assert len(found) == 1
        assert found[0].cadence == "monthly"

    def test_same_day_duplicates_do_not_destroy_the_interval(self) -> None:
        """Two coffees on one Tuesday would otherwise register a zero-day gap
        and drag the median to nonsense."""
        rows = occurrences("swiggy", start=date(2026, 1, 5), every=7, times=10, amount="400")
        rows += [
            recurring.Occurrence(
                transaction_id=uuid.uuid4(),
                merchant="swiggy",
                occurred_on=rows[3].occurred_on,
                amount=Decimal("380"),
                kind="expense",
            )
        ]
        found = recurring.detect(rows, today=TODAY)

        assert len(found) == 1
        assert found[0].cadence == "weekly"

    def test_income_and_expense_at_one_merchant_are_separate_rhythms(self) -> None:
        rows = occurrences(
            "acme", start=date(2025, 9, 1), every=30, times=11, amount="85000", kind="income"
        )
        rows += occurrences("acme", start=date(2025, 9, 15), every=30, times=11, amount="1200")
        found = recurring.detect(rows, today=TODAY)

        assert {p.kind for p in found} == {"income", "expense"}

    def test_the_next_due_date_is_never_in_the_past(self) -> None:
        """A pattern last seen four months ago must not project backwards."""
        found = recurring.detect(
            occurrences("old bill", start=date(2025, 1, 1), every=30, times=6, amount="700"),
            today=TODAY,
        )

        assert found
        assert found[0].next_due_on >= TODAY

    def test_a_cancelled_subscription_is_stale(self) -> None:
        """Without this, a subscription cancelled last spring would be projected
        into every forecast forever."""
        found = recurring.detect(
            occurrences("old gym", start=date(2025, 1, 1), every=30, times=6, amount="1500"),
            today=TODAY,
        )

        assert found
        assert recurring.is_stale(found[0], today=TODAY)

    def test_one_missed_payment_is_not_staleness(self) -> None:
        """A single late payment is common and is not evidence of cancellation."""
        found = recurring.detect(
            occurrences(
                "netflix", start=TODAY - timedelta(days=330), every=30, times=11, amount="649"
            ),
            today=TODAY,
        )

        assert found
        assert not recurring.is_stale(found[0], today=TODAY)

    def test_monthly_equivalent_normalises_across_cadences(self) -> None:
        weekly = recurring.detect(
            occurrences(
                "cleaner", start=TODAY - timedelta(days=70), every=7, times=11, amount="500"
            ),
            today=TODAY,
        )[0]

        # ~4.35 weeks a month, so ₹500/week is ~₹2,174/month.
        assert Decimal("2100") < weekly.monthly_equivalent < Decimal("2250")

    def test_due_dates_project_on_the_observed_interval(self) -> None:
        pattern = recurring.detect(
            occurrences(
                "rent", start=TODAY - timedelta(days=300), every=30, times=11, amount="18000"
            ),
            today=TODAY,
        )[0]

        due = pattern.due_dates(TODAY, TODAY + timedelta(days=90))
        assert 2 <= len(due) <= 4
        assert all(TODAY <= d <= TODAY + timedelta(days=90) for d in due)


def flat_history(days: int, *, daily: str = "-800") -> list[tuple[date, Decimal]]:
    start = TODAY - timedelta(days=days - 1)
    return [(start + timedelta(days=i), Decimal(daily)) for i in range(days)]


class TestTierSelection:
    def test_the_thresholds_are_the_documented_ones(self) -> None:
        """M7 exit criterion: correct tier at 30 / 120 / 250 days."""
        assert select_tier(30).name == "recurring_projection"
        assert select_tier(120).name == "ewma_seasonal"
        assert select_tier(250).name == "prophet"

    def test_below_the_floor_there_is_no_tier_at_all(self) -> None:
        """A 503, not a bad forecast."""
        assert select_tier(ABSOLUTE_MINIMUM_DAYS - 1) is None
        assert select_tier(0) is None

    def test_each_boundary_is_inclusive(self) -> None:
        assert select_tier(ABSOLUTE_MINIMUM_DAYS).name == "recurring_projection"
        assert select_tier(TIER2_MINIMUM_DAYS).name == "ewma_seasonal"
        assert select_tier(TIER3_MINIMUM_DAYS).name == "prophet"

    def test_the_api_can_decline_prophet_without_losing_a_tier(self) -> None:
        """The web process cannot import Prophet, so it selects tier 2 and
        queues the better answer rather than failing."""
        assert select_tier(400, allow_prophet=False).name == "ewma_seasonal"


class TestForecastShape:
    @pytest.mark.parametrize("tier", [RecurringProjection(), EwmaSeasonal()])
    def test_every_tier_returns_the_same_contract(self, tier) -> None:
        result = tier.forecast(
            ForecastRequest(
                horizon_days=60,
                opening_balance=Decimal("100000"),
                history=flat_history(120),
            )
        )

        assert result.method
        assert len(result.series) == 60
        assert Decimal(0) <= result.confidence <= Decimal(1)
        assert result.caveats, "every tier must state its limits"
        assert result.factors

    @pytest.mark.parametrize("tier", [RecurringProjection(), EwmaSeasonal()])
    def test_the_band_is_ordered_and_widens(self, tier) -> None:
        """p10 <= p50 <= p90 everywhere, and uncertainty grows with distance.
        A band that does not widen is claiming day 90 is as knowable as day 1."""
        result = tier.forecast(
            ForecastRequest(
                horizon_days=90,
                opening_balance=Decimal("100000"),
                history=flat_history(120, daily="-500"),
            )
        )

        for point in result.series:
            assert point.p10 <= point.p50 <= point.p90

        first_width = result.series[0].p90 - result.series[0].p10
        last_width = result.series[-1].p90 - result.series[-1].p10
        assert last_width > first_width

    def test_scheduled_commitments_move_the_projection(self) -> None:
        base = ForecastRequest(
            horizon_days=45,
            opening_balance=Decimal("50000"),
            history=flat_history(120, daily="0"),
        )
        with_salary = ForecastRequest(
            horizon_days=45,
            opening_balance=Decimal("50000"),
            history=flat_history(120, daily="0"),
            scheduled=[(TODAY + timedelta(days=10), Decimal("85000"))],
        )

        plain = EwmaSeasonal().forecast(base)
        salaried = EwmaSeasonal().forecast(with_salary)

        assert salaried.ending_balance - plain.ending_balance == Decimal("85000")

    def test_shortfalls_come_from_the_pessimistic_path(self) -> None:
        """p10, not p50. The useful warning is "this could happen" -- a warning
        that waits until a shortfall is more likely than not arrives too late to
        act on."""
        result = EwmaSeasonal().forecast(
            ForecastRequest(
                horizon_days=90,
                opening_balance=Decimal("20000"),
                history=flat_history(120, daily="-400"),
            )
        )

        shortfalls = result.shortfall_dates()
        assert shortfalls
        # Every flagged date is one where the pessimistic path is negative.
        flagged = {p.on: p for p in result.series}
        assert all(flagged[d].p10 < 0 for d in shortfalls)

    def test_the_trough_is_the_low_point_not_the_last_point(self) -> None:
        result = EwmaSeasonal().forecast(
            ForecastRequest(
                horizon_days=60,
                opening_balance=Decimal("100000"),
                history=flat_history(120, daily="0"),
                # A dip, then a recovery.
                scheduled=[
                    (TODAY + timedelta(days=10), Decimal("-60000")),
                    (TODAY + timedelta(days=40), Decimal("90000")),
                ],
            )
        )

        trough = result.trough
        assert trough is not None
        assert trough.p50 < result.ending_balance
        assert trough.on < result.series[-1].on

    def test_tier_one_is_openly_unconfident(self) -> None:
        """Three weeks of data must not produce a confident-looking number."""
        result = RecurringProjection().forecast(
            ForecastRequest(
                horizon_days=90,
                opening_balance=Decimal("50000"),
                history=flat_history(21),
            )
        )

        assert result.confidence <= Decimal("0.4")
        assert any("Too little data" in c for c in result.caveats)

    def test_tier_two_states_the_right_reason_for_being_tier_two(self) -> None:
        """Two different reasons land on this tier and saying the wrong one is
        worse than saying nothing: a user with 339 days told they have "below
        the 180 days needed" can see that is false."""
        short = EwmaSeasonal().forecast(
            ForecastRequest(horizon_days=30, opening_balance=Decimal("1"), history=flat_history(90))
        )
        long = EwmaSeasonal().forecast(
            ForecastRequest(
                horizon_days=30, opening_balance=Decimal("1"), history=flat_history(339)
            )
        )

        assert "below the 180 days" in short.caveats[0]
        assert "below the 180 days" not in long.caveats[0]
        assert "being prepared" in long.caveats[0]

    def test_a_weekday_pattern_narrows_the_band(self) -> None:
        """Explained variance should not be charged to uncertainty. If a strong
        Saturday effect widened the band, the model would be penalised for
        knowing something."""
        start = TODAY - timedelta(days=119)
        seasonal = [
            (
                start + timedelta(days=i),
                Decimal("-3000") if (start + timedelta(days=i)).weekday() == 5 else Decimal("-200"),
            )
            for i in range(120)
        ]

        modelled = EwmaSeasonal().forecast(
            ForecastRequest(horizon_days=30, opening_balance=Decimal("100000"), history=seasonal)
        )
        # The same total variability with no weekday structure to find.
        import random

        rng = random.Random(7)
        shuffled = [amount for _, amount in seasonal]
        rng.shuffle(shuffled)
        noise = EwmaSeasonal().forecast(
            ForecastRequest(
                horizon_days=30,
                opening_balance=Decimal("100000"),
                history=[(start + timedelta(days=i), shuffled[i]) for i in range(120)],
            )
        )

        modelled_width = modelled.series[-1].p90 - modelled.series[-1].p10
        noise_width = noise.series[-1].p90 - noise.series[-1].p10
        assert modelled_width < noise_width
