"""Time, made explicit.

``date.today()`` reads the *server's* local timezone. That is almost never what
a financial product means: a user in Asia/Kolkata is on tomorrow's date for five
and a half hours before a UTC server agrees, so a transaction entered late in
the evening lands in the wrong month, and a budget's "current period" flips at
the wrong moment.

Every caller here states which clock it wants, so the choice is visible in the
code rather than inherited from the container's TZ.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_today() -> date:
    """Today in UTC.

    Correct for anything the *system* owns -- job schedules, retention
    windows, audit timestamps.
    """
    return datetime.now(UTC).date()


def today_in(timezone: str | None) -> date:
    """Today as the user experiences it.

    Correct for anything a *person* reads as a date: which month a budget is
    in, what "this period" means, the default date on a new transaction.

    Falls back to UTC on an unknown zone rather than raising -- a bad timezone
    string on a profile should degrade the date by hours, not fail the request.
    """
    if not timezone:
        return utc_today()
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return utc_today()
