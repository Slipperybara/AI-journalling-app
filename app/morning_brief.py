"""Morning brief generation and posting (multi-tenant).

Runs after the nightly batch (or via the admin endpoint) per user. Creates a
fresh conversation in today's bucket for the user and inserts a warm assistant
message: an empathetic recap of yesterday that reflects what happened and how
they seemed to feel, with optional light touches on a 7-day pattern, goal
momentum, or a single caring suggestion — then ends with "How are you feeling
today?". The brief text is persisted to `morning_brief_log.brief_text` so the
live bot can reuse each day's recap as a per-day summary in its conversational
memory (see `get_daily_summaries`).

Idempotent via `morning_brief_log` keyed on `(user_id, day)`. Cron, catch-up
sweeps, and manual admin re-triggers all collapse into a single posting per
user per day.
"""
import json
import traceback
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from . import analytics, goals as goals_svc
from .bot import store_assistant_message
from .core import client
from .db import connect
from .graph_db import graph_connect


def post_morning_brief(day: str, user_id: UUID) -> dict:
    """Top-level entrypoint. Returns {status, day, conversation_id, error?}.

    Status values: 'posted', 'already_done', 'skipped_empty', 'failed'.
    """
    uid = str(user_id)
    with connect() as conn:
        existing = conn.execute(
            "SELECT day, status, conversation_id FROM morning_brief_log WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchone()
    if existing is not None and existing["status"] in ("posted", "skipped_empty"):
        return {
            "status": "already_done",
            "day": day,
            "conversation_id": existing["conversation_id"],
        }

    try:
        context = _gather_context(day, user_id)
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, user_id=user_id, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    if context.get("is_brand_new_user"):
        brief = _generate_welcome_for_new_user()
        conv_id = _post_to_conversation(brief, day, user_id)
        _log(day, status="skipped_empty", conversation_id=conv_id, user_id=user_id, brief_text=brief)
        return {"status": "skipped_empty", "day": day, "conversation_id": conv_id}

    try:
        pattern = _detect_pattern(context) if _has_pattern_data(context) else ""
        brief = _generate_brief(context, pattern, is_sparse=context["is_sparse_yesterday"])
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, user_id=user_id, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    try:
        conv_id = _post_to_conversation(brief, day, user_id)
    except Exception as exc:
        traceback.print_exc()
        _log(day, status="failed", conversation_id=0, user_id=user_id, error=str(exc))
        return {"status": "failed", "day": day, "error": str(exc)}

    # Store the pre-rendered push body alongside the brief. Delivery is deferred
    # to the notify_delivery cron, which sends it at the user's chosen local time
    # (see app/notify_delivery.py) — generation no longer pushes directly.
    _log(
        day, status="posted", conversation_id=conv_id, user_id=user_id,
        brief_text=brief, push_body=_push_body(context),
    )
    analytics.capture(user_id, "morning_brief_posted", {
        "is_sparse": context["is_sparse_yesterday"],
        "has_pattern_data": _has_pattern_data(context),
        "active_goals_count": len(context["active_goals"]),
    })
    return {"status": "posted", "day": day, "conversation_id": conv_id}


def _push_body(context: dict) -> str:
    """Catchy morning-brief push body, led by yesterday's dominant emotion words
    (cognitive_labels, e.g. 'excited', 'anxious'). Falls back to a neutral line
    when yesterday had no captured affect."""
    emo = context.get("yesterday_emotion") or {}
    labels = [w for w in (emo.get("cognitive_labels") or []) if w]
    if labels:
        feeling = labels[0] if len(labels) == 1 else f"{labels[0]} and {labels[1]}"
        return f"Yesterday, you were feeling {feeling} — your reflection's ready."
    return "Your reflection from yesterday is ready."


def _gather_context(day: str, user_id: UUID) -> dict:
    """Pulls everything the brief LLM needs for one user: yesterday, last-7-
    days summary, active goals + goal momentum. Single-shot Postgres reads
    plus one Neo4j Cypher for momentum counts."""
    uid = str(user_id)
    yesterday = (datetime.fromisoformat(day).date() - timedelta(days=1)).isoformat()
    seven_back = (datetime.fromisoformat(day).date() - timedelta(days=7)).isoformat()

    with connect() as conn:
        parse_log = conn.execute(
            "SELECT status FROM parse_log WHERE user_id = %s AND day = %s",
            (uid, yesterday),
        ).fetchone()

        yest_emotion = conn.execute(
            "SELECT valence, arousal, primary_quadrant, cognitive_labels, "
            "cognitive_triggers FROM emotional_analysis WHERE user_id = %s AND day = %s",
            (uid, yesterday),
        ).fetchone()
        yest_health = conn.execute(
            "SELECT sleep_quality, exercise_type, diet_quality, somatic_sensations, "
            "physical_performance FROM health_metrics WHERE user_id = %s AND day = %s",
            (uid, yesterday),
        ).fetchone()
        yest_productivity = conn.execute(
            "SELECT deep_work_hours, shallow_work_hours, cognitive_load, "
            "friction_points FROM productivity_metrics WHERE user_id = %s AND day = %s",
            (uid, yesterday),
        ).fetchone()
        yest_events = conn.execute(
            "SELECT title, description, event_type, tags FROM events WHERE user_id = %s AND day = %s",
            (uid, yesterday),
        ).fetchall()

        seven_emotion = conn.execute(
            "SELECT day, valence, arousal, primary_quadrant FROM emotional_analysis "
            "WHERE user_id = %s AND day >= %s AND day < %s ORDER BY day",
            (uid, seven_back, day),
        ).fetchall()
        seven_health = conn.execute(
            "SELECT day, sleep_quality, exercise_type, diet_quality FROM health_metrics "
            "WHERE user_id = %s AND day >= %s AND day < %s ORDER BY day",
            (uid, seven_back, day),
        ).fetchall()
        seven_productivity = conn.execute(
            "SELECT day, deep_work_hours, cognitive_load FROM productivity_metrics "
            "WHERE user_id = %s AND day >= %s AND day < %s ORDER BY day",
            (uid, seven_back, day),
        ).fetchall()

    active_goals = [g["name"] for g in goals_svc.list_goals(user_id, status="active")]
    goal_momentum = _fetch_goal_momentum(active_goals, user_id) if active_goals else {}

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


def _fetch_goal_momentum(goal_names: list[str], user_id: UUID) -> dict[str, int]:
    """Counts CONTRIBUTES_TO events per active goal in the last 7 days, scoped
    to this user. Every label pattern in the Cypher carries `user_id` to
    prevent traversal-through-shared-node leaks."""
    try:
        with graph_connect() as session:
            result = session.run(
                """
                MATCH (g:Goal {user_id: $user_id})
                WHERE g.name IN $names AND g.status = 'active'
                OPTIONAL MATCH (g)<-[:CONTRIBUTES_TO]-(e:Event {user_id: $user_id})
                              <-[:HAD_EVENT]-(d:Day {user_id: $user_id})
                WHERE d.date >= date() - duration('P7D')
                RETURN g.name AS name, count(e) AS n
                """,
                user_id=str(user_id),
                names=goal_names,
            )
            return {r["name"]: r["n"] for r in result}
    except Exception:
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
                    "You are MindForge writing the user's morning message — the first "
                    "thing they see when they open the app today. Its heart is a warm, "
                    "empathetic recap of yesterday: you remember what they went through "
                    "and how they seemed to feel, and you reflect it back so they feel "
                    "seen. This same text is reused later as your memory of that day, so "
                    "make the recap genuinely capture what mattered.\n\n"
                    "Write a single, warm, conversational message:\n"
                    "1. Open with 'Good morning' or a natural variant — one sentence.\n"
                    "2. Recap yesterday in 2-4 sentences grounded in the actual data — "
                    "what happened and, gently, how they seemed to feel. Reflect and "
                    "validate rather than report. If is_sparse_yesterday=true, say warmly "
                    "that you don't have much from yesterday and skip to step 5.\n"
                    "3. OPTIONAL: if seven_day_pattern is non-empty AND genuinely worth a "
                    "gentle mention, weave it in one sentence. Otherwise omit.\n"
                    "4. OPTIONAL: if an active goal's momentum is genuinely worth noting, "
                    "mention ONE lightly and warmly. Otherwise omit.\n"
                    "5. OPTIONAL: only if it arises naturally from the recap, offer ONE "
                    "small, caring suggestion for today. Skip it freely — do NOT force "
                    "advice. Better warm than prescriptive.\n"
                    "6. End with the literal line: How are you feeling today?\n\n"
                    "Style: 3-6 sentences total. Plain prose. No bullets, no markdown, "
                    "no headings, no emoji. Empathetic and unhurried, never preachy or "
                    "form-like. The optional steps are optional — lead with warmth and "
                    "presence, not analysis."
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
        "to start fresh together. How are you feeling today?"
    )


def _post_to_conversation(brief_text: str, day: str, user_id: UUID) -> int:
    """Creates a new conversation in today's bucket for this user and inserts
    the brief as an assistant message. Returns the new conversation_id."""
    started_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), started_at),
        )
        conv_id = cursor.fetchone()["id"]
    store_assistant_message(conv_id, brief_text, user_id)
    return conv_id


def _log(
    day: str,
    status: str,
    conversation_id: int,
    user_id: UUID,
    error: Optional[str] = None,
    brief_text: Optional[str] = None,
    push_body: Optional[str] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO morning_brief_log (user_id, day, posted_at, conversation_id, status, error, brief_text, push_body)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, day) DO UPDATE SET
                posted_at = excluded.posted_at,
                conversation_id = excluded.conversation_id,
                status = excluded.status,
                error = excluded.error,
                brief_text = excluded.brief_text,
                push_body = excluded.push_body
            """,
            (str(user_id), day, datetime.now().isoformat(), conversation_id, status, error, brief_text, push_body),
        )


def get_daily_summaries(today: str, user_id: UUID, num_days: int = 5) -> list[dict]:
    """Return the last `num_days` morning-brief recaps for the live bot's
    conversational memory, newest first.

    A brief posted on day P recaps day P-1, so each returned recap is labeled
    with the day it actually covers (`recaps`). The window is the briefs posted
    on [today-(num_days+1) .. today-2], which cover days [today-(num_days+2) ..
    today-3] — i.e. the days just *before* the 2 full-transcript days the bot
    already carries, so there is no overlap.

    Fallback for rows written before `brief_text` existed: read the brief from
    the logged conversation's first assistant message. Days with neither are
    skipped.
    """
    uid = str(user_id)
    today_date = datetime.fromisoformat(today).date()
    newest_post = (today_date - timedelta(days=2)).isoformat()   # recaps today-3
    oldest_post = (today_date - timedelta(days=num_days + 1)).isoformat()

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT day, conversation_id, brief_text
            FROM morning_brief_log
            WHERE user_id = %s AND status = 'posted'
              AND day >= %s AND day <= %s
            ORDER BY day DESC
            """,
            (uid, oldest_post, newest_post),
        ).fetchall()

        summaries: list[dict] = []
        for r in rows:
            text = (r["brief_text"] or "").strip()
            if not text and r["conversation_id"]:
                first = conn.execute(
                    "SELECT content FROM messages WHERE user_id = %s AND conversation_id = %s "
                    "AND role = 'assistant' ORDER BY id ASC LIMIT 1",
                    (uid, r["conversation_id"]),
                ).fetchone()
                text = (first["content"].strip() if first and first["content"] else "")
            if not text:
                continue
            recaps = (datetime.fromisoformat(r["day"]).date() - timedelta(days=1)).isoformat()
            summaries.append({"recaps": recaps, "summary": text})
    return summaries
