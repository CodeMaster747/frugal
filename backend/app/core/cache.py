"""Read-through cache with version-based invalidation.

Analytics aggregates are expensive and read constantly. TTL caching would be the
obvious approach and is the wrong one here: a stale financial number is worse
than a slow one, and a TTL guarantees a window where the dashboard disagrees
with the ledger the user just changed.

Instead every user carries a `data_version` counter, bumped on any write that
could move an aggregate. The version is part of the cache key, so a write
invalidates every derived value at once *by construction* -- nothing has to
remember which keys a given write affects, which is the part that rots.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 3600
_VERSION_KEY = "dv"
_CACHE_KEY = "agg"


def _version_key(user_id: uuid.UUID) -> str:
    return f"{_VERSION_KEY}:{user_id}"


async def current_version(user_id: uuid.UUID) -> int:
    """The user's data version. Absent means 0 -- nothing has been written yet."""
    try:
        raw = await get_redis().get(_version_key(user_id))
    except Exception as exc:
        # A cache outage must not break reads; it just means no caching.
        logger.warning("cache unavailable reading version", exc_info=exc)
        return 0
    return int(raw) if raw else 0


async def bump_version(user_id: uuid.UUID) -> int:
    """Invalidate every cached aggregate for a user.

    Called after any transaction, account, or budget write. One INCR replaces
    an enumerate-and-delete pass, which is both cheaper and impossible to get
    incomplete.
    """
    try:
        return int(await get_redis().incr(_version_key(user_id)))
    except Exception as exc:
        logger.warning("cache unavailable bumping version", exc_info=exc)
        return 0


async def get_or_compute(
    user_id: uuid.UUID,
    scope: str,
    compute: Callable[[], Awaitable[T]],
    *,
    serialize: Callable[[T], Any] = lambda v: v,
    deserialize: Callable[[Any], T] = lambda v: v,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> tuple[T, int]:
    """Return a cached aggregate, computing it on a miss.

    Returns the value and the version it was computed at, so the response can
    expose `data_version` -- letting a client tell whether what it is looking at
    reflects its own most recent write.
    """
    version = await current_version(user_id)
    key = f"{_CACHE_KEY}:{user_id}:{scope}:{version}"

    try:
        cached = await get_redis().get(key)
        if cached is not None:
            return deserialize(json.loads(cached)), version
    except Exception as exc:
        logger.warning("cache read failed", exc_info=exc, extra={"scope": scope})

    value = await compute()

    try:
        await get_redis().set(key, json.dumps(serialize(value), default=str), ex=ttl)
    except Exception as exc:
        logger.warning("cache write failed", exc_info=exc, extra={"scope": scope})

    return value, version
