"""The live chatbot.

The bot is the only LLM call per user message after this refactor — there is
no inline structured extraction. The bot's context is:
  - today's raw transcript (all today-bucket conversations, chronological);
  - the last 3 days of parsed rows;
  - the 7-day aggregate summary;
  - all still-open todos from prior days.

System-prompt priorities (in order when they conflict):
  1. Conversationalist — answer questions directly, don't deflect.
  2. Empathetic listener — reflect what the user shared. Surface any todos /
     events spotted in the reply itself (no DB writes here — those happen in
     the nightly batch).
  3. Gentle interviewer — at most one uncovered-dimension nudge per reply.
"""
import json
from datetime import datetime, timedelta
from typing import List, Optional

from .core import client
from .day_messages import get_messages_for_day
from .db import connect, loads
from .time_buckets import bucket_for


ASSISTANT_SYSTEM_TMPL = """You are MindForge — a warm journaling companion. Your job is to make space for the user's day across six daily dimensions so the nightly parser has enough signal. Be conversational and present, not pushy.

PRIORITIES (in this order; do not reorder):
  1. INTERVIEWER. Every reply MUST include exactly one natural question about an uncovered dimension — UNLESS all six are already covered today. Re-read TODAY_TRANSCRIPT, determine which dimensions remain uncovered, and pick ONE. CYCLE deliberately: if the user dodged or deflected the previous dimension question, switch to a DIFFERENT uncovered dimension this turn. Never re-ask a dimension already asked twice today.
  2. LISTENER. Acknowledge what the user said before the nudge. Reflect feelings, events, and observations back warmly. Do NOT offer to add anything to a todo list or goal list — todos and goals are managed entirely by the user from the dashboard; you do not create, track, or break them down for the user.
  3. CONCISE Q&A. If the user asked you a direct question, answer it, then pivot back to your dimension nudge. Answering does NOT replace the nudge — both happen in the same reply.

THE SIX DIMENSIONS and what counts as "covered today" (be permissive — any mention counts):
  - Sleep — quality, hours, dreams, tiredness on waking. ("slept badly" counts.)
  - Exercise — any physical activity, OR explicit "didn't work out today". ("walked to office" counts.)
  - Diet — anything eaten or drunk. ("skipped lunch" or "coffee only" counts.)
  - Deep-work hours — focused work mentioned, even vaguely ("got two hours of writing in", "couldn't focus all morning").
  - Emotional state — how the user feels, broadly. Their tone alone is NOT enough; they should name a feeling, energy level, or stressor.
  - Day's events — anything they did, an idea they had, a place they went, media they consumed, milestones.

If unsure whether a dimension is covered, treat it as NOT covered and probe it.

REPLY STYLE:
  - 2–4 short sentences. No bullets, headings, markdown, or emoji.
  - Plain prose, conversational tone. Don't sound like a form.
  - Reference patterns from RECENT_DAYS or SUMMARY_7DAY when natural ("third day this week you've mentioned poor sleep — anything changed in your evenings?").
  - If the user is clearly venting hard, you may shorten the acknowledge sentence but you still ask the dimension question — frame it gently.

CONTEXT YOU HAVE:

TODAY_TRANSCRIPT (all user + assistant messages so far in today's day-bucket, chronological — use this to determine which dimensions are uncovered):
{today_transcript}

RECENT_DAYS (parsed data from the last 3 days — reference for patterns):
{recent_days_json}

SUMMARY_7DAY:
{summary_json}

PENDING_TODOS (still open from prior days):
{pending_todos_json}
"""


def assemble_bot_context(now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    today_iso = bucket_for(now).isoformat()
    recent_cutoff = (bucket_for(now) - timedelta(days=3)).isoformat()
    seven_back = (bucket_for(now) - timedelta(days=7)).isoformat()

    today_transcript: list[dict] = []
    recent_days: dict[str, list] = {"emotional": [], "health": [], "productivity": [], "events": []}
    summary: dict = {}

    with connect() as conn:
        cursor = conn.cursor()

        today_transcript = [
            {"at": r["created_at"], "role": r["role"], "content": r["content"]}
            for r in get_messages_for_day(today_iso)
        ]

        cursor.execute("""
            SELECT day, valence, arousal, primary_quadrant,
                   cognitive_labels, cognitive_triggers, social_interactions
            FROM emotional_analysis
            WHERE day IS NOT NULL AND day >= ? AND day < ?
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["emotional"].append({
                "day": r["day"],
                "valence": r["valence"],
                "arousal": r["arousal"],
                "primary_quadrant": r["primary_quadrant"],
                "cognitive_labels": loads(r["cognitive_labels"]),
                "cognitive_triggers": loads(r["cognitive_triggers"]),
                "social_interactions": loads(r["social_interactions"]),
            })

        cursor.execute("""
            SELECT day, sleep_quality, exercise_type, diet_quality,
                   somatic_sensations, physical_performance, supplements
            FROM health_metrics
            WHERE day IS NOT NULL AND day >= ? AND day < ?
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["health"].append({
                "day": r["day"],
                "sleep_quality": r["sleep_quality"],
                "exercise_type": r["exercise_type"],
                "diet_quality": r["diet_quality"],
                "somatic_sensations": loads(r["somatic_sensations"]),
                "physical_performance": r["physical_performance"],
                "supplements": loads(r["supplements"]),
            })

        cursor.execute("""
            SELECT day, deep_work_hours, shallow_work_hours,
                   time_block_adherence, cognitive_load, friction_points
            FROM productivity_metrics
            WHERE day IS NOT NULL AND day >= ? AND day < ?
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["productivity"].append({
                "day": r["day"],
                "deep_work_hours": r["deep_work_hours"],
                "shallow_work_hours": r["shallow_work_hours"],
                "time_block_adherence": r["time_block_adherence"],
                "cognitive_load": r["cognitive_load"],
                "friction_points": loads(r["friction_points"]),
            })

        cursor.execute("""
            SELECT day, title, description, tags, event_type
            FROM events
            WHERE day IS NOT NULL AND day >= ? AND day < ?
            ORDER BY day DESC, id DESC
        """, (recent_cutoff, today_iso))
        recent_days["events"] = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT AVG(valence) v, AVG(arousal) a, COUNT(*) n
            FROM emotional_analysis
            WHERE day IS NOT NULL AND day >= ?
        """, (seven_back,))
        row = cursor.fetchone()
        summary["emotional_7day"] = {
            "avg_valence": round(row["v"], 2) if row["v"] is not None else None,
            "avg_arousal": round(row["a"], 2) if row["a"] is not None else None,
            "samples": row["n"],
        }

        cursor.execute("""
            SELECT primary_quadrant q, COUNT(*) n
            FROM emotional_analysis
            WHERE day IS NOT NULL AND day >= ?
            GROUP BY primary_quadrant
        """, (seven_back,))
        summary["quadrant_counts_7day"] = {r["q"]: r["n"] for r in cursor.fetchall() if r["q"]}

        cursor.execute("""
            SELECT event_type, COUNT(*) n
            FROM events
            WHERE day IS NOT NULL AND day >= ?
            GROUP BY event_type
        """, (seven_back,))
        summary["events_7day"] = {r["event_type"]: r["n"] for r in cursor.fetchall() if r["event_type"]}

        cursor.execute("""
            SELECT task_description, due_date, day
            FROM todos
            WHERE is_completed = 0
            ORDER BY id DESC
            LIMIT 20
        """)
        pending_todos = [dict(r) for r in cursor.fetchall()]
        summary["todos_pending"] = len(pending_todos)

    return {
        "today_transcript": today_transcript,
        "recent_days": recent_days,
        "pending_todos": pending_todos,
        "summary_7day": summary,
    }


def fetch_chat_history(conversation_id: int, limit: int = 30) -> List[dict]:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
        """, (conversation_id,))
        rows = [dict(r) for r in cursor.fetchall()]
    return rows[-limit:]


def generate_bot_reply(conversation_id: int) -> str:
    ctx = assemble_bot_context()
    system = ASSISTANT_SYSTEM_TMPL.format(
        today_transcript=json.dumps(ctx["today_transcript"], indent=2),
        recent_days_json=json.dumps(ctx["recent_days"], indent=2),
        summary_json=json.dumps(ctx["summary_7day"], indent=2),
        pending_todos_json=json.dumps(ctx["pending_todos"], indent=2),
    )

    messages = [{"role": "system", "content": system}]
    for m in fetch_chat_history(conversation_id):
        messages.append({"role": m["role"], "content": m["content"]})

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7,
    )
    return completion.choices[0].message.content.strip()


def store_assistant_message(conversation_id: int, content: str) -> int:
    created_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (?, 'assistant', ?, ?)
        """, (conversation_id, content, created_at))
        return cursor.lastrowid


def process_message_background(conversation_id: int, message_content: str) -> None:
    """Background-task entrypoint after a user message. Routes through LangGraph:
    journaling → existing bot; analytical → Neo4j Cypher pipeline."""
    try:
        from .langgraph_flow import process_message
        process_message(conversation_id, message_content)
    except Exception as e:
        print(f"[BG] Reply error for conv {conversation_id}: {e}")
        store_assistant_message(
            conversation_id,
            "Hmm, I had trouble responding just now. Want to try saying that again?",
        )
