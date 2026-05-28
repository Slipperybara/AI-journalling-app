"""Nightly batch parse of a day-bucket.

Runs at `settings.day_boundary_hour` (default 06:00 local). Parses the bucket
that just ended (i.e., yesterday's bucket). On app startup, also sweeps the
last 7 buckets and reparses any that aren't `succeeded` in `parse_log`.
"""
import traceback
from datetime import datetime, timedelta
from typing import List

from .day_messages import get_days_with_messages, get_messages_for_day
from .db import EXTRACTION_TABLES, connect
from .extractions import store_extractions
from .parser import parse_day_content
from .time_buckets import current_bucket
from . import graph_batch, graph_maintenance


def _fetch_day_messages(day: str) -> List[dict]:
    """Return all user messages whose created_at falls in the day-bucket for `day`."""
    return get_messages_for_day(day, roles=('user',))


def _delete_existing_rows(day: str) -> None:
    with connect() as conn:
        cursor = conn.cursor()
        for table in EXTRACTION_TABLES:
            cursor.execute(f"DELETE FROM {table} WHERE day = ?", (day,))


def _format_batch_prompt(messages: List[dict]) -> str:
    parts: list[str] = []
    last_conv = None
    for m in messages:
        if last_conv is not None and m["conversation_id"] != last_conv:
            parts.append("---")
        last_conv = m["conversation_id"]
        parts.append(f"[{m['created_at']}] {m['content']}")
    return "\n".join(parts)


def carryover_unfilled_todos(from_day: str, to_day: str) -> int:
    """Copy unfilled todos from from_day into to_day as new rows.
    Idempotent: skips if any rows with source_day=from_day already exist in to_day."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM todos WHERE day = ? AND source_day = ?",
            (to_day, from_day),
        )
        if cursor.fetchone()[0] > 0:
            return 0
        cursor.execute("""
            INSERT INTO todos (day, task_description, is_completed, due_date, created_at, source_day)
            SELECT ?, task_description, 0, due_date, ?, ?
            FROM todos
            WHERE day = ? AND is_completed = 0
        """, (to_day, datetime.now().isoformat(), from_day, from_day))
        return cursor.rowcount


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
        _delete_existing_rows(day)
        _mark_parse_log(day, status="empty")
        return {"day": day, "status": "empty", "messages": 0}

    user_content = _format_batch_prompt(messages)
    try:
        parsed = parse_day_content(user_content)
    except Exception as e:
        traceback.print_exc()
        _mark_parse_log(day, status="failed", error=str(e))
        raise

    _delete_existing_rows(day)
    store_extractions(parsed, day=day)
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


def backfill_all_message_days() -> int:
    """Parse every day-bucket that has at least one user message. Used after
    the one-shot schema migration so the dashboard / inspector has data to
    work with on first launch. Idempotent (delegates to `parse_day`)."""
    days = [r["day"] for r in get_days_with_messages()]

    for d in days:
        try:
            parse_day(d)
            print(f"[batch] backfill parsed {d}")
        except Exception:
            print(f"[batch] backfill failed for {d}")
            traceback.print_exc()
    return len(days)


def run_scheduled_batch() -> None:
    """Cron entrypoint. Parses yesterday's bucket, writes to Neo4j, carries over todos."""
    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    today = current_bucket().isoformat()

    parse_ok = False
    try:
        parse_day(yesterday)
        print(f"[batch] scheduled parse complete for {yesterday}")
        parse_ok = True
    except Exception:
        print(f"[batch] scheduled parse failed for {yesterday}")
        traceback.print_exc()

    if parse_ok:
        try:
            result = graph_batch.write_day(yesterday)
            print(f"[batch] graph write: {result}")
            maint = graph_maintenance.run()
            print(f"[batch] maintenance: {maint}")
        except Exception:
            print(f"[batch] graph pipeline failed for {yesterday}")
            traceback.print_exc()

    try:
        n = carryover_unfilled_todos(yesterday, today)
        if n:
            print(f"[batch] carried over {n} unfilled todo(s) from {yesterday} to {today}")
    except Exception:
        print(f"[batch] carryover failed")
        traceback.print_exc()
