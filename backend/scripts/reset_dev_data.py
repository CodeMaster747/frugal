"""Drop accumulated local test users.

Every E2E run signs up ~80 users and seeds ~300 transactions each, and nothing
cleans them up. The local database therefore grows without bound, and the
hourly notification sweep is O(users) -- so a machine that has run the suite
for a few weeks has a sweep slow enough to contend with the API it shares a
database with. That contention is observable: the E2E suite fails only on runs
that overlap a sweep.

This deletes users and lets the cascade take everything owned by them. The
system category taxonomy has ``user_id IS NULL``, is owned by no user, and so
survives -- which is why this is a data reset and not a `down` + `up`.

Refuses to run against anything but a local database. The guard is the point:
a script whose whole purpose is deleting every user must not be one SSH session
away from doing it in production.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "postgres", "db", "host.docker.internal"})


async def _reset(email_like: str | None) -> int:
    from sqlalchemy import delete, func, select
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings
    from app.core.database import worker_async_session
    from app.modules.auth.models import User

    settings = get_settings()
    host = make_url(str(settings.database_url)).host

    if host not in LOCAL_HOSTS:
        print(f"refusing to run: database host {host!r} is not local", file=sys.stderr)
        return 1

    where = User.email.like(email_like) if email_like else None

    async with worker_async_session() as session:
        count = select(func.count()).select_from(User)
        statement = delete(User)
        if where is not None:
            count = count.where(where)
            statement = statement.where(where)

        before = await session.scalar(count)
        await session.execute(statement)
        await session.commit()

    scope = f" matching {email_like!r}" if email_like else ""
    print(f"deleted {before} users{scope} and everything owned by them")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email-like",
        help=(
            "Only delete users whose email matches this SQL LIKE pattern. "
            "The E2E teardown passes '%%@example.com' -- a reserved domain "
            "(RFC 2606), so it can never match a real account."
        ),
    )
    raise SystemExit(asyncio.run(_reset(parser.parse_args().email_like)))
