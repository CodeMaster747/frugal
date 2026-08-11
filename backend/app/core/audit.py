"""Append-only audit log.

Core infrastructure rather than a domain module: authentication, financial
mutations, and account deletion all write to it, and a shared table that every
module can reach without importing each other is exactly what core is for.

The `user_id` column carries no foreign key on purpose -- audit entries must
survive the deletion of the user they describe, which is precisely when they
matter most (FR-1.8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 — SQLAlchemy's own name
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.logging import get_request_id
from app.core.models import Base


class AuditAction(StrEnum):
    USER_REGISTERED = "user.registered"
    USER_LOGGED_IN = "user.logged_in"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGGED_OUT = "user.logged_out"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    TOKEN_REFRESHED = "token.refreshed"  # noqa: S105 — an audit action, not a credential
    TOKEN_REUSE_DETECTED = "token.reuse_detected"  # noqa: S105 — an audit action
    OAUTH_LINKED = "oauth.linked"


class AuditLog(Base):
    """Append-only, so it carries `created_at` and deliberately no `updated_at`
    -- an audit row that could be updated would not be evidence of anything."""

    __tablename__ = "audit_log"

    # BIGSERIAL, not UUID: high-volume append-only with no external references,
    # so a time-sortable integer is cheaper and gains nothing from a UUID.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))


async def record(
    session: AsyncSession,
    action: AuditAction,
    *,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Write an audit entry on the caller's session.

    Deliberately shares the caller's transaction: an audit row describing a
    change that rolled back would be a lie.
    """
    session.add(
        AuditLog(
            user_id=user_id,
            action=action.value,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            ip_address=ip_address if ip_address and ip_address != "unknown" else None,
            user_agent=user_agent[:500] if user_agent else None,
            request_id=get_request_id(),
        )
    )
