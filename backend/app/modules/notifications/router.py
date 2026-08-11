"""Notifications HTTP layer."""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import NotFoundError
from app.modules.notifications.models import DigestFrequency
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> NotificationService:
    return NotificationService(db)


ServiceDep = Annotated[NotificationService, Depends(get_service)]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    urgency: str
    subject: str
    body: str
    link: str | None
    status: str
    delivered_at: datetime | None
    read_at: datetime | None
    created_at: datetime


class FeedOut(BaseModel):
    data: list[NotificationOut]
    unread_count: int


@router.get("", response_model=FeedOut)
async def feed(
    current: CurrentUserDep,
    service: ServiceDep,
    unread: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> FeedOut:
    rows = await service.feed(current.id, unread_only=unread, limit=limit)
    return FeedOut(
        data=[NotificationOut.model_validate(row) for row in rows],
        unread_count=await service.unread_count(current.id),
    )


class GenerateOut(BaseModel):
    detected: int
    created: int
    suppressed_by_preference: int


@router.post("/generate", response_model=GenerateOut)
async def generate(current: CurrentUserDep, service: ServiceDep) -> GenerateOut:
    """Run the rules now.

    The scheduler does this daily; the endpoint exists so the loop is
    demonstrable without waiting for Beat.
    """
    return GenerateOut.model_validate(await service.generate(current.id))


class DeliverOut(BaseModel):
    delivered: int
    held: int


@router.post("/deliver", response_model=DeliverOut)
async def deliver(current: CurrentUserDep, service: ServiceDep) -> DeliverOut:
    """Send what is due, honouring digest settings and quiet hours."""
    result = await service.deliver_pending(current.id)
    return DeliverOut(delivered=result["delivered"], held=result["held"])


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(
    notification_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> None:
    if not await service.mark_read(current.id, notification_id):
        raise NotFoundError("Notification")


@router.post("/read-all")
async def mark_all_read(current: CurrentUserDep, service: ServiceDep) -> dict[str, int]:
    return {"marked": await service.mark_all_read(current.id)}


class PreferencesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    budget_enabled: bool
    bill_enabled: bool
    renewal_enabled: bool
    goal_milestone_enabled: bool
    forecast_shortfall_enabled: bool
    price_drop_enabled: bool
    digest_frequency: str
    digest_hour: int
    quiet_from: time | None
    quiet_until: time | None


class PreferencesIn(BaseModel):
    budget_enabled: bool | None = None
    bill_enabled: bool | None = None
    renewal_enabled: bool | None = None
    goal_milestone_enabled: bool | None = None
    forecast_shortfall_enabled: bool | None = None
    price_drop_enabled: bool | None = None
    digest_frequency: DigestFrequency | None = None
    digest_hour: Annotated[int, Field(ge=0, le=23)] | None = None
    quiet_from: time | None = None
    quiet_until: time | None = None


@router.get("/preferences", response_model=PreferencesOut)
async def get_preferences(current: CurrentUserDep, service: ServiceDep) -> PreferencesOut:
    """Delivery preferences, defaulted for a user who has never set any.

    Returns the defaults without persisting them: a GET that writes is a
    surprise in a log, and the row appears when someone first changes something.
    """
    return PreferencesOut.model_validate(await service.preferences(current.id))


@router.patch("/preferences", response_model=PreferencesOut)
async def update_preferences(
    payload: PreferencesIn, current: CurrentUserDep, service: ServiceDep
) -> PreferencesOut:
    changes = payload.model_dump(exclude_unset=True)
    if "digest_frequency" in changes and changes["digest_frequency"] is not None:
        changes["digest_frequency"] = changes["digest_frequency"].value
    return PreferencesOut.model_validate(await service.update_preferences(current.id, changes))
