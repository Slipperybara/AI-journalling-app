"""Persist a day's `JournalParserResponse` into the day-keyed structured tables.

Callers (the batch in `app.batch`) are responsible for deleting prior rows for
the same `day` before calling this — keeping write-side idempotency at the
call site, not here.
"""
import json

from .db import connect
from .models import JournalParserResponse
from .parser import is_health_meaningful, is_productivity_meaningful


def store_extractions(parsed: JournalParserResponse, day: str) -> None:
    with connect() as conn:
        cursor = conn.cursor()

        e = parsed.emotions
        cursor.execute("""
            INSERT INTO emotional_analysis
            (day, valence, arousal, primary_quadrant,
             cognitive_labels, cognitive_triggers, social_interactions)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
        """, (
            day, e.valence, e.arousal, e.primary_quadrant,
            json.dumps(e.cognitive_labels),
            json.dumps(e.cognitive_triggers),
            json.dumps(e.social_interactions),
        ))

        if is_health_meaningful(parsed.health):
            h = parsed.health
            cursor.execute("""
                INSERT INTO health_metrics
                (day, sleep_quality, exercise_type, diet_quality,
                 somatic_sensations, physical_performance, supplements)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
            """, (
                day, h.sleep_quality, h.exercise_type, h.diet_quality,
                json.dumps(h.somatic_sensations), h.physical_performance, json.dumps(h.supplements),
            ))

        if is_productivity_meaningful(parsed.productivity):
            p = parsed.productivity
            cursor.execute("""
                INSERT INTO productivity_metrics
                (day, deep_work_hours, shallow_work_hours,
                 time_block_adherence, cognitive_load, friction_points)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            """, (
                day, p.deep_work_hours, p.shallow_work_hours,
                p.time_block_adherence, p.cognitive_load, json.dumps(p.friction_points),
            ))

        for ev in parsed.events:
            cursor.execute("""
                INSERT INTO events (day, title, description, tags, event_type)
                VALUES (%s, %s, %s, %s::jsonb, %s)
            """, (day, ev.title, ev.description, json.dumps(ev.tags), ev.event_type))

        for ev in parsed.events:
            for topic in (ev.topics or []):
                if topic.strip():
                    cursor.execute(
                        "INSERT INTO event_topics (day, event_title, topic) VALUES (%s, %s, %s)",
                        (day, ev.title, topic.strip()),
                    )
            for goal_name in (ev.contributes_to_goals or []):
                if goal_name.strip():
                    cursor.execute(
                        "INSERT INTO event_goal_contributions (day, event_title, goal_name) VALUES (%s, %s, %s)",
                        (day, ev.title, goal_name.strip()),
                    )
