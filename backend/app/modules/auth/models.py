"""Auth module tables: users and refresh tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin


class OAuthProvider(StrEnum):
    GOOGLE = "google"


class User(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    # CITEXT makes email comparison case-insensitive in the database. Doing it
    # by lowercasing in application code fails the moment one code path forgets.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)

    # Null for OAuth-only accounts; the check constraint below guarantees at
    # least one authentication method exists.
    password_hash: Mapped[str | None] = mapped_column(Text)

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Asia/Kolkata")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="en-IN")

    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_provider: Mapped[str | None] = mapped_column(String(20))
    oauth_subject: Mapped[str | None] = mapped_column(Text)

    # Set once the demo seeder has run, so the empty state knows whether to
    # offer it (FR-2.10).
    is_demo_seeded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_users_oauth",
            "oauth_provider",
            "oauth_subject",
            unique=True,
            postgresql_where=text("oauth_subject IS NOT NULL"),
        ),
        CheckConstraint(
            "password_hash IS NOT NULL OR oauth_subject IS NOT NULL",
            name="auth_method",
        ),
    )


class RefreshToken(UUIDMixin, TimestampMixin, Base):
    """One link in a rotation chain.

    Rotation means each refresh issues a new row and marks the old one used.
    A *used* token presented again means it was stolen and replayed, so the
    whole `family_id` is revoked -- the legitimate user and the attacker are
    indistinguishable at that point, and logging both out is the safe answer
    (FR-1.3).
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    # Only the SHA-256 digest is stored, so a database leak yields no usable
    # tokens.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index(
            "ix_refresh_tokens_user_id_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Uniqueness on token_hash comes from unique=True on the column; a
        # second UniqueConstraint here would emit a duplicate index.
    )
