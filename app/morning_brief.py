"""Morning brief generation and posting.

Runs after the nightly batch (or via the admin endpoint). Creates a fresh
conversation in today's bucket and inserts a warm assistant message that
greets the user, summarizes yesterday, surfaces one notable 7-day pattern
if any, lightly references active goals and pending todos, and offers one
grounded suggestion — then ends with "How are you doing today?".

Idempotent via `morning_brief_log` keyed on `day`. Cron, catch-up sweeps,
and manual admin re-triggers all collapse into a single posting per day.
"""
import json
import traceback
from datetime import datetime, timedelta
from typing import Optional

from . import goals as goals_svc
from .bot import store_assistant_message
from .core import client
from .db import connect
from .graph_db import graph_connect


def post_morning_brief(day: str) -> dict:
    """Top-level entrypoint. Returns {status, day, conversation_id, error?}.

    Status values: 'posted', 'already_done', 'skipped_empty', 'failed'.
    """
    with connect() as conn:
        existing = conn.execute(
            "SELECT day, status, conversation_id FROM morning_brief_log WHERE day = %s",
            (day,),
        ).fetchone()
    if existing is not None and existing["status"] in ("posted", "skipped_empty"):
        return {
            "status": "already_done",
            "day": day,
            "conversation_id": existing["conversation_id"],
        }

    try:
        context = _gather_context(day)
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    if context.get("is_brand_new_user"):
        brief = _generate_welcome_for_new_user()
        conv_id = _post_to_conversation(brief, day)
        _log(day, status="skipped_empty", conversation_id=conv_id)
        return {"status": "skipped_empty", "day": day, "conversation_id": conv_id}

    try:
        pattern = _detect_pattern(context) if _has_pattern_data(context) else ""
        brief = _generate_brief(context, pattern, is_sparse=context["is_sparse_yesterday"])
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    try:
        conv_id = _post_to_conversation(brief, day)
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    _log(day, status="posted", conversation_id=conv_id)
    return {"status": "posted", "day": day, "conversation_id": conv_id}


def _gather_context(day: str) -> dict:
    """Pulls everything the brief LLM needs: yesterday, last-7-days summary,
    active goals + goal momentum, pending todos. Single-shot SQLite reads
    plus one Neo4j Cypher for momentum counts."""
    yesterday = (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()
    seven_back = (datetime.fromisoformat(day).date() - timedelta(days=7)).isoformat()

    with connect() as conn:
        parse_log = conn.execute(
            "SELECT status FROM parse_log WHERE day = %s", (yesterday,)
        ).fetchone()

        yest_emotion = conn.execute(
            "SELECT valence, arousal, primary_quadrant, cognitive_labels, "
            "cognitive_triggers FROM emotional_analysis WHERE day = %s",
            (yesterday,),
        ).fetchone()
        yest_health = conn.execute(
            "SELECT sleep_quality, exercise_type, diet_quality, somatic_sensations, "
            "physical_performance FROM health_metrics WHERE day = %s",
            (yesterday,),
        ).fetchone()
        yest_productivity = conn.execute(
            "SELECT deep_work_hours, shallow_work_hours, cognitive_load, "
            "friction_points FROM productivity_metrics WHERE day = %s",
            (yesterday,),
        ).fetchone()
        yest_events = conn.execute(
            "SELECT title, description, event_type, tags FROM events WHERE day = %s",
            (yesterday,),
        ).fetchall()

        seven_emotion = conn.execute(
            "SELECT day, valence, arousal, primary_quadrant FROM emotional_analysis "
            "WHERE day >= %s AND day < %s ORDER BY day",
            (seven_back, day),
        ).fetchall()
        seven_health = conn.execute(
            "SELECT day, sleep_quality, exercise_type, diet_quality FROM health_metrics "
            "WHERE day >= %s AND day < %s ORDER BY day",
            (seven_back, day),
        ).fetchall()
        seven_productivity = conn.execute(
            "SELECT day, deep_work_hours, cognitive_load FROM productivity_metrics "
            "WHERE day >= %s AND day < %s ORDER BY day",
            (seven_back, day),
        ).fetchall()

    active_goals = [g["name"] for g in goals_svc.list_goals(status="active")]
    goal_momentum = _fetch_goal_momentum(active_goals) if active_goals else {}

    yest_data_present = any([yest_emotion, yest_health, yest_productivity, yest_events])
    seven_data_present = any([seven_emotion, seven_health, seven_productivity])
    has_any_data = (
        yest_data_present
        or seven_data_present
        or active_goals
    )

    return {
        "day": day,
        "yesterday": yesterday,
        "yesterday_emotion": _row_or_none(yest_emotion),
        "yesterday_health": _row_or_none(yest_health),
        "yesterday_productivity": _row_or_none(yest_productivity),
        "yesterday_events": [dict(r) for r in yest_events],
        "seven_day_emotion": [dict(r) for r in seven_emotion],
        "seven_day_health": [dict(r) for r in seven_health],
        "seven_day_productivity": [dict(r) for r in seven_productivity],
        "active_goals": active_goals,
        "goal_momentum": goal_momentum,
        "parse_log_status": parse_log["status"] if parse_log else None,
        "is_sparse_yesterday": not yest_data_present,
        "is_brand_new_user": not has_any_data,
    }


def _row_or_none(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for k in ("cognitive_labels", "cognitive_triggers", "somatic_sensations", "friction_points"):
        if k in d:
            d[k] = (d[k] or [])
    return d


def _fetch_goal_momentum(goal_names: list[str]) -> dict[str, int]:
    """Counts CONTRIBUTES_TO events per active goal in the last 7 days."""
    try:
        with graph_connect() as session:
            result = session.run(
                """
                MATCH (g:Goal)
                WHERE g.name IN $names AND g.status = 'active'
                OPTIONAL MATCH (g)<-[:CONTRIBUTES_TO]-(e:Event)<-[:HAD_EVENT]-(d:Day)
                WHERE d.date >= date() - duration('P7D')
                RETURN g.name AS name, count(e) AS n
                """,
                names=goal_names,
            )
            return {r["name"]: r["n"] for r in result}
    except Exception:
        # Neo4j down or other transient — momentum is optional context.
        return {name: 0 for name in goal_names}


def _has_pattern_data(context: dict) -> bool:
    return (
        len(context["seven_day_emotion"]) >= 3
        or len(context["seven_day_health"]) >= 3
        or len(context["seven_day_productivity"]) >= 3
    )


def _detect_pattern(context: dict) -> str:
    """Compact gpt-4o call to surface ONE notable 7-day pattern. Returns
    empty string if nothing meaningful stands out."""
    seven_day = {
        "emotion": context["seven_day_emotion"],
        "health": context["seven_day_health"],
        "productivity": context["seven_day_productivity"],
    }
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You read the user's last 7 days of structured journaling data "
                    "and identify ONE notable pattern worth mentioning in a morning "
                    "brief. Return ONE concise sentence (max 25 words) — or return "
                    "exactly the word NONE if nothing meaningful stands out. "
                    "Examples of meaningful: 'three nights of poor sleep before each "
                    "High-Stress day this week', 'deep-work hours dropped from 4 to 1 "
                    "across the week'. Examples of NOT meaningful: small day-to-day "
                    "variation, single-day events. Do not invent — only what the data "
                    "shows."
                ),
            },
            {
                "role": "user",
                "content": "Last 7 days:\n" + json.dumps(seven_day, indent=2, default=str),
            },
        ],
        temperature=0.3,
    )
    text = response.choices[0].message.content.strip()
    if text.upper().startswith("NONE"):
        return ""
    return text


def _generate_brief(context: dict, pattern: str, is_sparse: bool) -> str:
    """Main gpt-4o call producing the user-facing brief."""
    payload = {
        "yesterday_date": context["yesterday"],
        "is_sparse_yesterday": is_sparse,
        "yesterday_emotion": context["yesterday_emotion"],
        "yesterday_health": context["yesterday_health"],
        "yesterday_productivity": context["yesterday_productivity"],
        "yesterday_events": context["yesterday_events"],
        "seven_day_pattern": pattern,
        "active_goals": [
            {"name": name, "events_last_7d": context["goal_momentum"].get(name, 0)}
            for name in context["active_goals"]
        ],
    }

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are MindForge writing the user's morning brief — the first "
                    "message they see when they open the app today. Write a single, "
                    "warm, conversational message:\n\n"
                    "1. Open with 'Good morning' or a natural variant — one sentence.\n"
                    "2. Summarize yesterday in 1-2 sentences grounded in the actual "
                    "data. If is_sparse_yesterday=true, say so kindly and skip to step 5.\n"
                    "3. If seven_day_pattern is non-empty, mention it in one sentence.\n"
                    "4. If active_goals show momentum or stalled status worth noting, "
                    "mention ONE lightly.\n"
                    "5. Offer ONE concrete, specific suggestion for today, grounded "
                    "in the data — not generic advice. Skip this step if the data is "
                    "too sparse to justify it.\n"
                    "6. End with the literal line: How are you doing today?\n\n"
                    "Style: 4-7 sentences total. Plain prose. No bullets, no markdown, "
                    "no headings, no emoji. Empathetic, not preachy. Skip steps 3-5 if "
                    "the data doesn't justify them — better brief and warm than padded."
                ),
            },
            {
                "role": "user",
                "content": "Brief context:\n" + json.dumps(payload, indent=2, default=str),
            },
        ],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def _generate_welcome_for_new_user() -> str:
    return (
        "Good morning. I'm MindForge — your space to talk through your day, "
        "track your goals, and notice the patterns that shape how you feel "
        "and work. There's no data to look back on yet, which means we get "
        "to start fresh together. How are you doing today?"
    )


def _post_to_conversation(brief_text: str, day: str) -> int:
    """Creates a new conversation in today's bucket and inserts the brief as
    an assistant message. Returns the new conversation_id."""
    started_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (started_at) VALUES (%s) RETURNING id", (started_at,)
        )
        conv_id = cursor.fetchone()["id"]
    store_assistant_message(conv_id, brief_text)
    return conv_id


def _log(day: str, status: str, conversation_id: int, error: Optional[str] = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO morning_brief_log (day, posted_at, conversation_id, status, error)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(day) DO UPDATE SET
                posted_at = excluded.posted_at,
                conversation_id = excluded.conversation_id,
                status = excluded.status,
                error = excluded.error
            """,
            (day, datetime.now().isoformat(), conversation_id, status, error),
        )
