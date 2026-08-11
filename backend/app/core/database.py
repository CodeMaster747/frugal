"""Database engines and session factories.

Two engines over one schema (ADR-006): asyncpg for the API, psycopg for Celery
workers. Forcing async into CPU-bound OCR and Prophet code would add a
thread-pool layer, obscure stack traces, and buy no concurrency -- that work is
bounded by cores, not by waiting.

Engines are created lazily so that importing this module never opens a
connection; tests and Alembic import it freely.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_async_engine() -> AsyncEngine:
    """Engine for the API process."""
    settings = get_settings()
    return create_async_engine(
        settings.async_database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        # Neon closes idle connections; recycling below their timeout avoids
        # handing a dead connection to a request.
        pool_recycle=280,
    )


@lru_cache(maxsize=1)
def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def worker_async_session() -> AsyncIterator[AsyncSession]:
    """An async session for a Celery task, on an engine it owns and disposes.

    `get_async_engine` is `lru_cache`d, which is right for the API -- one engine,
    one long-lived event loop. It is wrong inside a worker: each task runs
    `asyncio.run()`, which creates and destroys a loop, while asyncpg's
    connections stay bound to the loop that opened them. The cached engine hands
    the second task a pool full of connections belonging to a loop that no
    longer exists, and it fails with "attached to a different loop".

    The symptom is nasty because the *first* task in each worker process
    succeeds: it looks like an intermittent fault rather than a certainty.

    Most worker tasks should use `sync_session` instead (ADR-006). This exists
    for the ones whose logic is already async and would otherwise need a second
    implementation to maintain in parallel.

    **Use this anywhere `asyncio.run` appears.** The failure has now been hit in
    three separate places -- the forecasting task, the market refresh task, and
    a session-scoped pytest fixture -- and each time it looked like a different
    problem. The rule is simple: if the code creates its own event loop, it
    needs its own engine, and `get_async_engine` is not it. `reset_redis` is the
    matching fix for the Redis client, which caches the same way.
    """
    settings = get_settings()
    engine = create_async_engine(
        settings.async_database_url,
        echo=settings.db_echo,
        # A task is one unit of work; pooling across a disposed engine buys
        # nothing and costs the bug above.
        poolclass=NullPool,
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@lru_cache(maxsize=1)
def get_sync_engine() -> Engine:
    """Engine for Celery workers.

    Pooled smaller than the API's: worker concurrency is 1 on t3.micro, and both
    processes share Neon's connection allowance.
    """
    settings = get_settings()
    return create_engine(
        settings.sync_database_url,
        echo=settings.db_echo,
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=280,
    )


@lru_cache(maxsize=1)
def get_sync_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_sync_engine(), expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session.

    Commits on success, rolls back on any exception. Handlers therefore never
    manage transactions by hand, which is what keeps a partially-applied write
    from escaping a failed request.
    """
    async with get_async_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@contextmanager
def sync_session() -> Iterator[Session]:
    """Session for Celery tasks, with the same commit/rollback discipline."""
    with get_sync_session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


async def check_database() -> bool:
    """Readiness probe. Returns False rather than raising."""
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def dispose_engines() -> None:
    """Close pools on shutdown."""
    await get_async_engine().dispose()
