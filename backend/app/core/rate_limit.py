"""Sliding-window rate limiting backed by Redis.

Per-IP limits alone are trivially defeated by a botnet, so credential-sensitive
routes are limited per IP *and* per account (FR-1.7).

The whole check is one Lua script -- a single round trip. The read-modify-write
must be atomic anyway (two concurrent requests would otherwise both see the
count below the limit), and Upstash's free tier allows 10k commands/day, so
spending four commands per check would be a real budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

# ZREMRANGEBYSCORE evicts entries older than the window, ZCARD counts what
# remains, and the request is recorded only if it is admitted -- so a rejected
# request does not extend its own penalty.
_SLIDING_WINDOW = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = window
  if oldest[2] then retry = math.ceil(oldest[2] + window - now) end
  if retry < 1 then retry = 1 end
  return {0, retry}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return {1, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimit:
    """A named limit: `times` requests per `seconds`."""

    times: int
    seconds: int

    def key_for(self, scope: str, identifier: str) -> str:
        return f"rl:{scope}:{identifier}:{self.seconds}"


# Named policies, resolved from settings so limits are declared in one place
# rather than scattered as literals across routers. Read through the functions
# below rather than captured at import time, so a test or environment override
# actually takes effect.


def login_per_ip() -> RateLimit:
    return RateLimit(times=get_settings().login_attempts_per_ip, seconds=900)


def login_per_account() -> RateLimit:
    return RateLimit(times=get_settings().login_attempts_per_account, seconds=900)


def register_per_ip() -> RateLimit:
    return RateLimit(times=get_settings().registrations_per_ip_per_hour, seconds=3600)


def refresh_per_user() -> RateLimit:
    return RateLimit(times=get_settings().refreshes_per_hour, seconds=3600)


class RateLimiter:
    def __init__(self) -> None:
        self._script_sha: str | None = None

    async def _run(self, key: str, limit: RateLimit) -> tuple[bool, int]:
        redis = get_redis()
        now = time.time()
        member = f"{now:.6f}:{id(self)}"
        args = [str(now), str(limit.seconds), str(limit.times), member]

        try:
            if self._script_sha is None:
                self._script_sha = await redis.script_load(_SLIDING_WINDOW)
            result = await redis.evalsha(self._script_sha, 1, key, *args)
        except Exception as exc:
            if "NOSCRIPT" in str(exc):
                # Redis restarted and lost its script cache; reload once.
                self._script_sha = await redis.script_load(_SLIDING_WINDOW)
                result = await redis.evalsha(self._script_sha, 1, key, *args)
            else:
                # Fail *open*. A Redis outage must not lock every user out of
                # their own finances; the tradeoff is deliberate and logged.
                logger.warning("rate limiter unavailable, failing open", exc_info=exc)
                return True, 0

        allowed, retry_after = int(result[0]), int(result[1])
        return bool(allowed), retry_after

    async def check(self, scope: str, identifier: str, limit: RateLimit) -> None:
        """Raise RateLimitedError if the caller is over the limit."""
        allowed, retry_after = await self._run(limit.key_for(scope, identifier), limit)
        if not allowed:
            logger.warning(
                "rate limit exceeded",
                extra={"scope": scope, "limit": limit.times, "window_s": limit.seconds},
            )
            raise RateLimitedError(retry_after)

    async def reset(self, scope: str, identifier: str, limit: RateLimit) -> None:
        """Clear a counter -- used after a successful login so a user who
        finally gets their password right is not still throttled."""
        await get_redis().delete(limit.key_for(scope, identifier))


rate_limiter = RateLimiter()
