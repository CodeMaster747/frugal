"""Data access for users and refresh tokens.

`users` is the one tenant table that cannot be tenant-scoped -- a login lookup
happens *before* an identity exists. So UserRepository does not extend
BaseRepository; it is the deliberate exception, and the scoping sweep in
tests/unit/test_repository_scoping.py excludes it explicitly rather than by
accident.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.core.repository import BaseRepository
from app.modules.auth.models import RefreshToken, User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        stmt = select(User).where(User.id == user_id, User.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        # CITEXT handles case-insensitivity in the database.
        stmt = select(User).where(User.email == email, User.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_oauth(self, provider: str, subject: str) -> User | None:
        stmt = select(User).where(
            User.oauth_provider == provider,
            User.oauth_subject == subject,
            User.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def active_ids(self) -> Sequence[uuid.UUID]:
        """Every live user, for the scheduled jobs that sweep all of them.

        Ids only. A scheduled sweep wants a roster, not hydrated `User` objects
        it would immediately discard -- and returning ids is what lets the
        caller stay outside this module's tables.
        """
        stmt = select(User.id).where(User.deleted_at.is_(None)).order_by(User.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def email_exists(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email, User.deleted_at.is_(None))
        return (await self.session.execute(stmt)).first() is not None

    async def add(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def delete(self, user: User) -> None:
        """Hard delete.

        FR-1.8 requires the data to be gone, not hidden. Refresh tokens cascade;
        audit rows survive by design, carrying only an opaque id.
        """
        await self.session.delete(user)
        await self.session.flush()


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    model = RefreshToken

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Look up by digest alone.

        Not tenant-scoped because the token *is* the identity claim -- there is
        no authenticated user yet at refresh time.
        """
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke every token in a rotation chain.

        The response to detected reuse: the legitimate user and the attacker
        both hold tokens from this family and cannot be told apart, so both are
        logged out.
        """
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return await self._affected(stmt)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return await self._affected(stmt)

    async def active_for_user(self, user_id: uuid.UUID) -> Sequence[RefreshToken]:
        stmt = self.scoped_select(user_id).where(
            RefreshToken.revoked_at.is_(None),
            RefreshToken.used_at.is_(None),
            RefreshToken.expires_at > datetime.now(UTC),
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def purge_expired(self, before: datetime | None = None) -> int:
        """Housekeeping, run by Celery Beat. Expired rows prove nothing."""
        cutoff = before or datetime.now(UTC)
        stmt = delete(RefreshToken).where(RefreshToken.expires_at < cutoff)
        return await self._affected(stmt)

    async def _affected(self, stmt: Executable) -> int:
        """Row count for a DML statement.

        ``session.execute`` is typed as returning ``Result``; DML actually
        returns ``CursorResult``, which is where ``rowcount`` lives.
        """
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        return result.rowcount or 0
