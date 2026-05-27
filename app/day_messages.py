"""Single source of truth for "which messages belong to a given day-bucket?"

Bucketing is by message `created_at`, not conversation `started_at`. This means
a message sent today in an old conversation correctly lands in today's bucket,
not the conversation's start day.
"""
from typing import List

from .db import connect
from .time_buckets import sqlite_bucket_modifier


def get_messages_for_day(
    day: str,
    roles: tuple[str, ...] | None = None,
) -> List[dict]:
    """Return messages whose created_at falls in the 6 AM–6 AM bucket for `day`.

    Parameters
    ----------
    day:   ISO date string 'YYYY-MM-DD' representing the day-bucket.
    roles: If None, return all roles (user + assistant).
           Pass e.g. ('user',) to filter.

    Returns dicts with keys: id, conversation_id, role, content, created_at.
    Ordered by created_at ASC, id ASC (stable within the same second).
    """
    modifier = sqlite_bucket_modifier()

    if roles is None:
        role_filter = ""
        params: list = [modifier, day]
    else:
        placeholders = ",".join("?" * len(roles))
        role_filter = f"AND m.role IN ({placeholders})"
        params = [modifier, day, *roles]

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT m.id, m.conversation_id, m.role, m.content, m.created_at
            FROM messages m
            WHERE date(m.created_at, ?) = ?
              {role_filter}
            ORDER BY m.created_at ASC, m.id ASC
        """, params)
        return [dict(r) for r in cursor.fetchall()]


def get_days_with_messages() -> List[dict]:
    """Return every day-bucket that has at least one user message, newest first.
    Used by the inspector day-picker and backfill logic."""
    modifier = sqlite_bucket_modifier()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date(m.created_at, ?) AS day, COUNT(*) AS message_count
            FROM messages m
            WHERE m.role = 'user'
            GROUP BY day
            ORDER BY day DESC
        """, (modifier,))
        return [dict(r) for r in cursor.fetchall()]
