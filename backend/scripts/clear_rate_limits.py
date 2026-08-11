"""Drop the rate-limiter's counters.

A full E2E run registers ~80 accounts from one IP. The limiter counts them --
correctly, that is its job -- and several runs in a row exhaust even the
generous local ceiling, at which point pages start failing with a 429 that looks
like an application error rather than a limit being enforced.

The backend test suite already clears these keys between tests for the same
reason. This is that, for the E2E suite, which reaches the API over HTTP and so
cannot do it from inside a fixture.

Local only, on the same reasoning as `reset_dev_data`: switching off a rate
limiter is not something to make convenient against a real deployment.
"""

from __future__ import annotations

import asyncio
import sys

from scripts.reset_dev_data import LOCAL_HOSTS

#: Sliding-window counters, keyed by IP and by account. Nothing else in Redis is
#: rate-limiting state, and nothing else is touched.
PATTERN = "rl:*"


async def _clear() -> int:
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings
    from app.core.redis import get_redis

    settings = get_settings()
    if (host := make_url(str(settings.database_url)).host) not in LOCAL_HOSTS:
        print(f"refusing to run: database host {host!r} is not local", file=sys.stderr)
        return 1

    redis = get_redis()
    cleared = 0
    if keys := await redis.keys(PATTERN):
        cleared = await redis.delete(*keys)
    await redis.aclose()

    print(f"cleared {cleared} rate-limit counters")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_clear()))
