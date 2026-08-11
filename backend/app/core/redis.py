"""Redis client.

Redis holds only regenerable state -- Celery broker, computed-aggregate cache,
rate-limit counters. Losing it degrades performance, never data. Job state lives
in Postgres precisely so an Upstash eviction cannot lose a user's receipt.
"""

from __future__ import annotations

from contextlib import suppress
from functools import lru_cache

from redis.asyncio import Redis, from_url

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    settings = get_settings()
    return from_url(
        str(settings.redis_url),
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
    )


async def check_redis() -> bool:
    """Readiness probe. Returns False rather than raising."""
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


async def close_redis() -> None:
    # aclose() supersedes the deprecated close() on redis-py 8.x.
    await get_redis().aclose()


async def reset_redis() -> None:
    """Close the client and drop it from the cache.

    For Celery tasks that run `asyncio.run()`: the client is `lru_cache`d and
    its connections bind to the loop that opened them, so the second task in a
    worker process inherits a pool pointing at a loop that no longer exists. The
    failure is "attached to a different loop", and it is worse than a plain
    crash because the *first* task in each process succeeds -- so it reads as an
    intermittent fault rather than a certainty.

    Paired with `worker_async_session`, which solves the same problem for the
    database engine.
    """
    # Closing a client whose loop has already gone raises; the close is
    # best-effort and dropping the cache is the part that matters.
    with suppress(Exception):
        await get_redis().aclose()
    get_redis.cache_clear()
