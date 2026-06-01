"""Nightly batch parse of a day-bucket, per user.

Runs at `settings.day_boundary_hour` (default 06:00 local). Parses the bucket
that just ended (i.e., yesterday's bucket) for every user with data. On app
startup, also sweeps the last 7 buckets per user and reparses any that aren't
`succeeded` in `parse_log`.

Phase 2 scoping: every helper takes a `user_id`. `run_scheduled_batch` and
`catch_up_parses` discover users via `get_all_user_ids_with_messages()` and
loop. The set of users is derived from data, not auth.users, so this stays
portable across Supabase prod and local Postgres dev.
"""
import traceback
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

from .day_messages import (
    get_all_user_ids_with_messages,
    get_days_with_messages,
    get_messages_for_day,
)
from .db import EXTRACTION_TABLES, connect
from .extractions import store_extractions
from .parser import parse_day_content
from .time_buckets import current_bucket
from . import graph_batch, graph_maintenance


def _fetch_day_messages(day: str, user_id: UUID) -> List[dict]:
    return get_messages_for_day(day, user_id, roles=('user',))


def _delete_existing_rows(day: str, user_id: UUID) -> None:
    with connect() as conn:
        cursor = conn.cursor()
        for table in EXTRACTION_TABLES:
            cursor.execute(
                f"DELETE FROM {table} WHERE user_id = %s AND day = %s",
                (str(user_id), day),
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


def _mark_parse_log(day: str, status: str, user_id: UUID, error: Optional[str] = None) -> None:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO parse_log (user_id, day, status, parsed_at, error)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, day) DO UPDATE SET
                status = excluded.status,
                parsed_at = excluded.parsed_at,
                error = excluded.error
        """, (str(user_id), day, status, datetime.now().isoformat(), error))


def parse_day(day: str, user_id: UUID) -> dict:
    """Parse a single day-bucket for one user. Idempotent — safe to re-run.
    Returns a small summary dict the admin endpoint can return."""
    messages = _fetch_day_messages(day, user_id)

    if not messages:
        _delete_existing_rows(day, user_id)
        _mark_parse_log(day, status="empty", user_id=user_id)
        return {"day": day, "user_id": str(user_id), "status": "empty", "messages": 0}

    user_content = _format_batch_prompt(messages)
    try:
        parsed = parse_day_content(user_content, user_id)
    except Exception as e:
        traceback.print_exc()
        _mark_parse_log(day, status="failed", user_id=user_id, error=str(e))
        raise

    _delete_existing_rows(day, user_id)
    store_extractions(parsed, day=day, user_id=user_id)
    _mark_parse_log(day, status="succeeded", user_id=user_id)
    return {"day": day, "user_id": str(user_id), "status": "succeeded", "messages": len(messages)}


def catch_up_parses(days_back: int = 7, user_id: Optional[UUID] = None) -> None:
    """Sweep the last N completed buckets and bring each user fully up to date:
    parse missing days (Postgres) + reconcile graph (Neo4j) + post today's
    morning brief.

    Re-running graph reconcile + morning brief on a day the 06:00 cron already
    handled is safe: `graph_maintenance.run` is idempotent (MERGE-based) and
    `morning_brief.post_morning_brief` early-returns on
    `morning_brief_log.status IN ('posted','skipped_empty')` before any LLM call.
    See the dedup table in the plan.

    If `user_id` is given, only that user is processed. Otherwise iterates
    every user with messages.
    """
    user_ids = [user_id] if user_id is not None else get_all_user_ids_with_messages()
    today = current_bucket()
    today_iso = today.isoformat()

    for uid in user_ids:
        with connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT day, status FROM parse_log WHERE user_id = %s",
                (str(uid),),
            )
            log = {r["day"]: r["status"] for r in cursor.fetchall()}

        for delta in range(1, days_back + 1):
            d = (today - timedelta(days=delta)).isoformat()
            if log.get(d) == "succeeded":
                continue
            try:
                parse_day(d, uid)
                print(f"[batch] catch-up parsed user={uid} day={d}")
            except Exception:
                print(f"[batch] catch-up failed for user={uid} day={d}")
                traceback.print_exc()

        # Project all `succeeded` days into Neo4j. Picks up days parsed in
        # earlier runs whose graph projection never ran (the original bug:
        # catch_up was Postgres-only).
        try:
            maint = graph_maintenance.run(uid)
            print(f"[batch] catch-up graph reconcile user={uid}: {maint}")
        except Exception:
            print(f"[batch] catch-up graph reconcile failed user={uid}")
            traceback.print_exc()

        # Post today's morning brief if it hasn't been posted yet
        # (idempotent — returns 'already_done' otherwise, no LLM call).
        try:
            from . import morning_brief
            brief = morning_brief.post_morning_brief(today_iso, uid)
            print(f"[batch] catch-up morning brief user={uid}: {brief}")
        except Exception:
            print(f"[batch] catch-up morning brief failed user={uid}")
            traceback.print_exc()


def backfill_all_message_days(user_id: UUID) -> int:
    """Parse every day-bucket that has at least one user message for this
    user. Idempotent (delegates to `parse_day`)."""
    days = [r["day"] for r in get_days_with_messages(user_id)]

    for d in days:
        try:
            parse_day(d, user_id)
            print(f"[batch] backfill parsed user={user_id} day={d}")
        except Exception:
            print(f"[batch] backfill failed for user={user_id} day={d}")
            traceback.print_exc()
    return len(days)


def run_scheduled_batch() -> None:
    """Cron entrypoint. For every user with messages: parses yesterday's
    bucket, writes to Neo4j, posts the morning brief into a fresh conversation.
    """
    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    today = current_bucket().isoformat()

    user_ids = get_all_user_ids_with_messages()
    if not user_ids:
        print("[batch] no users with messages; nothing to do")
        return

    for uid in user_ids:
        parse_ok = False
        try:
            parse_day(yesterday, uid)
            print(f"[batch] scheduled parse complete user={uid} day={yesterday}")
            parse_ok = True
        except Exception:
            print(f"[batch] scheduled parse failed user={uid} day={yesterday}")
            traceback.print_exc()

        if parse_ok:
            try:
                result = graph_batch.write_day(yesterday, uid)
                print(f"[batch] graph write user={uid}: {result}")
                maint = graph_maintenance.run(uid)
                print(f"[batch] maintenance user={uid}: {maint}")
            except Exception:
                print(f"[batch] graph pipeline failed user={uid} day={yesterday}")
                traceback.print_exc()

        try:
            from . import morning_brief
            brief = morning_brief.post_morning_brief(today, uid)
            print(f"[batch] morning brief user={uid}: {brief}")
        except Exception:
            print(f"[batch] morning brief failed user={uid} day={today}")
            traceback.print_exc()
