"""Notification generation and delivery.

Gathers the state each rule needs, runs them, and persists what survives
suppression.

Order matters and is deliberate: **preferences are checked before creation, not
before delivery.** A category the user turned off produces no row at all, so a
later change to the delivery path cannot leak it. The one exception is recorded
explicitly — a rule that fires against a disabled category writes a
`suppressed` row when the user has asked to see what they are missing, which
makes "why didn't I get told" answerable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.notify.channels import InAppNotifier
from app.adapters.ports import Message, Notifier
from app.core.clock import utc_now, utc_today
from app.core.logging import get_logger
from app.modules.analytics.service import AnalyticsService
from app.modules.notifications import rules
from app.modules.notifications.models import (
    DeliveryStatus,
    DigestFrequency,
    Notification,
    NotificationPreference,
    Urgency,
    default_preference,
)

logger = get_logger(__name__)

ZERO = Decimal("0")


class NotificationService:
    def __init__(self, session: AsyncSession, *, notifier: Notifier | None = None) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)
        self.notifier: Notifier = notifier or InAppNotifier()

    # -- preferences -------------------------------------------------------

    async def preferences(self, user_id: uuid.UUID) -> NotificationPreference:
        """This user's preferences, defaulted but not persisted on read.

        A GET that writes is a surprise in a log and a write amplification on a
        read path. The row appears the first time someone changes something.
        """
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        return existing or default_preference(user_id)

    async def update_preferences(
        self, user_id: uuid.UUID, changes: dict[str, object]
    ) -> NotificationPreference:
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()

        if row is None:
            row = default_preference(user_id)
            self.session.add(row)

        for field, value in changes.items():
            if value is not None and hasattr(row, field):
                setattr(row, field, value)

        await self.session.flush()
        return row

    # -- generation --------------------------------------------------------

    async def generate(self, user_id: uuid.UUID) -> dict[str, int]:
        """Run every rule and persist what is new and wanted."""
        preference = await self.preferences(user_id)
        candidates = await self._collect(user_id)

        created = suppressed = 0
        for candidate in candidates:
            if not preference.allows(candidate.category.value):
                suppressed += 1
                continue
            if await self._persist(user_id, candidate):
                created += 1

        return {
            "detected": len(candidates),
            "created": created,
            "suppressed_by_preference": suppressed,
        }

    async def _collect(self, user_id: uuid.UUID) -> list[rules.Candidate]:
        today = utc_today()
        candidates: list[rules.Candidate] = []

        candidates.extend(rules.budget_alerts(await self._budget_states(user_id, today)))

        upcoming = await self._upcoming_items(user_id, today)
        candidates.extend(rules.bill_reminders(upcoming, today=today))
        candidates.extend(rules.renewal_reminders(upcoming, today=today))

        candidates.extend(rules.goal_milestones(await self._goal_states(user_id)))
        candidates.extend(await self._forecast_candidates(user_id, today))
        candidates.extend(await self._price_drop_candidates(user_id))

        return candidates

    async def _budget_states(self, user_id: uuid.UUID, today: date) -> list[rules.BudgetState]:
        """Budgets for the *current* period, with what has been spent so far.

        `budget_outcomes` in analytics deliberately returns only closed periods,
        which is right for scoring discipline and wrong here: a notification
        about a budget is only useful while there is still time to act on it.
        """
        outcomes = await self.analytics.open_budget_progress(user_id, today)
        return [
            rules.BudgetState(
                category_name=row.category_name,
                category_slug=row.category_slug,
                limit=row.limit,
                spent=row.spent,
                period_label=row.period_label,
                days_left=row.days_left,
            )
            for row in outcomes
        ]

    async def _upcoming_items(self, user_id: uuid.UUID, today: date) -> list[rules.UpcomingItem]:
        rows = await self.analytics.recurring_items(user_id)
        return [
            rules.UpcomingItem(
                item_id=row.item_id,
                name=row.name.title(),
                amount=row.amount,
                due_on=rules.next_due(row.next_due_on or today, row.cadence, today=today),
                item_type=row.item_type,
                cadence=row.cadence,
            )
            for row in rows
            if row.kind != "income"
        ]

    async def _goal_states(self, user_id: uuid.UUID) -> list[rules.GoalState]:
        return [
            rules.GoalState(
                goal_id=row.goal_id,
                name=row.name,
                target_amount=row.target_amount,
                current_amount=row.current_amount,
            )
            for row in await self.analytics.goal_progress(user_id)
        ]

    async def _forecast_candidates(self, user_id: uuid.UUID, today: date) -> list[rules.Candidate]:
        from app.modules.forecasting.service import (
            ForecastService,
            InsufficientHistoryError,
        )

        try:
            result, _, _ = await ForecastService(self.session).forecast(
                user_id, horizon_days=90, allow_prophet=False
            )
        except InsufficientHistoryError:
            # No forecast is not a shortfall. Silence is correct here.
            return []

        trough = result.trough
        return rules.forecast_shortfall(
            shortfall_dates=result.shortfall_dates(),
            trough_amount=trough.p50 if trough else None,
            trough_on=trough.on if trough else None,
            today=today,
        )

    async def _price_drop_candidates(self, user_id: uuid.UUID) -> list[rules.Candidate]:
        """Built from M9's alerts rather than re-detecting.

        M9 already decided what counts as a drop worth raising; a second
        threshold here would be one more thing to keep in step.
        """
        from app.modules.market.models import PriceAlert, Product

        stmt = (
            select(PriceAlert, Product.canonical_name)
            .join(Product, Product.id == PriceAlert.product_id)
            .where(PriceAlert.user_id == user_id, PriceAlert.read_at.is_(None))
            .order_by(PriceAlert.created_at.desc())
            .limit(10)
        )
        return [
            rules.price_drop(
                product_name=name,
                product_id=alert.product_id,
                previous=Decimal(alert.previous_price),
                now=Decimal(alert.new_price),
                seller=alert.seller_name,
                is_lowest=alert.is_lowest_recorded,
                on=alert.created_at.date(),
            )
            for alert, name in (await self.session.execute(stmt)).all()
        ]

    async def _persist(self, user_id: uuid.UUID, candidate: rules.Candidate) -> bool:
        """Insert, or do nothing if this fact was already notified.

        `ON CONFLICT DO NOTHING` rather than check-then-insert: two concurrent
        runs would both see nothing and both insert, and the unique constraint
        would turn the loser into a 500 for something working as designed.
        """
        stmt = (
            insert(Notification)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                category=candidate.category.value,
                urgency=candidate.urgency.value,
                subject=candidate.subject,
                body=candidate.body,
                link=candidate.link,
                dedup_key=candidate.dedup_key,
                status=DeliveryStatus.PENDING.value,
            )
            .on_conflict_do_nothing(constraint="uq_notifications_dedup")
            .returning(Notification.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    # -- delivery ----------------------------------------------------------

    async def deliver_pending(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Send what is due, honouring digest settings and quiet hours.

        Immediate-urgency messages bypass batching but not quiet hours: a
        shortfall warning is worth interrupting a morning for and not worth
        waking someone at 2am, and those are different judgements.
        """
        preference = await self.preferences(user_id)
        now = utc_now()

        if preference.digest_frequency == DigestFrequency.OFF.value:
            return {"delivered": 0, "held": 0, "reason_held": "digest off"}

        pending = await self._pending(user_id)
        if not pending:
            return {"delivered": 0, "held": 0}

        in_quiet = _in_quiet_hours(preference, now)
        delivered = held = 0

        for notification in pending:
            immediate = notification.urgency == Urgency.IMMEDIATE.value
            due = immediate or _digest_due(preference, now)

            if in_quiet or not due:
                held += 1
                continue

            ok = await self.notifier.send(
                user_id,
                Message(
                    subject=notification.subject,
                    body=notification.body,
                    category=notification.category,
                    link=notification.link,
                ),
            )
            notification.status = DeliveryStatus.SENT.value if ok else DeliveryStatus.FAILED.value
            notification.delivered_at = now if ok else None
            delivered += int(ok)

        await self.session.flush()
        return {"delivered": delivered, "held": held}

    async def _pending(self, user_id: uuid.UUID) -> Sequence[Notification]:
        stmt = (
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.status == DeliveryStatus.PENDING.value,
            )
            .order_by(Notification.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    # -- reading -----------------------------------------------------------

    async def feed(
        self, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 30
    ) -> Sequence[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.read_at.is_(None))
        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def unread_count(self, user_id: uuid.UUID) -> int:
        rows = await self.session.execute(
            select(Notification.id).where(
                Notification.user_id == user_id, Notification.read_at.is_(None)
            )
        )
        return len(rows.all())

    async def mark_read(self, user_id: uuid.UUID, notification_id: uuid.UUID) -> bool:
        stmt = (
            update(Notification)
            .where(Notification.user_id == user_id, Notification.id == notification_id)
            .values(read_at=utc_now())
            .returning(Notification.id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=utc_now())
            .returning(Notification.id)
        )
        return len((await self.session.execute(stmt)).all())


def _in_quiet_hours(preference: NotificationPreference, now: datetime) -> bool:
    """Whether now falls inside the user's quiet window.

    Handles a window crossing midnight (22:00–07:00), which is the shape most
    people actually want and the one a naive `from <= t <= until` gets wrong.
    """
    if preference.quiet_from is None or preference.quiet_until is None:
        return False

    current = now.time()
    start, end = preference.quiet_from, preference.quiet_until

    if start <= end:
        return bool(start <= current < end)
    return bool(current >= start or current < end)


def _digest_due(preference: NotificationPreference, now: datetime) -> bool:
    """Whether a batched message should go out at this moment."""
    frequency = preference.digest_frequency

    if frequency == DigestFrequency.IMMEDIATE.value:
        return True
    if frequency == DigestFrequency.DAILY.value:
        return bool(now.hour >= preference.digest_hour)
    if frequency == DigestFrequency.WEEKLY.value:
        # Monday, at or after the digest hour.
        return bool(now.weekday() == 0 and now.hour >= preference.digest_hour)
    return False
