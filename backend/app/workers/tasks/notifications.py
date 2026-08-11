"""Scheduled notification generation and delivery.

Runs hourly under Beat, which sounds frequent for a daily digest and is not:
users choose their own digest hour, so the task has to wake up often enough to
catch each of them. Generation is deduplicated by the database and delivery is
gated by each user's preferences, so an hourly run that finds nothing to do is
the normal case and costs one query per user.
"""

from __future__ import annotations

from app.core.clock import utc_now
from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.notifications.run_notifications", bind=True
)
def run_notifications(self: object) -> dict[str, object]:
    """Generate what is due and deliver what is allowed."""
    del self

    import asyncio

    started = utc_now()
    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning("notification run failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}

    logger.info(
        "notification run complete",
        extra={**result, "seconds": round((utc_now() - started).total_seconds(), 2)},
    )
    return {"status": "ok", **result}


#: Users per transaction.
#:
#: The first version swept every user in one session and committed once at the
#: end. On 2,475 users that held a single connection and one open transaction
#: for 15 seconds, and the API — sharing the pool — became slow enough that
#: pages failed to load while the sweep ran. It was reproducible: the E2E suite
#: failed only on runs that overlapped a sweep.
#:
#: A batch is also the unit of durability. Committing once at the end meant a
#: failure at user 2,400 discarded the work done for the previous 2,399.
BATCH_SIZE = 100

#: Yielded between batches so the sweep interleaves with user traffic rather
#: than racing it. ~25 batches × 50 ms adds a second to a job with an hour to
#: run in, which is a good trade for not degrading the API every hour.
BATCH_PAUSE_SECONDS = 0.05


async def _run() -> dict[str, int]:
    import asyncio

    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.auth.service import AuthService
    from app.modules.notifications.service import NotificationService

    # Both async globals bind connections to the loop that opened them, and this
    # task creates a fresh one. See `worker_async_session`.
    await reset_redis()

    # Read the roster in its own short-lived session, so the connection is not
    # held while the batches below do their work. Through the auth *service*,
    # not a select on its table -- the boundary the import-linter enforces.
    async with worker_async_session() as session:
        users = await AuthService(session).active_user_ids()

    created = delivered = 0
    for start in range(0, len(users), BATCH_SIZE):
        batch = users[start : start + BATCH_SIZE]

        async with worker_async_session() as session:
            service = NotificationService(session)
            for uid in batch:
                # Per user rather than in bulk: preferences, quiet hours, and
                # the digest hour are all per user, and a bulk query would have
                # to reimplement that logic in SQL to gain anything.
                created += (await service.generate(uid))["created"]
                delivered += (await service.deliver_pending(uid))["delivered"]
            await session.commit()

        await asyncio.sleep(BATCH_PAUSE_SECONDS)

    await reset_redis()
    return {"users": len(users), "created": created, "delivered": delivered}
