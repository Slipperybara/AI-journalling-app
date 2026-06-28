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
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from .day_messages import (
    get_all_user_ids_with_messages,
    get_days_with_messages,
    get_messages_for_day,
)
from .db import EXTRACTION_TABLES, connect
from .extractions import store_extractions
from .notifications_prefs import get_user_tz
from .parser import parse_day_content
from .time_buckets import bucket_for
from .core import settings
from . import graph_batch, graph_maintenance


def _fetch_day_messages(day: str, user_id: UUID, tz: Optional[str] = None) -> List[dict]:
    return get_messages_for_day(day, user_id, roles=('user',), tz=tz)


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


def parse_day(day: str, user_id: UUID, tz: Optional[str] = None) -> dict:
    """Parse a single day-bucket for one user. Idempotent — safe to re-run.
    `tz` selects the user's local day-bucket (None = legacy UTC bucket).
    Returns a small summary dict the admin endpoint can return."""
    messages = _fetch_day_messages(day, user_id, tz)

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


def _parse_status(user_id: UUID, day: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM parse_log WHERE user_id = %s AND day = %s",
            (str(user_id), day),
        ).fetchone()
    return row["status"] if row else None


def _brief_done(user_id: UUID, day: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM morning_brief_log WHERE user_id = %s AND day = %s",
            (str(user_id), day),
        ).fetchone()
    return bool(row and row["status"] in ("posted", "skipped_empty"))


def process_user_due(uid: UUID, now_utc: datetime) -> dict:
    """Run one user's pipeline IF their local day-boundary has passed and today's
    brief isn't already done — bucketed in their own timezone. Idempotent and
    safe to call hourly: once a user's local day is handled, later ticks no-op.

    Steps (gated): parse the user's local yesterday (skip if already succeeded) →
    project to Neo4j + maintenance (only when a NEW day was just parsed, so the
    gpt-4o categorize cost stays ~once/user/day) → post today's morning brief →
    refresh the dashboard summary.
    """
    tz = get_user_tz(uid)
    # Gate on the user's local wall-clock hour: before their 06:00 boundary,
    # yesterday's bucket isn't sealed yet, so there's nothing fresh to generate.
    try:
        from zoneinfo import ZoneInfo
        local_dt = now_utc.astimezone(ZoneInfo(tz))
    except Exception:
        local_dt = now_utc
    if local_dt.hour < settings.day_boundary_hour:
        return {"status": "before_local_boundary", "tz": tz}

    today_local = bucket_for(now_utc, tz)
    today_iso = today_local.isoformat()
    yesterday_iso = (today_local - timedelta(days=1)).isoformat()

    # The morning brief is the terminal step; if it's done, the whole local day
    # is handled — nothing more to do this tick.
    if _brief_done(uid, today_iso):
        return {"status": "already_done", "day": today_iso, "tz": tz}

    result: dict = {"tz": tz, "day": today_iso, "yesterday": yesterday_iso}

    did_parse = False
    if _parse_status(uid, yesterday_iso) != "succeeded":
        try:
            pres = parse_day(yesterday_iso, uid, tz)
            result["parse"] = pres
            did_parse = pres.get("status") == "succeeded"
        except Exception as exc:
            traceback.print_exc()
            result["parse"] = {"status": "failed", "error": str(exc)}
    else:
        result["parse"] = {"status": "already_succeeded"}

    # Only run the (LLM-costly) graph pipeline when a new day was just parsed.
    if did_parse:
        try:
            result["graph"] = graph_batch.write_day(yesterday_iso, uid)
            result["maintenance"] = graph_maintenance.run(uid, tz)
        except Exception as exc:
            traceback.print_exc()
            result["graph"] = {"status": "failed", "error": str(exc)}

    try:
        from . import morning_brief
        result["morning_brief"] = morning_brief.post_morning_brief(today_iso, uid)
    except Exception as exc:
        traceback.print_exc()
        result["morning_brief"] = {"status": "failed", "error": str(exc)}

    try:
        from . import dashboard_summary
        result["dashboard_summary"] = dashboard_summary.refresh_dashboard_summary(uid, tz)
    except Exception as exc:
        traceback.print_exc()
        result["dashboard_summary"] = {"status": "failed", "error": str(exc)}

    return result


def catch_up_parses(
    days_back: int = 7,
    user_id: Optional[UUID] = None,
    now_utc: Optional[datetime] = None,
) -> None:
    """Sweep the last N local buckets and bring each user fully up to date:
    parse missing local days (Postgres) + reconcile graph (Neo4j), then run the
    due-check so today's brief posts when their local morning has arrived.

    Re-running is safe: `parse_day` skips re-work via the `succeeded` guard,
    `graph_maintenance.run` is idempotent, and `process_user_due` early-returns
    once the brief is posted.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    user_ids = [user_id] if user_id is not None else get_all_user_ids_with_messages()

    for uid in user_ids:
        tz = get_user_tz(uid)
        today_local = bucket_for(now_utc, tz)

        with connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT day, status FROM parse_log WHERE user_id = %s",
                (str(uid),),
            )
            log = {r["day"]: r["status"] for r in cursor.fetchall()}

        for delta in range(1, days_back + 1):
            d = (today_local - timedelta(days=delta)).isoformat()
            if log.get(d) == "succeeded":
                continue
            try:
                parse_day(d, uid, tz)
                print(f"[batch] catch-up parsed user={uid} day={d}")
            except Exception:
                print(f"[batch] catch-up failed for user={uid} day={d}")
                traceback.print_exc()

        # Project all `succeeded` days into Neo4j (picks up days whose graph
        # projection never ran).
        try:
            maint = graph_maintenance.run(uid, tz)
            print(f"[batch] catch-up graph reconcile user={uid}: {maint}")
        except Exception:
            print(f"[batch] catch-up graph reconcile failed user={uid}")
            traceback.print_exc()

        # Post today's brief + refresh the summary, gated on the local boundary.
        try:
            res = process_user_due(uid, now_utc)
            print(f"[batch] catch-up due-check user={uid}: {res.get('status') or 'processed'}")
        except Exception:
            print(f"[batch] catch-up due-check failed user={uid}")
            traceback.print_exc()


def backfill_all_message_days(user_id: UUID) -> int:
    """Parse every day-bucket that has at least one user message for this
    user, in their local timezone. Idempotent (delegates to `parse_day`)."""
    tz = get_user_tz(user_id)
    days = [r["day"] for r in get_days_with_messages(user_id, tz)]

    for d in days:
        try:
            parse_day(d, user_id, tz)
            print(f"[batch] backfill parsed user={user_id} day={d}")
        except Exception:
            print(f"[batch] backfill failed for user={user_id} day={d}")
            traceback.print_exc()
    return len(days)


def run_scheduled_batch(now_utc: Optional[datetime] = None) -> None:
    """Cron entrypoint (now hourly). For every user with messages, runs the
    per-user, timezone-aware due-check — generating their brief when their local
    morning arrives. Idempotent across the day's hourly ticks."""
    now_utc = now_utc or datetime.now(timezone.utc)
    user_ids = get_all_user_ids_with_messages()
    if not user_ids:
        print("[batch] no users with messages; nothing to do")
        return

    for uid in user_ids:
        try:
            res = process_user_due(uid, now_utc)
            print(f"[batch] user={uid}: {res.get('status') or 'processed'}")
        except Exception:
            print(f"[batch] pipeline failed user={uid}")
            traceback.print_exc()
