"""Base repository with mandatory tenant scoping.

This is the structural defence against the highest-cost bug class in a
multi-tenant financial product: returning one user's data to another. Every
read and write goes through a statement builder that injects ``user_id``, so
"forgot to check ownership" is not something a caller can express.

A test in tests/unit/test_repository_scoping.py enumerates every repository
subclass and asserts each generated statement carries a user_id predicate.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.models import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Tenant-scoped data access for a single model.

    Subclasses set ``model`` and add query methods. Those methods must build on
    :meth:`scoped_select` rather than calling ``select(self.model)`` directly --
    that is the whole mechanism.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "model"):
            raise TypeError(f"{cls.__name__} must declare a `model` attribute")

    # -- statement construction ------------------------------------------

    @property
    def _is_tenant_scoped(self) -> bool:
        return hasattr(self.model, "user_id")

    @property
    def _is_soft_deletable(self) -> bool:
        return hasattr(self.model, "deleted_at")

    def scoped_select(
        self, user_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Select[tuple[ModelT]]:
        """The only entry point for building a query on a tenant-owned model.

        Raises if used against a model that has no ``user_id``; shared reference
        tables (categories, products) use :meth:`global_select` instead, and the
        distinction is explicit so it cannot be made by accident.
        """
        if not self._is_tenant_scoped:
            raise TypeError(
                f"{self.model.__name__} has no user_id; use global_select() for "
                "shared reference data"
            )

        stmt = select(self.model).where(self.model.user_id == user_id)  # type: ignore[attr-defined]
        if self._is_soft_deletable and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    def global_select(self) -> Select[tuple[ModelT]]:
        """For shared reference tables only, which have no owner by design."""
        if self._is_tenant_scoped:
            raise TypeError(f"{self.model.__name__} is tenant-owned; use scoped_select(user_id)")
        stmt = select(self.model)
        if self._is_soft_deletable:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    # -- reads -------------------------------------------------------------

    async def get(
        self, user_id: uuid.UUID, entity_id: uuid.UUID, *, include_deleted: bool = False
    ) -> ModelT | None:
        stmt = self.scoped_select(user_id, include_deleted=include_deleted).where(
            self.model.id == entity_id  # type: ignore[attr-defined]
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_404(self, user_id: uuid.UUID, entity_id: uuid.UUID) -> ModelT:
        """Fetch or raise NotFound.

        A row owned by another user is indistinguishable from a missing one --
        returning 403 would confirm the ID exists (docs/04-api-design.md 2.3).
        """
        entity = await self.get(user_id, entity_id)
        if entity is None:
            raise NotFoundError(self.model.__name__)
        return entity

    async def list(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> Sequence[ModelT]:
        stmt = self.scoped_select(user_id).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(self.scoped_select(user_id).subquery())
        return (await self.session.execute(stmt)).scalar_one()

    async def exists(self, user_id: uuid.UUID, entity_id: uuid.UUID) -> bool:
        return await self.get(user_id, entity_id) is not None

    # -- writes ------------------------------------------------------------

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def soft_delete(self, user_id: uuid.UUID, entity_id: uuid.UUID) -> None:
        entity = await self.get_or_404(user_id, entity_id)
        if not self._is_soft_deletable:
            raise TypeError(f"{self.model.__name__} does not support soft delete")
        entity.soft_delete()  # type: ignore[attr-defined]
        await self.session.flush()
