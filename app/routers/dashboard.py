"""Dashboard endpoint.

Returns the last 7 day-buckets of parsed data plus the most recent events and
todos. Legacy per-message rows (where `day IS NULL`) are excluded — they
predate the deferred-batch architecture and aren't keyed by day-bucket.
"""
from datetime import timedelta

from fastapi import APIRouter

from ..db import connect
from ..time_buckets import current_bucket


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard():
    seven_back = (current_bucket() - timedelta(days=7)).isoformat()
    today_iso = current_bucket().isoformat()

    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT day, valence, arousal, primary_quadrant,
                   cognitive_labels, cognitive_triggers, social_interactions
            FROM emotional_analysis
            WHERE day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (seven_back,))
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
            WHERE day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (seven_back,))
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
            WHERE day IS NOT NULL AND day >= %s
            ORDER BY day DESC
        """, (seven_back,))
        productivity = []
        for r in cursor.fetchall():
            d = dict(r)
            d["friction_points"] = (d.get("friction_points") or [])
            productivity.append(d)

        cursor.execute("""
            SELECT day, title, description, tags, event_type
            FROM events
            WHERE day IS NOT NULL AND day >= %s
            ORDER BY day DESC, id DESC
            LIMIT 40
        """, (seven_back,))
        events = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT name, status, discovered_on, fulfilled_at, removed_at,
                   source, created_at
            FROM goals
            WHERE status IN ('active','fulfilled')
            ORDER BY created_at DESC
        """)
        goals: dict[str, list] = {"active": [], "fulfilled": []}
        for r in cursor.fetchall():
            d = dict(r)
            goals[d["status"]].append(d)

        cursor.execute("""
            SELECT day, status, parsed_at FROM parse_log
            ORDER BY day DESC LIMIT 10
        """)
        parse_log = [dict(r) for r in cursor.fetchall()]

    return {
        "today_bucket": today_iso,
        "emotional": emotional,
        "health": health,
        "productivity": productivity,
        "events": events,
        "goals": goals,
        "parse_log": parse_log,
    }
