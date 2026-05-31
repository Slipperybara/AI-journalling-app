"""Single source of truth for which messages belong to a given day-bucket.

Bucketing is by message `created_at`, not conversation `started_at`. This means
a message sent today in an old conversation correctly lands in today's bucket,
not the conversation's start day.
"""
from typing import List

from .db import connect
from .time_buckets import bucket_sql_expr


def get_messages_for_day(
    day: str,
    roles: tuple[str, ...] | None = None,
) -> List[dict]:
    """Return messages whose created_at falls in the 6 AM-6 AM bucket for `day`.

    Parameters
    ----------
    day:   ISO date string 'YYYY-MM-DD' representing the day-bucket.
    roles: If None, return all roles (user + assistant).
           Pass e.g. ('user',) to filter.

    Returns dicts with keys: id, conversation_id, role, content, created_at.
    Ordered by created_at ASC, id ASC (stable within the same second).
    """
    bucket_expr = bucket_sql_expr("m.created_at")

    if roles is None:
        role_filter = ""
        params: list = [day]
    else:
        placeholders = ",".join(["%s"] * len(roles))
        role_filter = f"AND m.role IN ({placeholders})"
        params = [day, *roles]

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT m.id, m.conversation_id, m.role, m.content, m.created_at
            FROM messages m
            WHERE {bucket_expr} = %s
              {role_filter}
            ORDER BY m.created_at ASC, m.id ASC
        """, params)
        return [dict(r) for r in cursor.fetchall()]


def get_days_with_messages() -> List[dict]:
    """Return every day-bucket that has at least one user message, newest first.
    Used by the inspector day-picker and backfill logic."""
    bucket_expr = bucket_sql_expr("m.created_at")
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT {bucket_expr}::text AS day, COUNT(*) AS message_count
            FROM messages m
            WHERE m.role = 'user'
            GROUP BY day
            ORDER BY day DESC
        """)
        return [dict(r) for r in cursor.fetchall()]
