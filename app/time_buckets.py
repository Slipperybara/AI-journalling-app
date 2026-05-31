"""Day-bucketing for journal entries.

A "day" runs from `settings.day_boundary_hour` (default 06:00) to the same hour
the next calendar day. A conversation belongs to day N if its `started_at`
falls in [06:00 day N, 06:00 day N+1). A late-night conversation starting at
23:00 day N and continuing into 01:00 day N+1 stays bucketed to day N. A fresh
conversation started at 03:00 day N+1 also buckets to day N (it's pre-6 AM).

The same rule applies in SQL via `bucket_sql_expr("started_at")` which yields
the Postgres expression `(started_at::timestamp - INTERVAL 'N hours')::date`.
"""
from datetime import date, datetime, timedelta

from .core import settings


def bucket_for(ts: datetime) -> date:
    return (ts - timedelta(hours=settings.day_boundary_hour)).date()


def current_bucket() -> date:
    return bucket_for(datetime.now())


def bucket_sql_expr(column: str) -> str:
    """Return a Postgres expression that yields the day-bucket date for `column`.

    Inlined into queries via f-string. The hour comes from settings rather than
    a parameter because Postgres `INTERVAL` syntax doesn't accept bound params.
    """
    return f"({column}::timestamp - INTERVAL '{settings.day_boundary_hour} hours')::date"
