"""Admin endpoints for manual batch triggering and parse inspection."""
from datetime import datetime, time, timedelta

from fastapi import APIRouter, HTTPException

from ..batch import parse_day
from ..core import settings
from ..day_messages import get_days_with_messages, get_messages_for_day
from ..db import connect
from .. import morning_brief


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/parse-day/{day}")
async def trigger_parse_day(day: str):
    """Manually parse a single day-bucket. `day` is 'YYYY-MM-DD'.
    Idempotent — existing rows for the day are replaced."""
    try:
        return parse_day(day)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse failed: {e}")


@router.post("/morning-brief/{day}")
async def trigger_morning_brief(day: str):
    """Manually run the morning brief for one day-bucket. Idempotent via
    morning_brief_log — returns the existing conversation_id on re-trigger."""
    try:
        return morning_brief.post_morning_brief(day)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Morning brief failed: {e}")


@router.get("/inspect/days")
async def list_inspect_days():
    """Every day-bucket that has at least one user message, newest first.
    Powers the day picker in the frontend inspector."""
    return get_days_with_messages()


def _bucket_window(day: str) -> dict:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Bad day format, expected YYYY-MM-DD: {e}")
    start = datetime.combine(d, time(hour=settings.day_boundary_hour))
    end = start + timedelta(days=1)
    return {"start": start.isoformat(), "end": end.isoformat()}


@router.get("/inspect/{day}")
async def inspect_day(day: str):
    """Return the raw chat transcript + all extractions + parse_log for one
    day-bucket. Read-only — does not trigger a parse."""
    window = _bucket_window(day)
    messages = get_messages_for_day(day)

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT valence, arousal, primary_quadrant,
                   cognitive_labels, cognitive_triggers, social_interactions
            FROM emotional_analysis WHERE day = %s
        """, (day,))
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
            FROM health_metrics WHERE day = %s
        """, (day,))
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
            FROM productivity_metrics WHERE day = %s
        """, (day,))
        row = cursor.fetchone()
        if row:
            productivity = dict(row)
            productivity["friction_points"] = (productivity.get("friction_points") or [])
        else:
            productivity = None

        cursor.execute("""
            SELECT id, title, description, tags, event_type
            FROM events WHERE day = %s
            ORDER BY id ASC
        """, (day,))
        events = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT day, status, parsed_at, error
            FROM parse_log WHERE day = %s
        """, (day,))
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
async def eval_day(day: str):
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
