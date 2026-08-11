"""Tier-3 forecasting, in the worker.

This module exists because Prophet cannot live in the API process. It pulls
~400 MB of compiled dependencies against a 1 GB instance, and a lazy import that
is merely *usually* not reached is one careless call away from resident. The API
image does not install the `forecast` extra at all, so the constraint is
enforced by the image rather than by discipline.

The request path serves tier 2 immediately and queues this. The better forecast
lands in `forecasts` and is served on the next read, when its `data_version`
still matches.
"""

from __future__ import annotations

import uuid

from app.core.clock import utc_now
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]  # Celery's decorator is untyped
    name="app.workers.tasks.forecasting.generate_forecast", bind=True
)
def generate_forecast(self: object, user_id: str, horizon_days: int = 90) -> dict[str, object]:
    """Compute and store a Prophet forecast.

    Runs the async service under a fresh event loop: Celery workers are
    synchronous by design (ADR-006), because Prophet's fit is CPU-bound and
    would block an event loop for seconds regardless.

    A failure here is logged and swallowed. The user already has a tier-2
    forecast; retrying a Prophet fit that is failing for a structural reason
    would burn a worker on every attempt and change nothing they can see.
    """
    del self  # bound only for the task name in logs

    import asyncio

    started = utc_now()
    try:
        result = asyncio.run(_run(uuid.UUID(user_id), horizon_days))
    except Exception as exc:
        logger.warning(
            "tier-3 forecast failed; the tier-2 result stands",
            exc_info=exc,
            extra={"user": user_id},
        )
        return {"status": "failed", "reason": type(exc).__name__}

    elapsed = (utc_now() - started).total_seconds()
    logger.info(
        "tier-3 forecast stored",
        extra={"user": user_id, "method": result["method"], "seconds": round(elapsed, 2)},
    )
    return {"status": "ok", "elapsed_seconds": round(elapsed, 2), **result}


async def _run(user_id: uuid.UUID, horizon_days: int) -> dict[str, object]:
    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.forecasting.service import ForecastService

    # Both async globals -- the engine and the Redis client -- cache connections
    # against the loop that created them, and every task gets a fresh loop.
    await reset_redis()

    # A session on an engine this task owns -- see `worker_async_session`. The
    # cached engine would work for the first task in the process and fail for
    # every one after it.
    async with worker_async_session() as session:
        service = ForecastService(session)
        result, _, _ = await service.forecast(
            user_id,
            horizon_days=horizon_days,
            # The whole reason this task exists.
            allow_prophet=True,
            use_cache=False,
        )
        # Persist what the detector found while it is fresh. Doing it here
        # rather than on the read path keeps a GET free of writes to another
        # module's table.
        synced = await service.sync_recurring(user_id)

        # Keep the table bounded: every read on changed data writes a row.
        await service.prune(user_id)
        await session.commit()

    await reset_redis()
    return {
        "method": result.method,
        "observation_days": result.observation_days,
        "recurring_synced": synced,
    }
