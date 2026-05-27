"""Dashboard endpoint.

Returns the last 7 day-buckets of parsed data plus the most recent events and
todos. Legacy per-message rows (where `day IS NULL`) are excluded — they
predate the deferred-batch architecture and aren't keyed by day-bucket.
"""
from datetime import timedelta

from fastapi import APIRouter

from ..db import connect, loads
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
            WHERE day IS NOT NULL AND day >= ?
            ORDER BY day DESC
        """, (seven_back,))
        emotional = []
        for r in cursor.fetchall():
            d = dict(r)
            d["cognitive_labels"] = loads(d.get("cognitive_labels"))
            d["cognitive_triggers"] = loads(d.get("cognitive_triggers"))
            d["social_interactions"] = loads(d.get("social_interactions"))
            emotional.append(d)

        cursor.execute("""
            SELECT day, sleep_quality, exercise_type, diet_quality,
                   somatic_sensations, physical_performance, supplements
            FROM health_metrics
            WHERE day IS NOT NULL AND day >= ?
            ORDER BY day DESC
        """, (seven_back,))
        health = []
        for r in cursor.fetchall():
            d = dict(r)
            d["somatic_sensations"] = loads(d.get("somatic_sensations"))
            d["supplements"] = loads(d.get("supplements"))
            health.append(d)

        cursor.execute("""
            SELECT day, deep_work_hours, shallow_work_hours,
                   time_block_adherence, cognitive_load, friction_points
            FROM productivity_metrics
            WHERE day IS NOT NULL AND day >= ?
            ORDER BY day DESC
        """, (seven_back,))
        productivity = []
        for r in cursor.fetchall():
            d = dict(r)
            d["friction_points"] = loads(d.get("friction_points"))
            productivity.append(d)

        cursor.execute("""
            SELECT day, title, description, tags, event_type
            FROM events
            WHERE day IS NOT NULL AND day >= ?
            ORDER BY day DESC, id DESC
            LIMIT 40
        """, (seven_back,))
        events = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT id, day, task_description, is_completed, due_date,
                   created_at, fulfilled_at, source_day
            FROM todos
            WHERE day >= ?
            ORDER BY day DESC, id ASC
        """, (seven_back,))
        todos: dict[str, list] = {}
        for r in cursor.fetchall():
            d = dict(r)
            todos.setdefault(d["day"], []).append(d)

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
        "todos": todos,
        "parse_log": parse_log,
    }
