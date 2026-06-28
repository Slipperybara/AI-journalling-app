"""Single source of truth for which messages belong to a given (user, day-bucket).

Bucketing is by message `created_at`, not conversation `started_at`. This means
a message sent today in an old conversation correctly lands in today's bucket,
not the conversation's start day.
"""
from typing import List
from uuid import UUID

from .db import connect
from .time_buckets import bucket_sql_expr, bucket_sql_expr_tz


def get_messages_for_day(
    day: str,
    user_id: UUID,
    roles: tuple[str, ...] | None = None,
    tz: str | None = None,
) -> List[dict]:
    """Return messages whose created_at falls in the 6 AM-6 AM bucket for `day`,
    for the given user.

    Parameters
    ----------
    day:     ISO date string 'YYYY-MM-DD' representing the day-bucket.
    user_id: scope every read by this UUID.
    roles:   If None, return all roles (user + assistant). Pass e.g. ('user',)
             to filter.
    tz:      IANA timezone. When given, the bucket is the user's LOCAL day; when
             None, the legacy server (UTC) bucket.

    Returns dicts with keys: id, conversation_id, role, content, created_at.
    Ordered by created_at ASC, id ASC (stable within the same second).
    """
    bucket_expr = bucket_sql_expr_tz("m.created_at") if tz else bucket_sql_expr("m.created_at")

    # Param order MUST match the SQL text: user_id, [tz inside bucket_expr], day, [roles].
    params: list = [str(user_id)]
    if tz:
        params.append(tz)
    params.append(day)
    if roles is None:
        role_filter = ""
    else:
        placeholders = ",".join(["%s"] * len(roles))
        role_filter = f"AND m.role IN ({placeholders})"
        params.extend(roles)

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT m.id, m.conversation_id, m.role, m.content, m.created_at
            FROM messages m
            WHERE m.user_id = %s
              AND {bucket_expr} = %s
              {role_filter}
            ORDER BY m.created_at ASC, m.id ASC
        """, params)
        return [dict(r) for r in cursor.fetchall()]


def get_days_with_messages(user_id: UUID, tz: str | None = None) -> List[dict]:
    """Return every day-bucket that has at least one user message for this
    user, newest first. Used by the inspector day-picker and backfill logic.
    `tz` selects local vs legacy (UTC) bucketing."""
    bucket_expr = bucket_sql_expr_tz("m.created_at") if tz else bucket_sql_expr("m.created_at")
    # Param order: [tz inside bucket_expr], user_id.
    params: list = []
    if tz:
        params.append(tz)
    params.append(str(user_id))
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT {bucket_expr}::text AS day, COUNT(*) AS message_count
            FROM messages m
            WHERE m.user_id = %s AND m.role = 'user'
            GROUP BY day
            ORDER BY day DESC
        """, params)
        return [dict(r) for r in cursor.fetchall()]


def get_all_user_ids_with_messages() -> List[UUID]:
    """Return every distinct user UUID that has at least one message. Used by
    the nightly batch to iterate users without depending on auth.users."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM messages")
        return [r["user_id"] for r in cursor.fetchall()]
