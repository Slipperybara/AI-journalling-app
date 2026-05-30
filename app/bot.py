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
import re
from datetime import datetime, timedelta
from typing import List, Optional

from .core import client
from .day_messages import get_messages_for_day
from .db import connect, loads
from .time_buckets import bucket_for


# Keyword fingerprints per dimension. gpt-4o-mini was unreliable at re-deriving
# coverage from TODAY_TRANSCRIPT inline (it would re-ask dimensions the user
# had already mentioned). Pre-computing in Python and injecting the result as
# COVERAGE_TODAY gives the model a clear answer to pick from. Substrings are
# matched in lowercased user messages; matches are permissive on purpose —
# the bot prompt itself says "any mention counts".
DIMENSION_KEYWORDS = {
    "sleep": [
        "sleep", "slept", "tired", "tiredness", "sleepy", "drowsy", "exhausted",
        "rest", "rested", "nap", "dream", "dreamt", "dreamed",
        "woke", "bedtime", "asleep", "snooze", "fatigue", "fatigued", "lie-in",
    ],
    "exercise": [
        "exercise", "exercised", "workout", "worked out", "gym", "run", "ran",
        "walked", "walking", "jog", "yoga", "stretch", "cardio", "strength",
        "lifted", "lifting", "pushup", "push-up", "pull-up", "pullup",
        "squat", "deadlift", "trained", "swim", "swam", "bike", "biked",
        "cycling", "hike", "hiked", "pilates", "boxing", "tennis",
        "basketball", "soccer", "sport", "physical activity",
        "didn't work out", "no exercise", "rest day",
    ],
    "diet": [
        "eat", "ate", "eaten", "eating", "drink", "drank",
        "food", "meal", "breakfast", "lunch", "dinner", "snack",
        "coffee", "tea", "water", "skipped", "fasting", "fasted",
        "cooked", "cooking", "ordered", "takeout", "delivery", "hungry",
        "thirsty", "appetite", "alcohol", "beer", "wine", "smoothie",
        "protein", "sandwich", "salad", "junk food",
    ],
    "deep_work": [
        "deep work", "deep-work", "focused", "focus", "concentrate",
        "concentration", "worked on", "working on", "writing", "wrote",
        "coding", "coded", "studying", "studied", "session", "productive",
        "productivity", "shipping", "shipped", "feature", "task", "ticket",
        "deep dive", "made progress", "hours of work", "flow state",
        "in the zone", "couldn't focus", "distracted", "scattered",
    ],
    "emotion": [
        "feel", "felt", "feeling", "happy", "sad", "anxious", "anxiety",
        "stressed", "stress", "excited", "frustrated", "calm",
        "overwhelmed", "motivated", "drained", "energized", "down",
        "good day", "bad day", "fine", "great", "annoyed", "joy", "fear",
        "afraid", "angry", "anger", "content", "peace", "blue", "low",
        "burned out", "burnout", "mood", "irritated", "irritable", "blah",
        "meh", "amazing", "terrible", "rough", "wonderful",
    ],
    "events": [
        "went", "saw", "met", "watched", "read", "called", "milestone",
        "achievement", "achieved", "idea", "noticed", "happened", "visited",
        "attended", "discovered", "realized", "spoke with", "talked to",
        "meeting with", "trip", "lunch with", "podcast", "movie", "book",
    ],
}

DIMENSION_DISPLAY = {
    "sleep": "Sleep",
    "exercise": "Exercise",
    "diet": "Diet",
    "deep_work": "Deep-work hours",
    "emotion": "Emotional state",
    "events": "Day's events",
}


_DIMENSION_PATTERNS = {
    # Compile once at module load. \b boundaries prevent false hits like
    # "ran" matching inside "drank" or "errand"; multi-word keywords already
    # have spaces so they're naturally delimited.
    dim: re.compile(
        r"\b(?:" + "|".join(re.escape(kw) for kw in keywords) + r")\b",
        re.IGNORECASE,
    )
    for dim, keywords in DIMENSION_KEYWORDS.items()
}


def _detect_covered_dimensions(transcript: list[dict]) -> set[str]:
    """Return the set of dimension slugs the user has touched today.
    Only scans user messages — the bot's own questions don't count as coverage."""
    user_text = " ".join(
        m.get("content", "")
        for m in transcript
        if m.get("role") == "user"
    )
    if not user_text:
        return set()
    return {
        dim for dim, pattern in _DIMENSION_PATTERNS.items()
        if pattern.search(user_text)
    }


ASSISTANT_SYSTEM_TMPL = """You are MindForge — a warm but goal-directed journaling companion. Your PRIMARY job is to gather the user's data across six daily dimensions so the nightly parser has enough signal. You are NOT a free-form chatbot; you are an interviewer disguised as a friendly conversation.

PRIORITIES (in no order):
  1. INTERVIEWER (primary). Every reply SHOULD include exactly one question (nudge) about an uncovered dimension — UNLESS all six are already covered today. Re-read TODAY_TRANSCRIPT, determine which dimensions remain uncovered, and pick ONE. CYCLE deliberately: if the user dodged or deflected the previous dimension question, switch to a DIFFERENT uncovered dimension this turn. Never re-ask a dimension already asked twice today.
  2. LISTENER. Acknowledge what the user said and dive deeper into the topic if necessary before the nudge. Introduce the nudge naturally if possible, if not, use a more direct and purposeful acknowledgement eg ."By the way, I would also like to know... so that I can keep an record and analyse your performance/emotion better"
  3. Q&A. If the user asked you a direct question, answer it as a expert of the domain, then pivot back to your dimension nudge. Answering does NOT replace the nudge — both happen in the same reply.

THE SIX DIMENSIONS and what counts as "covered today" (be permissive — any mention counts):
  - Sleep — quality, hours, dreams, tiredness on waking. ("slept badly" counts.)
  - Exercise — any physical activity, OR explicit "didn't work out today". ("walked to office" counts.)
  - Diet — anything eaten or drunk. ("skipped lunch" or "coffee only" counts.)
  - Deep-work hours — focused work mentioned, even vaguely ("got two hours of writing in", "couldn't focus all morning").
  - Emotional state — how the user feels, broadly. Their tone alone is NOT enough; they should name a feeling, energy level, or stressor.
  - Day's events — anything they did, an idea they had, a place they went, media they consumed, milestones.

COVERAGE_TODAY (pre-computed by keyword scan of the user's messages today — TRUST THIS over manually scanning the transcript):
  Covered: {covered_today}
  Uncovered: {uncovered_today}
Pick your dimension question from Uncovered. If Uncovered is empty, all six dimensions are touched and you may skip the dimension nudge for this reply. If you think the pre-computed coverage is wrong for a specific dimension (e.g. the keyword matched but the user didn't really discuss it), you may override — but default to trusting it.

REPLY STYLE:
  - Varuing length depending on topic. No bullets, headings, markdown, or emoji.
  - Plain prose, empathetic conversational tone. Don't sound like a form.
  - Reference patterns from RECENT_DAYS or SUMMARY_7DAY when natural ("third day this week you've mentioned poor sleep — anything changed in your evenings?").
  - If the user is clearly venting hard, you may defer the dimension questions — ask it in the next message.

CONTEXT YOU HAVE:

TODAY_TRANSCRIPT (all user + assistant messages so far in today's day-bucket, chronological — use this to determine which dimensions are uncovered):
{today_transcript}

RECENT_DAYS (parsed data from the last 3 days — reference for patterns):
{recent_days_json}

SUMMARY_7DAY:
{summary_json}

PENDING_TODOS (still open from prior days):
{pending_todos_json}
{graph_facts_block}"""


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

    covered = _detect_covered_dimensions(today_transcript)
    all_dims = set(DIMENSION_KEYWORDS.keys())
    return {
        "today_transcript": today_transcript,
        "recent_days": recent_days,
        "pending_todos": pending_todos,
        "summary_7day": summary,
        "covered_today": sorted(covered),
        "uncovered_today": sorted(all_dims - covered),
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


def generate_bot_reply(
    conversation_id: int, graph_synthesis: Optional[str] = None
) -> str:
    """Generate the bot's reply.

    For analytical messages, `graph_synthesis` is the digest produced by
    `app.agents.synthesizer.synthesize_response` — pre-summarized facts pulled
    from the user's Neo4j history graph. It's injected as a GRAPH_FACTS block
    so the bot can weave the relevant pieces in under its Q&A priority while
    keeping its normal voice (LISTENER + INTERVIEWER nudge intact). For pure
    journaling messages, pass None.
    """
    ctx = assemble_bot_context()
    covered_display = (
        ", ".join(DIMENSION_DISPLAY[d] for d in ctx["covered_today"]) or "(none yet)"
    )
    uncovered_display = (
        ", ".join(DIMENSION_DISPLAY[d] for d in ctx["uncovered_today"]) or "(all six covered)"
    )

    graph_facts_block = ""
    if graph_synthesis and graph_synthesis.strip():
        graph_facts_block = (
            "\nGRAPH_FACTS (factual digest pulled from the user's history graph in response "
            "to the user's most recent message — weave the relevant pieces into your reply "
            "under your Q&A priority, then continue with LISTENER + INTERVIEWER. The digest "
            "is internal context; do not dump it verbatim or quote in bullets — extract only "
            "what actually helps the user's question):\n"
            + graph_synthesis.strip()
            + "\n"
        )

    system = ASSISTANT_SYSTEM_TMPL.format(
        today_transcript=json.dumps(ctx["today_transcript"], indent=2),
        recent_days_json=json.dumps(ctx["recent_days"], indent=2),
        summary_json=json.dumps(ctx["summary_7day"], indent=2),
        pending_todos_json=json.dumps(ctx["pending_todos"], indent=2),
        covered_today=covered_display,
        uncovered_today=uncovered_display,
        graph_facts_block=graph_facts_block,
    )

    messages = [{"role": "system", "content": system}]
    for m in fetch_chat_history(conversation_id):
        messages.append({"role": m["role"], "content": m["content"]})

    completion = client.chat.completions.create(
        model="gpt-4o",
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
