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
from .db import connect
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

GOAL TOOLS (when to call them):
  - `add_goal(name)`: ONLY after the user explicitly confirms they want a goal tracked. When the user mentions a long-term aim ("I want to train for a marathon", "I'm going to focus on Jane Street prep"), FIRST ask in conversation whether to track it — do NOT call add_goal yet. If on a later turn they confirm ("yes please", "yeah track it"), THEN call add_goal. The 3-active cap is enforced server-side; if the call returns an error about the cap, tell the user they need to fulfill or remove a goal first.
  - `fulfill_goal(name)`: when the user clearly says they've completed or are done with a tracked goal ("I finished Jane Street prep", "I hit my marathon"). Call immediately with the matching active goal name; no confirmation needed.
  - `remove_goal(name)`: when the user says they're dropping or want to remove a tracked goal ("drop Jane Street prep", "remove that"). Call immediately with the matching name.
  - `rename_goal(old_name, new_name)`: when the user explicitly asks to rename a goal ("rename Jane Street to JS Prep"). Call immediately.
  After a tool call, generate ONE user-facing reply acknowledging what changed in plain prose. Do not list the tool result verbatim.

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
            WHERE day IS NOT NULL AND day >= %s AND day < %s
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["emotional"].append({
                "day": r["day"],
                "valence": r["valence"],
                "arousal": r["arousal"],
                "primary_quadrant": r["primary_quadrant"],
                "cognitive_labels": (r["cognitive_labels"] or []),
                "cognitive_triggers": (r["cognitive_triggers"] or []),
                "social_interactions": (r["social_interactions"] or []),
            })

        cursor.execute("""
            SELECT day, sleep_quality, exercise_type, diet_quality,
                   somatic_sensations, physical_performance, supplements
            FROM health_metrics
            WHERE day IS NOT NULL AND day >= %s AND day < %s
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["health"].append({
                "day": r["day"],
                "sleep_quality": r["sleep_quality"],
                "exercise_type": r["exercise_type"],
                "diet_quality": r["diet_quality"],
                "somatic_sensations": (r["somatic_sensations"] or []),
                "physical_performance": r["physical_performance"],
                "supplements": (r["supplements"] or []),
            })

        cursor.execute("""
            SELECT day, deep_work_hours, shallow_work_hours,
                   time_block_adherence, cognitive_load, friction_points
            FROM productivity_metrics
            WHERE day IS NOT NULL AND day >= %s AND day < %s
            ORDER BY day DESC
        """, (recent_cutoff, today_iso))
        for r in cursor.fetchall():
            recent_days["productivity"].append({
                "day": r["day"],
                "deep_work_hours": r["deep_work_hours"],
                "shallow_work_hours": r["shallow_work_hours"],
                "time_block_adherence": r["time_block_adherence"],
                "cognitive_load": r["cognitive_load"],
                "friction_points": (r["friction_points"] or []),
            })

        cursor.execute("""
            SELECT day, title, description, tags, event_type
            FROM events
            WHERE day IS NOT NULL AND day >= %s AND day < %s
            ORDER BY day DESC, id DESC
        """, (recent_cutoff, today_iso))
        recent_days["events"] = [dict(r) for r in cursor.fetchall()]

        cursor.execute("""
            SELECT AVG(valence) v, AVG(arousal) a, COUNT(*) n
            FROM emotional_analysis
            WHERE day IS NOT NULL AND day >= %s
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
            WHERE day IS NOT NULL AND day >= %s
            GROUP BY primary_quadrant
        """, (seven_back,))
        summary["quadrant_counts_7day"] = {r["q"]: r["n"] for r in cursor.fetchall() if r["q"]}

        cursor.execute("""
            SELECT event_type, COUNT(*) n
            FROM events
            WHERE day IS NOT NULL AND day >= %s
            GROUP BY event_type
        """, (seven_back,))
        summary["events_7day"] = {r["event_type"]: r["n"] for r in cursor.fetchall() if r["event_type"]}

    covered = _detect_covered_dimensions(today_transcript)
    all_dims = set(DIMENSION_KEYWORDS.keys())
    return {
        "today_transcript": today_transcript,
        "recent_days": recent_days,
        "summary_7day": summary,
        "covered_today": sorted(covered),
        "uncovered_today": sorted(all_dims - covered),
    }


def fetch_chat_history(conversation_id: int, limit: int = 30) -> List[dict]:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM messages
            WHERE conversation_id = %s
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
            "\nGRAPH_DIGEST (pre-computed from the user's history graph in response to the "
            "user's most recent message — three labeled sections: FACTS, OBSERVATIONS, "
            "SUGGESTIONS).\n\n"
            "How to use it:\n"
            "  - Use FACTS to answer the user's question under your Q&A priority. Do not "
            "dump them verbatim or quote in bullets — extract what actually helps.\n"
            "  - OBSERVATIONS are your advisor lens, not user-facing prose. Lean on them "
            "to inform your tone and angle.\n"
            "  - SUGGESTIONS are CANDIDATES. If one is genuinely relevant and grounded in "
            "the user's question, weave it naturally into your reply — but only ONE, and "
            "only when it adds value. If suggestions feel generic or off-topic, omit them. "
            "Default to omitting.\n"
            "After answering, continue with LISTENER + INTERVIEWER as normal.\n\n"
            "DIGEST:\n"
            + graph_synthesis.strip()
            + "\n"
        )

    system = ASSISTANT_SYSTEM_TMPL.format(
        today_transcript=json.dumps(ctx["today_transcript"], indent=2),
        recent_days_json=json.dumps(ctx["recent_days"], indent=2),
        summary_json=json.dumps(ctx["summary_7day"], indent=2),
        covered_today=covered_display,
        uncovered_today=uncovered_display,
        graph_facts_block=graph_facts_block,
    )

    messages = [{"role": "system", "content": system}]
    for m in fetch_chat_history(conversation_id):
        messages.append({"role": m["role"], "content": m["content"]})

    tools = _goal_tools()

    # Multi-round tool dispatch. Cap at 2 rounds so the model can't loop —
    # after that, we accept the next text response even if it tries to call
    # more tools. Typical conversation: 1 round (no tools) or 2 rounds (tool
    # call → ack reply).
    for _ in range(2):
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
        )
        msg = completion.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return (msg.content or "").strip()

        # Append the assistant's tool-call turn so the next call sees it.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            result = _dispatch_goal_tool(tc.function.name, tc.function.arguments)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    # Fallback: one more call without tools to force a text reply.
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
    )
    return (completion.choices[0].message.content or "").strip()


def _goal_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "add_goal",
                "description": "Start tracking a new long-term goal. Only call after the user confirms they want it tracked.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Short noun-phrase title for the goal, e.g. 'Marathon Training'."},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fulfill_goal",
                "description": "Mark an active goal as fulfilled / completed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact name of the active goal to mark fulfilled."},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "remove_goal",
                "description": "Stop tracking a goal entirely (soft-delete).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Exact name of the goal to remove."},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rename_goal",
                "description": "Rename a tracked goal. Cascades to graph relationships.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "old_name": {"type": "string", "description": "Current name."},
                        "new_name": {"type": "string", "description": "New name to use going forward."},
                    },
                    "required": ["old_name", "new_name"],
                },
            },
        },
    ]


def _dispatch_goal_tool(name: str, arguments_json: str) -> dict:
    """Execute a tool call from the model. Returns a JSON-serializable dict
    describing the outcome — model receives this as the tool result and
    decides how to phrase the user-facing reply."""
    from . import goals as goals_svc

    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return {"error": "invalid_arguments", "detail": arguments_json}

    try:
        if name == "add_goal":
            row = goals_svc.add_user_goal(args.get("name", ""))
            return {"ok": True, "goal": row}
        if name == "fulfill_goal":
            row = goals_svc.mark_fulfilled(args.get("name", ""))
            return {"ok": True, "goal": row}
        if name == "remove_goal":
            row = goals_svc.mark_removed(args.get("name", ""))
            return {"ok": True, "goal": row}
        if name == "rename_goal":
            row = goals_svc.rename_goal(args.get("old_name", ""), args.get("new_name", ""))
            return {"ok": True, "goal": row}
    except goals_svc.GoalCapReachedError:
        return {"error": "cap_reached", "detail": "3 active goals already; fulfill or remove one first"}
    except goals_svc.GoalExistsError as exc:
        return {"error": "already_exists", "detail": f"goal '{exc}' already exists"}
    except goals_svc.GoalNotFoundError as exc:
        return {"error": "not_found", "detail": f"no matching goal '{exc}'"}
    except ValueError as exc:
        return {"error": "bad_input", "detail": str(exc)}

    return {"error": "unknown_tool", "detail": name}


def store_assistant_message(conversation_id: int, content: str) -> int:
    created_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO messages (conversation_id, role, content, created_at)
            VALUES (%s, 'assistant', %s, %s)
            RETURNING id
        """, (conversation_id, content, created_at))
        return cursor.fetchone()["id"]


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
