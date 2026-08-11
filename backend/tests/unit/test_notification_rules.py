"""Notification rules, and the ways they are meant to stay silent.

Pure, so no database.

**Most of these assert an absence.** An insight the user ignores costs a glance;
a notification they ignore costs an interruption, and the second one gets turned
off. So the thresholds here are less sensitive than the equivalent insight
detectors on purpose, and the tests that matter are the ones proving a rule does
*not* fire.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.modules.notifications import rules
from app.modules.notifications.models import NotificationCategory, Urgency

TODAY = date(2026, 8, 6)


def budget(*, limit: str, spent: str, days_left: int = 20) -> rules.BudgetState:
    return rules.BudgetState(
        category_name="Groceries",
        category_slug="groceries",
        limit=Decimal(limit),
        spent=Decimal(spent),
        period_label="2026-08",
        days_left=days_left,
    )


def item(
    *, item_type: str = "emi", due_in: int = 2, amount: str = "12000", cadence: str = "monthly"
) -> rules.UpcomingItem:
    return rules.UpcomingItem(
        item_id=uuid.uuid4(),
        name="Auto Loan EMI",
        amount=Decimal(amount),
        due_on=TODAY + timedelta(days=due_in),
        item_type=item_type,
        cadence=cadence,
    )


class TestBudgetAlerts:
    def test_a_budget_nearing_its_limit_is_flagged(self) -> None:
        found = rules.budget_alerts([budget(limit="10000", spent="9000")])

        assert len(found) == 1
        assert found[0].category is NotificationCategory.BUDGET
        assert "90%" in found[0].subject

    def test_a_budget_comfortably_within_its_limit_is_silent(self) -> None:
        assert rules.budget_alerts([budget(limit="10000", spent="4000")]) == []

    def test_a_finished_period_is_not_notified(self) -> None:
        """Telling someone on the 1st that they overspent last month is a
        report. Reports belong in the insight feed; a notification is for
        something still actionable."""
        assert rules.budget_alerts([budget(limit="10000", spent="14000", days_left=0)]) == []

    def test_going_over_is_worded_differently_from_approaching(self) -> None:
        approaching = rules.budget_alerts([budget(limit="10000", spent="8800")])[0]
        over = rules.budget_alerts([budget(limit="10000", spent="12000")])[0]

        assert "spent" in approaching.subject
        assert "over by" in over.subject
        assert "leaves" in approaching.body

    def test_the_dedup_key_separates_warning_from_breach(self) -> None:
        """Crossing 85% and later crossing 100% are two different facts, and a
        user who was warned should still be told when it actually happens."""
        approaching = rules.budget_alerts([budget(limit="10000", spent="8800")])[0]
        over = rules.budget_alerts([budget(limit="10000", spent="12000")])[0]

        assert approaching.dedup_key != over.dedup_key

    def test_a_zero_limit_does_not_divide_by_zero(self) -> None:
        assert rules.budget_alerts([budget(limit="0", spent="500")]) == []


class TestBillsAndRenewals:
    def test_a_bill_due_soon_is_announced(self) -> None:
        found = rules.bill_reminders([item(due_in=2)], today=TODAY)

        assert len(found) == 1
        assert found[0].category is NotificationCategory.BILL
        assert "2 days" in found[0].subject

    def test_a_bill_far_off_is_silent(self) -> None:
        assert rules.bill_reminders([item(due_in=20)], today=TODAY) == []

    def test_a_bill_already_past_is_silent(self) -> None:
        assert rules.bill_reminders([item(due_in=-3)], today=TODAY) == []

    def test_subscriptions_are_not_treated_as_bills(self) -> None:
        """They get their own rule with a week's notice, because the useful
        action is cancelling and cancelling takes longer than paying."""
        assert rules.bill_reminders([item(item_type="subscription", due_in=2)], today=TODAY) == []

    def test_a_renewal_gets_more_notice_than_a_bill(self) -> None:
        subscription = item(item_type="subscription", due_in=6, amount="649")

        assert rules.renewal_reminders([subscription], today=TODAY)
        # The same lead time would have been too late for a bill.
        assert rules.bill_reminders([item(due_in=6)], today=TODAY) == []

    def test_a_renewal_states_the_annual_cost(self) -> None:
        """₹649 a month is easy to wave through; ₹7,788 a year is the figure
        worth deciding on."""
        found = rules.renewal_reminders(
            [item(item_type="subscription", due_in=3, amount="649")], today=TODAY
        )

        assert "7,788" in found[0].body
        assert "cancelling" in found[0].body


class TestGoalMilestones:
    def goal(self, current: str, target: str = "100000") -> rules.GoalState:
        return rules.GoalState(
            goal_id=uuid.uuid4(),
            name="Emergency Fund",
            target_amount=Decimal(target),
            current_amount=Decimal(current),
        )

    def test_a_quarter_mark_is_announced(self) -> None:
        found = rules.goal_milestones([self.goal("52000")])

        assert len(found) == 1
        assert "50%" in found[0].subject

    def test_only_the_highest_milestone_reached_is_announced(self) -> None:
        """Crossing three at once should not send three notifications."""
        found = rules.goal_milestones([self.goal("78000")])

        assert len(found) == 1
        assert "75%" in found[0].subject

    def test_progress_below_the_first_mark_is_silent(self) -> None:
        assert rules.goal_milestones([self.goal("10000")]) == []

    def test_completion_is_worded_as_an_achievement(self) -> None:
        found = rules.goal_milestones([self.goal("100000")])

        assert "reached your" in found[0].subject
        assert "Keep going" not in found[0].body

    def test_the_dedup_key_includes_the_milestone(self) -> None:
        """Progress that wobbles across a line must not re-announce."""
        goal = self.goal("52000")
        first = rules.goal_milestones([goal])[0]
        again = rules.goal_milestones([goal])[0]

        assert first.dedup_key == again.dedup_key
        assert "0.5" in first.dedup_key


class TestForecastShortfall:
    def test_a_projected_shortfall_is_immediate(self) -> None:
        """The only immediate rule here. Everything else can wait for the
        morning digest; money running out is where a day costs a returned
        payment."""
        found = rules.forecast_shortfall(
            shortfall_dates=[TODAY + timedelta(days=20)],
            trough_amount=Decimal("-4200"),
            trough_on=TODAY + timedelta(days=25),
            today=TODAY,
        )

        assert len(found) == 1
        assert found[0].urgency is Urgency.IMMEDIATE
        assert "20 days" in found[0].subject

    def test_no_shortfall_is_silent(self) -> None:
        assert (
            rules.forecast_shortfall(
                shortfall_dates=[],
                trough_amount=Decimal("50000"),
                trough_on=TODAY,
                today=TODAY,
            )
            == []
        )

    def test_it_says_this_is_the_pessimistic_path(self) -> None:
        """The warning comes from p10, not the median. Presenting it as the
        expected outcome would be alarming and wrong."""
        found = rules.forecast_shortfall(
            shortfall_dates=[TODAY + timedelta(days=10)],
            trough_amount=Decimal("-1000"),
            trough_on=TODAY,
            today=TODAY,
        )

        assert "pessimistic edge" in found[0].body

    def test_the_key_is_the_month_so_a_shifting_date_does_not_renotify(self) -> None:
        """A projection moves by a day or two as transactions land. Keying on
        the exact date would notify on every shift."""
        first = rules.forecast_shortfall(
            shortfall_dates=[date(2026, 9, 12)], trough_amount=None, trough_on=None, today=TODAY
        )[0]
        shifted = rules.forecast_shortfall(
            shortfall_dates=[date(2026, 9, 15)], trough_amount=None, trough_on=None, today=TODAY
        )[0]

        assert first.dedup_key == shifted.dedup_key


class TestNextDue:
    def test_a_stale_due_date_rolls_forward(self) -> None:
        """A recurring item whose stored date has slipped into the past would
        otherwise never remind again."""
        due = rules.next_due(date(2026, 1, 15), "monthly", today=TODAY)

        assert due >= TODAY

    def test_a_future_date_is_left_alone(self) -> None:
        future = TODAY + timedelta(days=10)
        assert rules.next_due(future, "monthly", today=TODAY) == future
