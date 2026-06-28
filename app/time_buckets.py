"""Day-bucketing for journal entries.

A "day" runs from `settings.day_boundary_hour` (default 06:00) to the same hour
the next calendar day. A conversation belongs to day N if its `started_at`
falls in [06:00 day N, 06:00 day N+1). A late-night conversation starting at
23:00 day N and continuing into 01:00 day N+1 stays bucketed to day N. A fresh
conversation started at 03:00 day N+1 also buckets to day N (it's pre-6 AM).

The same rule applies in SQL via `bucket_sql_expr("started_at")` which yields
the Postgres expression `(started_at::timestamp - INTERVAL 'N hours')::date`.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None  # type: ignore

from .core import settings


def bucket_for(ts: datetime, tz: Optional[str] = None) -> date:
    """Day-bucket for a timestamp.

    With `tz` (an IANA name like 'Asia/Singapore'), the timestamp is first
    converted into that timezone so the bucket reflects the user's LOCAL
    06:00-06:00 day. A naive `ts` is assumed to be UTC. Without `tz`, behaves as
    before (server-local wall time, which is UTC in production).
    """
    if tz and ZoneInfo is not None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        try:
            ts = ts.astimezone(ZoneInfo(tz))
        except Exception:
            ts = ts.astimezone(ZoneInfo("UTC"))
        ts = ts.replace(tzinfo=None)  # bucket on local wall time
    return (ts - timedelta(hours=settings.day_boundary_hour)).date()


def current_bucket() -> date:
    return bucket_for(datetime.now())


def bucket_sql_expr(column: str) -> str:
    """Return a Postgres expression that yields the day-bucket date for `column`.

    Inlined into queries via f-string. The hour comes from settings rather than
    a parameter because Postgres `INTERVAL` syntax doesn't accept bound params.
    """
    return f"({column}::timestamp - INTERVAL '{settings.day_boundary_hour} hours')::date"


def bucket_sql_expr_tz(column: str) -> str:
    """Like `bucket_sql_expr`, but converts the naive-UTC `column` into the
    user's timezone before bucketing — so the bucket matches the user's LOCAL
    06:00-06:00 day.

    The timezone is a single bound `%s` parameter the caller MUST supply in the
    position where this expression appears in the SQL text (the INTERVAL hour
    stays inlined because Postgres won't bind it).
    """
    return (
        f"((({column}::timestamp AT TIME ZONE 'UTC') AT TIME ZONE %s) "
        f"- INTERVAL '{settings.day_boundary_hour} hours')::date"
    )
