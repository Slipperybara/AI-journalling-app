"""Nightly batch parse of a day-bucket.

Runs at `settings.day_boundary_hour` (default 06:00 local). Parses the bucket
that just ended (i.e., yesterday's bucket). On app startup, also sweeps the
last 7 buckets and reparses any that aren't `succeeded` in `parse_log`.
"""
import traceback
from datetime import datetime, timedelta
from typing import List

from .db import connect
from .extractions import store_extractions
from .parser import parse_day_content
from .time_buckets import current_bucket, sqlite_bucket_modifier


def _fetch_day_messages(day: str) -> List[dict]:
    """Return all user messages belonging to conversations bucketed to `day`,
    chronological. Conversation membership uses started_at (not message
    timestamps), so messages from a midnight-spanning convo bucket together."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.id, m.conversation_id, m.content, m.created_at
            FROM messages m
            JOIN conversations c ON m.conversation_id = c.id
            WHERE date(c.started_at, ?) = ?
              AND m.role = 'user'
            ORDER BY m.created_at ASC
        """, (sqlite_bucket_modifier(), day))
        return [dict(r) for r in cursor.fetchall()]


def _delete_existing_rows(day: str, message_ids: List[int]) -> None:
    """Idempotent cleanup. Removes (a) any prior day-keyed rows for this day,
    and (b) any legacy per-message rows anchored on this day's messages (these
    could exist on DBs that pre-date the day-keyed migration)."""
    placeholders = ",".join("?" * len(message_ids)) or "NULL"
    with connect() as conn:
        cursor = conn.cursor()
        for table in ("emotional_analysis", "health_metrics", "productivity_metrics", "events", "todos"):
            cursor.execute(
                f"DELETE FROM {table} WHERE day = ? OR (day IS NULL AND message_id IN ({placeholders}))",
                [day, *message_ids],
            )


def _format_batch_prompt(messages: List[dict]) -> str:
    parts: list[str] = []
    last_conv = None
    for m in messages:
        if last_conv is not None and m["conversation_id"] != last_conv:
            parts.append("---")
        last_conv = m["conversation_id"]
        parts.append(f"[{m['created_at']}] {m['content']}")
    return "\n".join(parts)


def _mark_parse_log(day: str, status: str, error: str = None) -> None:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO parse_log (day, status, parsed_at, error)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(day) DO UPDATE SET
                status = excluded.status,
                parsed_at = excluded.parsed_at,
                error = excluded.error
        """, (day, status, datetime.now().isoformat(), error))


def parse_day(day: str) -> dict:
    """Parse a single day-bucket. Idempotent — safe to re-run.
    Returns a small summary dict the admin endpoint can return."""
    messages = _fetch_day_messages(day)

    if not messages:
        _mark_parse_log(day, status="empty")
        return {"day": day, "status": "empty", "messages": 0}

    user_content = _format_batch_prompt(messages)
    try:
        parsed = parse_day_content(user_content)
    except Exception as e:
        traceback.print_exc()
        _mark_parse_log(day, status="failed", error=str(e))
        raise

    anchor_message_id = messages[-1]["id"]
    msg_ids = [m["id"] for m in messages]
    _delete_existing_rows(day, msg_ids)
    store_extractions(anchor_message_id, parsed, day=day)
    _mark_parse_log(day, status="succeeded")
    return {"day": day, "status": "succeeded", "messages": len(messages)}


def catch_up_parses(days_back: int = 7) -> None:
    """Sweep the last N completed buckets and parse any that aren't succeeded."""
    today = current_bucket()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT day, status FROM parse_log")
        log = {r["day"]: r["status"] for r in cursor.fetchall()}

    for delta in range(1, days_back + 1):
        d = (today - timedelta(days=delta)).isoformat()
        if log.get(d) == "succeeded":
            continue
        try:
            parse_day(d)
            print(f"[batch] catch-up parsed {d}")
        except Exception:
            print(f"[batch] catch-up failed for {d}")
            traceback.print_exc()


def run_scheduled_batch() -> None:
    """Cron entrypoint. Parses yesterday's bucket — the one that just ended."""
    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    try:
        parse_day(yesterday)
        print(f"[batch] scheduled parse complete for {yesterday}")
    except Exception:
        print(f"[batch] scheduled parse failed for {yesterday}")
        traceback.print_exc()
