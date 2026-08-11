"""Data access for the financial core.

Every tenant-owned repository builds on ``BaseRepository.scoped_select``, which
injects the ``user_id`` predicate. Categories are the exception: the system
taxonomy has ``user_id IS NULL``, so that repository reaches shared and personal
rows through an explicit union rather than the tenant path.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import CursorResult, Select, and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.core.pagination import Cursor
from app.core.repository import BaseRepository
from app.modules.finance.models import (
    Account,
    Budget,
    Category,
    Goal,
    RecurringItem,
    Transaction,
    TransactionKind,
)


class AccountRepository(BaseRepository[Account]):
    model = Account

    async def list_all(
        self, user_id: uuid.UUID, *, include_archived: bool = False
    ) -> Sequence[Account]:
        stmt = self.scoped_select(user_id).order_by(Account.name)
        if not include_archived:
            stmt = stmt.where(Account.archived_at.is_(None))
        return (await self.session.execute(stmt)).scalars().all()

    async def by_name(self, user_id: uuid.UUID, name: str) -> Account | None:
        stmt = self.scoped_select(user_id).where(Account.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def adjust_balance(self, account_id: uuid.UUID, delta: Decimal) -> None:
        """Apply a signed delta to the materialised balance.

        Done as a SQL expression rather than read-modify-write so concurrent
        writes to the same account cannot lose an update.
        """
        await self.session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(current_balance=Account.current_balance + delta)
        )

    async def ledger_balance(self, account_id: uuid.UUID) -> Decimal:
        """Recompute the balance from the ledger, for reconciliation.

        Income adds and everything else subtracts. Transfers need no special
        case: they are two rows -- an expense on the source account and an
        income on the destination -- so they net to zero across accounts while
        moving each one correctly.
        """
        account = await self.session.get(Account, account_id)
        if account is None:
            return Decimal("0")

        movement = (
            await self.session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Transaction.kind == TransactionKind.INCOME.value,
                                    Transaction.amount,
                                ),
                                else_=-Transaction.amount,
                            )
                        ),
                        Decimal("0"),
                    )
                ).where(
                    Transaction.account_id == account_id,
                    Transaction.deleted_at.is_(None),
                )
            )
        ).scalar_one()

        return Decimal(account.opening_balance) + Decimal(movement)


class CategoryRepository:
    """Shared taxonomy plus per-user additions.

    Not a BaseRepository: system rows have ``user_id IS NULL`` and must be
    visible to everyone, so the scoping rule genuinely does not apply. Keeping
    it out of the hierarchy means the scoping sweep in the tests cannot be
    quietly weakened to accommodate it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _visible(self, user_id: uuid.UUID) -> Select[tuple[Category]]:
        return select(Category).where(or_(Category.user_id.is_(None), Category.user_id == user_id))

    async def list_all(self, user_id: uuid.UUID) -> Sequence[Category]:
        stmt = self._visible(user_id).order_by(Category.sort_order, Category.name)
        return (await self.session.execute(stmt)).scalars().all()

    async def list_system(self) -> Sequence[Category]:
        """The shared taxonomy: seeded categories, belonging to no user.

        Deliberately not tenant-scoped, because these rows have no tenant. Every
        other query in this class goes through `scoped_select`; this one is the
        exception and says so.
        """
        stmt = (
            select(Category)
            .where(Category.user_id.is_(None))
            .order_by(Category.sort_order, Category.name)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, user_id: uuid.UUID, category_id: uuid.UUID) -> Category | None:
        stmt = self._visible(user_id).where(Category.id == category_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def by_slug(self, user_id: uuid.UUID, slug: str) -> Category | None:
        stmt = self._visible(user_id).where(Category.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, category: Category) -> Category:
        self.session.add(category)
        await self.session.flush()
        return category

    async def in_use(self, category_id: uuid.UUID) -> bool:
        stmt = select(Transaction.id).where(Transaction.category_id == category_id).limit(1)
        return (await self.session.execute(stmt)).first() is not None


class TransactionRepository(BaseRepository[Transaction]):
    model = Transaction

    async def page(
        self,
        user_id: uuid.UUID,
        *,
        cursor: Cursor | None,
        limit: int,
        filters: Any = None,
    ) -> tuple[Sequence[Transaction], bool]:
        """One page of the ledger, newest first.

        Fetches ``limit + 1`` rows to determine ``has_more`` without a second
        COUNT query, which on a large ledger costs as much as the page itself.
        """
        stmt = self.scoped_select(user_id)

        if filters is not None:
            stmt = self._apply_filters(stmt, filters)

        if cursor is not None:
            # Keyset predicate matching (occurred_on DESC, id DESC).
            stmt = stmt.where(
                or_(
                    Transaction.occurred_on < cursor.occurred_on,
                    and_(
                        Transaction.occurred_on == cursor.occurred_on,
                        Transaction.id < cursor.entity_id,
                    ),
                )
            )

        stmt = stmt.order_by(Transaction.occurred_on.desc(), Transaction.id.desc()).limit(limit + 1)
        rows = list((await self.session.execute(stmt)).scalars().all())

        has_more = len(rows) > limit
        return rows[:limit], has_more

    @staticmethod
    def _apply_filters(stmt: Select[tuple[Transaction]], f: Any) -> Select[tuple[Transaction]]:
        if f.from_date:
            stmt = stmt.where(Transaction.occurred_on >= f.from_date)
        if f.to_date:
            stmt = stmt.where(Transaction.occurred_on <= f.to_date)
        if f.account_id:
            stmt = stmt.where(Transaction.account_id == f.account_id)
        if f.category_id:
            stmt = stmt.where(Transaction.category_id == f.category_id)
        if f.kind:
            stmt = stmt.where(Transaction.kind == f.kind.value)
        if f.source:
            stmt = stmt.where(Transaction.source == f.source.value)
        if f.uncategorized_only:
            stmt = stmt.where(Transaction.category_id.is_(None))
        if f.needs_review:
            # Categorised, but by a machine. A row with no category is not in
            # the review queue -- there is nothing to confirm or correct.
            stmt = stmt.where(
                Transaction.category_id.is_not(None),
                Transaction.is_reviewed.is_(False),
            )
        if f.q:
            # Trigram similarity, served by ix_transactions_merchant_trgm.
            stmt = stmt.where(Transaction.merchant_normalized.ilike(f"%{f.q.lower()}%"))
        return stmt

    async def hash_exists(self, user_id: uuid.UUID, content_hash: str) -> bool:
        stmt = select(Transaction.id).where(
            Transaction.user_id == user_id,
            Transaction.content_hash == content_hash,
            Transaction.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).first() is not None

    async def existing_hashes(self, user_id: uuid.UUID, hashes: Sequence[str]) -> set[str]:
        """Bulk membership test, so an import checks duplicates in one query
        rather than one per row."""
        if not hashes:
            return set()
        stmt = select(Transaction.content_hash).where(
            Transaction.user_id == user_id,
            Transaction.content_hash.in_(hashes),
            Transaction.deleted_at.is_(None),
        )
        return set((await self.session.execute(stmt)).scalars().all())

    async def spend_by_category(
        self, user_id: uuid.UUID, start: date, end: date
    ) -> dict[uuid.UUID | None, Decimal]:
        """Expense totals per category for a period.

        Transfers are excluded: moving money between your own accounts is not
        spending, and counting it would double every saving (FR-2.3).
        """
        stmt = (
            select(Transaction.category_id, func.sum(Transaction.amount))
            .where(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
                Transaction.kind == TransactionKind.EXPENSE.value,
                # A transfer between your own accounts is not spending.
                Transaction.transfer_pair_id.is_(None),
                Transaction.excluded_from_analytics.is_(False),
                Transaction.occurred_on >= start,
                Transaction.occurred_on <= end,
            )
            .group_by(Transaction.category_id)
        )
        return {row[0]: Decimal(row[1]) for row in (await self.session.execute(stmt)).all()}

    async def totals(self, user_id: uuid.UUID, start: date, end: date) -> dict[str, Decimal]:
        """Income and expense totals, with transfers excluded (FR-2.3)."""
        stmt = (
            select(Transaction.kind, func.sum(Transaction.amount))
            .where(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
                # Both legs of a transfer are excluded: moving money between
                # your own accounts is neither income nor expense (FR-2.3).
                Transaction.transfer_pair_id.is_(None),
                Transaction.excluded_from_analytics.is_(False),
                Transaction.occurred_on >= start,
                Transaction.occurred_on <= end,
            )
            .group_by(Transaction.kind)
        )
        rows = {k: Decimal(v) for k, v in (await self.session.execute(stmt)).all()}
        return {
            "income": rows.get(TransactionKind.INCOME.value, Decimal("0")),
            "expense": rows.get(TransactionKind.EXPENSE.value, Decimal("0")),
        }

    async def count_all(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(self.scoped_select(user_id).subquery())
        return int((await self.session.execute(stmt)).scalar_one())

    async def _affected(self, stmt: Executable) -> int:
        result = cast(CursorResult[Any], await self.session.execute(stmt))
        return result.rowcount or 0


class BudgetRepository(BaseRepository[Budget]):
    model = Budget

    async def for_period(self, user_id: uuid.UUID, period_start: date) -> Sequence[Budget]:
        stmt = self.scoped_select(user_id).where(Budget.period_start == period_start)
        return (await self.session.execute(stmt)).scalars().all()

    async def find(
        self, user_id: uuid.UUID, category_id: uuid.UUID | None, period_start: date
    ) -> Budget | None:
        stmt = self.scoped_select(user_id).where(
            Budget.period_start == period_start,
            Budget.category_id.is_(None)
            if category_id is None
            else Budget.category_id == category_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class GoalRepository(BaseRepository[Goal]):
    model = Goal

    async def list_all(self, user_id: uuid.UUID, status: str | None = None) -> Sequence[Goal]:
        stmt = self.scoped_select(user_id).order_by(Goal.priority, Goal.created_at)
        if status:
            stmt = stmt.where(Goal.status == status)
        return (await self.session.execute(stmt)).scalars().all()


class RecurringRepository(BaseRepository[RecurringItem]):
    model = RecurringItem

    async def list_all(
        self, user_id: uuid.UUID, *, active_only: bool = True
    ) -> Sequence[RecurringItem]:
        stmt = self.scoped_select(user_id).order_by(RecurringItem.next_due_on)
        if active_only:
            stmt = stmt.where(RecurringItem.is_active.is_(True))
        return (await self.session.execute(stmt)).scalars().all()

    async def upcoming(self, user_id: uuid.UUID, through: date) -> Sequence[RecurringItem]:
        stmt = (
            self.scoped_select(user_id)
            .where(RecurringItem.is_active.is_(True), RecurringItem.next_due_on <= through)
            .order_by(RecurringItem.next_due_on)
        )
        return (await self.session.execute(stmt)).scalars().all()
