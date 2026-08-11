"""Scheduled price refresh.

Runs daily under Celery Beat. Records today's prices for every tracked product,
then checks each watcher for a drop worth telling them about.

Only *tracked* products are polled. Refreshing the whole catalogue would write
thousands of rows nobody reads — the value of a price observation is entirely in
someone caring about it, and the wishlist is where that is recorded.
"""

from __future__ import annotations

import uuid

from app.core.clock import utc_now
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.market.refresh_prices", bind=True
)
def refresh_prices(self: object) -> dict[str, object]:
    """Record today's prices and raise any drop alerts."""
    del self

    import asyncio

    started = utc_now()
    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning("price refresh failed; yesterday's prices stand", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}

    logger.info(
        "price refresh complete",
        extra={**result, "seconds": round((utc_now() - started).total_seconds(), 2)},
    )
    return {"status": "ok", **result}


async def _run() -> dict[str, int]:
    from sqlalchemy import select

    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.market.models import WishlistItem
    from app.modules.market.service import MarketService

    # Both async globals cache connections against the loop that created them,
    # and every task gets a fresh loop. See `worker_async_session`.
    await reset_redis()

    async with worker_async_session() as session:
        service = MarketService(session)
        refreshed = await service.refresh_tracked_products()

        # Every user with something on their wishlist. Iterating users rather
        # than items because the cooling period and the alert cap are per user.
        watchers = (
            (
                await session.execute(
                    select(WishlistItem.user_id).where(WishlistItem.deleted_at.is_(None)).distinct()
                )
            )
            .scalars()
            .all()
        )

        raised = 0
        for user_id in watchers:
            raised += len(await service.check_drops(uuid.UUID(str(user_id))))

        await session.commit()

    await reset_redis()
    return {**refreshed, "watchers": len(watchers), "alerts": raised}
