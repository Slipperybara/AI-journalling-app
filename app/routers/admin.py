"""Admin endpoints for manual batch triggering and parse inspection.

Phase 2: per-user endpoints take `user_id` via the auth dependency and
scope all reads/writes by it.

Phase 4: adds `/api/admin/run-batch` — HMAC-protected, no user auth — for
the external GitHub Actions cron. Iterates every user with data and runs
the full per-user pipeline (parse yesterday + reconcile graph + post
today's morning brief).
"""
import hmac
import traceback
from datetime import datetime, time, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from ..auth import get_current_user_id
from ..batch import parse_day
from ..core import settings
from ..day_messages import (
    get_all_user_ids_with_messages,
    get_days_with_messages,
    get_messages_for_day,
)
from ..db import connect
from .. import graph_batch, graph_maintenance, morning_brief
from ..time_buckets import current_bucket


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/run-batch")
async def run_batch_webhook(
    x_webhook_secret: Optional[str] = Header(default=None, alias="X-Webhook-Secret"),
):
    """External cron entrypoint (GitHub Actions). HMAC-protected; no user auth.

    For every user with messages, parses yesterday's bucket into Postgres,
    projects into Neo4j, runs maintenance, posts today's morning brief.
    Idempotent — running it twice the same day re-uses already-parsed days
    and the existing morning brief (see the dedup table in the plan).

    Returns a per-user summary so the GitHub Action log captures what fired.
    """
    if not settings.batch_webhook_secret:
        # Misconfiguration safety: never accept any header when no secret is
        # configured. Avoids accidental exposure on a server that meant to
        # rotate the secret to empty.
        raise HTTPException(
            status_code=503,
            detail="batch webhook secret not configured",
        )
    if not x_webhook_secret or not hmac.compare_digest(
        x_webhook_secret, settings.batch_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    today = current_bucket().isoformat()
    user_ids = get_all_user_ids_with_messages()
    results: dict[str, dict] = {}

    for uid in user_ids:
        per_user: dict = {}
        try:
            per_user["parse"] = parse_day(yesterday, uid)
        except Exception as exc:
            traceback.print_exc()
            per_user["parse"] = {"status": "failed", "error": str(exc)}
        try:
            per_user["graph"] = graph_batch.write_day(yesterday, uid)
        except Exception as exc:
            traceback.print_exc()
            per_user["graph"] = {"status": "failed", "error": str(exc)}
        try:
            per_user["maintenance"] = graph_maintenance.run(uid)
        except Exception as exc:
            traceback.print_exc()
            per_user["maintenance"] = {"status": "failed", "error": str(exc)}
        try:
            per_user["morning_brief"] = morning_brief.post_morning_brief(today, uid)
        except Exception as exc:
            traceback.print_exc()
            per_user["morning_brief"] = {"status": "failed", "error": str(exc)}
        results[str(uid)] = per_user

    return {
        "yesterday": yesterday,
        "today": today,
        "users_processed": len(user_ids),
        "results": results,
    }


@router.post("/parse-day/{day}")
async def trigger_parse_day(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Manually parse a single day-bucket for the current user. `day` is
    'YYYY-MM-DD'. Idempotent — existing rows for the (user, day) are replaced."""
    try:
        return parse_day(day, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse failed: {e}")


@router.post("/morning-brief/{day}")
async def trigger_morning_brief(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Manually run the morning brief for one day-bucket for the current user.
    Idempotent via morning_brief_log — returns the existing conversation_id
    on re-trigger."""
    try:
        return morning_brief.post_morning_brief(day, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Morning brief failed: {e}")


@router.get("/inspect/days")
async def list_inspect_days(user_id: UUID = Depends(get_current_user_id)):
    """Every day-bucket that has at least one user message for the current
    user, newest first. Powers the day picker in the frontend inspector."""
    return get_days_with_messages(user_id)


def _bucket_window(day: str) -> dict:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Bad day format, expected YYYY-MM-DD: {e}")
    start = datetime.combine(d, time(hour=settings.day_boundary_hour))
    end = start + timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat()}


@router.get("/inspect/{day}")
async def inspect_day(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Return the raw chat transcript + all extractions + parse_log for one
    (user, day-bucket). Read-only — does not trigger a parse."""
    window = _bucket_window(day)
    messages = get_messages_for_day(day, user_id)
    uid = str(user_id)

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT valence, arousal, primary_quadrant,
                   cognitive_labels, cognitive_triggers, social_interactions
            FROM emotional_analysis WHERE user_id = %s AND day = %s
        """, (uid, day))
        row = cursor.fetchone()
        if row:
            emotional = dict(row)
            emotional["cognitive_labels"] = (emotional.get("cognitive_labels") or [])
            emotional["cognitive_triggers"] = (emotional.get("cognitive_triggers") or [])
            emotional["social_interactions"] = (emotional.get("social_interactions") or [])
        else:
            emotional = None

        cursor.execute("""
            SELECT sleep_quality, exercise_type, diet_quality,
                   somatic_sensations, physical_performance, supplements
            FROM health_metrics WHERE user_id = %s AND day = %s
        """, (uid, day))
        row = cursor.fetchone()
        if row:
            health = dict(row)
            health["somatic_sensations"] = (health.get("somatic_sensations") or [])
            health["supplements"] = (health.get("supplements") or [])
        else:
            health = None

        cursor.execute("""
            SELECT deep_work_hours, shallow_work_hours, time_block_adherence,
                   cognitive_load, friction_points
            FROM productivity_metrics WHERE user_id = %s AND day = %s
        """, (uid, day))
        row = cursor.fetchone()
        if row:
            productivity = dict(row)
            productivity["friction_points"] = (productivity.get("friction_points") or [])
        else:
            productivity = None

        cursor.execute("""
            SELECT id, title, description, tags, event_type
            FROM events WHERE user_id = %s AND day = %s
            ORDER BY id ASC
        """, (uid, day))
        events = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT day, status, parsed_at, error
            FROM parse_log WHERE user_id = %s AND day = %s
        """, (uid, day))
        log_row = cursor.fetchone()
        parse_log = dict(log_row) if log_row else None

    return {
        "day": day,
        "bucket_window": window,
        "messages": messages,
        "extractions": {
            "emotional": emotional,
            "health": health,
            "productivity": productivity,
            "events": events,
        },
        "parse_log": parse_log,
    }


@router.post("/eval/{day}")
async def eval_day(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Scaffold for the automatic parse evaluator. A future implementation
    will run a higher-tier model (e.g., gpt-4o) over the raw transcript and
    extractions returned by `/inspect/{day}` and grade each field against
    the rubric below."""
    return {
        "day": day,
        "status": "not_implemented",
        "message": (
            "Auto-eval will use a higher-tier model to grade each extracted "
            "field against the raw transcript. Coming in a follow-up."
        ),
        "planned_rubric": [
            "mention_present",
            "value_supported",
            "no_hallucination",
            "completeness",
        ],
    }
