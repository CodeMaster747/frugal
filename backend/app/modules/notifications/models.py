"""Notifications and delivery preferences.

The design problem is the same one the insight engine had, one step further
along: an insight the user can ignore costs them a glance, a *notification* they
can ignore costs them an interruption. So the bar is higher and the suppression
is stricter.

Three mechanisms, each doing something the others cannot:

* **`dedup_key` under a unique constraint** — the same fact never notified
  twice, enforced by the database rather than by every rule remembering.
* **Preferences** — a category the user has switched off is never created, so
  it cannot leak through a later change to the delivery code.
* **Digest batching** — five notifications in an hour become one message in the
  morning, because the alternative is that they turn all of them off.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantMixin, TimestampMixin, UUIDMixin


class NotificationCategory(StrEnum):
    BUDGET = "budget"
    BILL = "bill"
    RENEWAL = "renewal"
    GOAL_MILESTONE = "goal_milestone"
    FORECAST_SHORTFALL = "forecast_shortfall"
    PRICE_DROP = "price_drop"


class Urgency(StrEnum):
    """How soon this needs to reach someone.

    Distinct from severity: a critical *finding* delivered a day late is still
    useful, while a bill due tomorrow is not. Urgency decides whether digest
    batching applies, and nothing else.
    """

    IMMEDIATE = "immediate"
    DAILY = "daily"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


class DigestFrequency(StrEnum):
    IMMEDIATE = "immediate"
    DAILY = "daily"
    WEEKLY = "weekly"
    OFF = "off"


class Notification(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    urgency: Mapped[str] = mapped_column(String(16), nullable=False)

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(String(500))

    #: e.g. `budget:groceries:2026-08`. Stable for the same fact in the same
    #: period, which is the whole mechanism.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=DeliveryStatus.PENDING.value
    )
    #: Set when the message actually left, as opposed to when it was created.
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Recorded when a rule fires but the user has the category off, so
    #: "why didn't I get told" is answerable.
    suppressed_reason: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_dedup"),
        Index(
            "ix_notifications_user_pending",
            "user_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_notifications_user_created", "user_id", text("created_at DESC")),
    )


class NotificationPreference(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One row per user. Absent means the defaults below apply.

    Stored rather than derived so a user's choice survives a change to the
    defaults — someone who turned budget alerts off should not find them back on
    because we changed our minds about what a sensible default is.
    """

    __tablename__ = "notification_preferences"

    budget_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    bill_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    renewal_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    goal_milestone_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    forecast_shortfall_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    price_drop_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    digest_frequency: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=DigestFrequency.DAILY.value
    )
    #: Local time the digest is sent. Defaults to 09:00, which is when a person
    #: can act on it — a 03:00 send is technically punctual and useless.
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("9"))

    #: Nothing is delivered between these times, whatever its urgency. A
    #: shortfall warning at 2am wakes someone up to worry, which helps nobody.
    quiet_from: Mapped[time | None] = mapped_column(Time)
    quiet_until: Mapped[time | None] = mapped_column(Time)

    __table_args__ = (UniqueConstraint("user_id", name="uq_notification_preferences_user"),)

    def allows(self, category: str) -> bool:
        return bool(getattr(self, f"{category}_enabled", True))


#: Applied when a user has no preference row. Everything on, batched daily —
#: the shape most likely to be useful before anyone has tuned it.
DEFAULTS: dict[str, Any] = {
    "budget_enabled": True,
    "bill_enabled": True,
    "renewal_enabled": True,
    "goal_milestone_enabled": True,
    "forecast_shortfall_enabled": True,
    "price_drop_enabled": True,
    "digest_frequency": DigestFrequency.DAILY.value,
    "digest_hour": 9,
}


def default_preference(user_id: uuid.UUID) -> NotificationPreference:
    return NotificationPreference(user_id=user_id, **DEFAULTS)
