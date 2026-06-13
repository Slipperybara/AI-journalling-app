"""Dashboard endpoint (per-user).

Returns the last 7 day-buckets of parsed data plus the most recent events for
the authenticated user. Phase 2: every query filters by `user_id`. Legacy
rows where `day IS NULL` are excluded — they predate the deferred-batch
architecture.
"""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends

from .. import analytics
from ..auth import get_current_user_id
from ..dashboard_summary import get_dashboard_summary
from ..db import connect
from ..time_buckets import bucket_sql_expr, current_bucket


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(user_id: UUID = Depends(get_current_user_id)):
    uid = str(user_id)
    seven_back = (current_bucket() - timedelta(days=7)).isoformat()
    today_iso = current_bucket().isoformat()

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT day, valence, arousal, primary_quadrant,
                   cognitive_labels, cognitive_triggers, social_interactions
            FROM emotional_analysis
            WHERE user_id = %s AND day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (uid, seven_back))
        emotional = []
        for r in cursor.fetchall():
            d = dict(r)
            d["cognitive_labels"] = (d.get("cognitive_labels") or [])
            d["cognitive_triggers"] = (d.get("cognitive_triggers") or [])
            d["social_interactions"] = (d.get("social_interactions") or [])
            emotional.append(d)

        cursor.execute("""
            SELECT day, sleep_quality, exercise_type, diet_quality,
                   somatic_sensations, physical_performance, supplements
            FROM health_metrics
            WHERE user_id = %s AND day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (uid, seven_back))
        health = []
        for r in cursor.fetchall():
            d = dict(r)
            d["somatic_sensations"] = (d.get("somatic_sensations") or [])
            d["supplements"] = (d.get("supplements") or [])
            health.append(d)

        cursor.execute("""
            SELECT day, deep_work_hours, shallow_work_hours,
                   time_block_adherence, cognitive_load, friction_points
            FROM productivity_metrics
            WHERE user_id = %s AND day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (uid, seven_back))
        productivity = []
        for r in cursor.fetchall():
            d = dict(r)
            d["friction_points"] = (d.get("friction_points") or [])
            productivity.append(d)

        cursor.execute("""
            SELECT day, title, description, tags, event_type
            FROM events
            WHERE user_id = %s AND day IS NOT NULL AND day >= %s
            ORDER BY day DESC, id DESC
            LIMIT 40
        """, (uid, seven_back))
        events = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT name, status, discovered_on, fulfilled_at, removed_at,
                   source, created_at
            FROM goals
            WHERE user_id = %s AND status IN ('active','fulfilled')
            ORDER BY created_at DESC
        """, (uid,))
        goals: dict[str, list] = {"active": [], "fulfilled": []}
        for r in cursor.fetchall():
            d = dict(r)
            goals[d["status"]].append(d)

        cursor.execute("""
            SELECT day, status, parsed_at FROM parse_log
            WHERE user_id = %s
            ORDER BY day DESC LIMIT 10
        """, (uid,))
        parse_log = [dict(r) for r in cursor.fetchall()]

        # Which day-buckets in the last 7 days have at least one user message —
        # powers the binary journaling tracker.
        bucket_expr = bucket_sql_expr("m.created_at")
        cursor.execute(f"""
            SELECT {bucket_expr}::text AS day
            FROM messages m
            WHERE m.user_id = %s AND m.role = 'user' AND {bucket_expr} >= %s
            GROUP BY day
        """, (uid, seven_back))
        journaled_days = {r["day"] for r in cursor.fetchall()}

    # Last 7 day-buckets, oldest → newest, each flagged journaled or not.
    today_bucket = current_bucket()
    journaling_week = []
    for i in range(6, -1, -1):
        d = (today_bucket - timedelta(days=i)).isoformat()
        journaling_week.append({"day": d, "journaled": d in journaled_days})

    analytics.capture(user_id, "dashboard_viewed")
    return {
        "today_bucket": today_iso,
        "summary": get_dashboard_summary(user_id),
        "journaling_week": journaling_week,
        "emotional": emotional,
        "health": health,
        "productivity": productivity,
        "events": events,
        "goals": goals,
        "parse_log": parse_log,
    }
