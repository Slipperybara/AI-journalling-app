"""The live chatbot.

The bot is the only LLM call per user message — there is no inline structured
extraction. Its purpose is to be an empathetic conversation partner with memory
of recent days. Context:
  - today's raw transcript (all today-bucket conversations, chronological);
  - the previous 2 days of full conversation (fresh memory);
  - the 5 days before that as warm morning-brief recaps (longer memory);
  - soft coverage awareness of the six tracked dimensions (NOT a checklist).

System-prompt priorities (in order when they conflict):
  1. Empathetic listener — reflect and validate feelings; make the user feel
     heard. This is the primary job.
  2. Open-up guide — at most one gentle, open-ended question that invites the
     user to share more of themselves; often skipped entirely.
  3. Informed advisor / Q&A — answer direct questions; when a GRAPH_DIGEST is
     present, give accurate, actionable advice grounded in the user's history.
  4. Organic capture — sleep/diet/etc. are tracked quietly; never interrogate.
     Structured extraction still happens only in the nightly batch.
"""
import json
import re
from datetime import datetime, timedelta
from typing import List, Optional
from uuid import UUID

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


ASSISTANT_SYSTEM_TMPL = """You are MindForge — a warm, empathetic companion. Your PRIMARY purpose is to make the user feel genuinely heard and a little better for having talked with you. You are a caring conversation partner, not an interviewer and not a form.

PRIORITIES (in order when they conflict):
  1. EMPATHETIC LISTENER (primary). Read what the user is really saying and how they feel underneath it. Reflect it back, name it gently, and stay with the emotion before anything else. Don't rush to fix, advise, or redirect. Let your replies breathe — go longer when the moment is heavy or they're opening up; stay brief when they're light.
  2. OPEN-UP GUIDE. When it would help them go deeper, ask AT MOST ONE gentle, open-ended question that invites them to share more of themselves — their feelings, what's behind something, what mattered to them. Never a checklist question. Often the most caring move is to ask nothing and simply be present — skip the question whenever one would intrude on the moment.
  3. INFORMED ADVISOR / Q&A (secondary). If the user asks you something directly, answer it well, as a knowledgeable friend. When a GRAPH_DIGEST appears below, use it to give accurate, consolidated information and concrete, actionable advice grounded in the user's own history. Advice is welcome when it's wanted — but it never replaces listening.
  4. ORGANIC CAPTURE (lowest). MindForge quietly keeps track of six things for the user's own long-term reflection: sleep, exercise, diet, deep-work, emotional state, and the day's events. NEVER interrogate for these. Only when the user has paused, or a dimension comes up naturally, may you RARELY ask one light question about it — and only if it doesn't cut against the emotional moment. When in doubt, don't.

COVERAGE_TODAY (which of the six the user has already touched today — soft awareness ONLY, not a checklist to complete):
  Covered: {covered_today}
  Not yet mentioned: {uncovered_today}

GOAL TOOLS (when to call them):
  - `add_goal(name)`: ONLY after the user explicitly confirms they want a goal tracked. When the user mentions a long-term aim ("I want to train for a marathon", "I'm going to focus on Jane Street prep"), FIRST ask in conversation whether to track it — do NOT call add_goal yet. If on a later turn they confirm ("yes please", "yeah track it"), THEN call add_goal. The 3-active cap is enforced server-side; if the call returns an error about the cap, tell the user they need to fulfill or remove a goal first.
  - `fulfill_goal(name)`: when the user clearly says they've completed or are done with a tracked goal ("I finished Jane Street prep", "I hit my marathon"). Call immediately with the matching active goal name; no confirmation needed.
  - `remove_goal(name)`: when the user says they're dropping or want to remove a tracked goal ("drop Jane Street prep", "remove that"). Call immediately with the matching name.
  - `rename_goal(old_name, new_name)`: when the user explicitly asks to rename a goal ("rename Jane Street to JS Prep"). Call immediately.
  After a tool call, generate ONE user-facing reply acknowledging what changed in plain prose. Do not list the tool result verbatim.

REPLY STYLE:
  - Variable length — longer when they're opening up or hurting, shorter when they're light. No headings or emoji.
  - You may use light Markdown for clarity: **bold** for a key word, *italics* for gentle emphasis, and bullet points ("- ") when laying out concrete steps or options (most natural when sharing information or suggestions). Default to warm, flowing prose — never over-format an empathetic reply; a few sentences that feel human beat a tidy list.
  - ==highlight==: wrap the ONE word or short phrase that most captures the heart of your reply — the feeling, the insight, or the thing worth holding onto — in ==double equals==. Use it at most once, occasionally twice in a longer reply, and only when it genuinely lands. Skip it entirely in short replies. It should feel like a gentle underline on the thing that matters, never decoration.
  - Plain, warm prose. Sound like a person who cares, not a coach or a form.
  - You remember recent days (RECENT_TRANSCRIPTS, DAILY_SUMMARIES). Reference them naturally when it shows you've been listening ("you mentioned yesterday that…") — but only when it deepens the connection, never to show off recall.

CONTEXT YOU HAVE:

TODAY_TRANSCRIPT (all of today's messages so far, chronological):
{today_transcript}

RECENT_TRANSCRIPTS (full conversations from the previous 2 days — your fresh memory, oldest first):
{recent_transcripts}

DAILY_SUMMARIES (warm recaps of the days before that — your longer memory, newest first):
{daily_summaries}

{graph_facts_block}"""


def assemble_bot_context(user_id: UUID, now: Optional[datetime] = None) -> dict:
    """Assemble the empathetic bot's conversational memory:
      - today's full transcript;
      - the previous 2 days of full conversation (fresh memory, oldest first);
      - the 5 days before that as morning-brief recaps (longer memory);
      - soft coverage awareness of the six tracked dimensions.
    """
    # Lazy import: morning_brief imports store_assistant_message from this
    # module, so a top-level import here would be circular.
    from .morning_brief import get_daily_summaries

    now = now or datetime.now()
    today = bucket_for(now)
    today_iso = today.isoformat()

    today_transcript = [
        {"at": r["created_at"], "role": r["role"], "content": r["content"]}
        for r in get_messages_for_day(today_iso, user_id)
    ]

    # Previous 2 days of full conversation, ordered oldest → newest so the
    # model reads them as a timeline leading up to today.
    recent_transcripts: list[dict] = []
    for back in (2, 1):
        day_iso = (today - timedelta(days=back)).isoformat()
        msgs = [
            {"role": r["role"], "content": r["content"]}
            for r in get_messages_for_day(day_iso, user_id)
        ]
        if msgs:
            recent_transcripts.append({"day": day_iso, "messages": msgs})

    daily_summaries = get_daily_summaries(today_iso, user_id, num_days=5)

    covered = _detect_covered_dimensions(today_transcript)
    all_dims = set(DIMENSION_KEYWORDS.keys())
    return {
        "today_transcript": today_transcript,
        "recent_transcripts": recent_transcripts,
        "daily_summaries": daily_summaries,
        "covered_today": sorted(covered),
        "uncovered_today": sorted(all_dims - covered),
    }


def fetch_chat_history(conversation_id: int, user_id: UUID, limit: int = 30) -> List[dict]:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT role, content FROM messages
            WHERE conversation_id = %s AND user_id = %s
            ORDER BY id ASC
        """, (conversation_id, str(user_id)))
        rows = [dict(r) for r in cursor.fetchall()]
    return rows[-limit:]


def _build_reply_messages(
    conversation_id: int, user_id: UUID, graph_synthesis: Optional[str] = None
) -> tuple[list, list]:
    """Assemble the (messages, tools) for a bot reply. Shared by the
    non-streaming and streaming reply generators so they can't drift."""
    ctx = assemble_bot_context(user_id)
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

    recent_transcripts_str = (
        json.dumps(ctx["recent_transcripts"], indent=2, default=str)
        if ctx["recent_transcripts"] else "(no conversation in the previous 2 days)"
    )
    daily_summaries_str = (
        json.dumps(ctx["daily_summaries"], indent=2, default=str)
        if ctx["daily_summaries"] else "(no earlier recaps yet)"
    )

    system = ASSISTANT_SYSTEM_TMPL.format(
        today_transcript=json.dumps(ctx["today_transcript"], indent=2, default=str),
        recent_transcripts=recent_transcripts_str,
        daily_summaries=daily_summaries_str,
        covered_today=covered_display,
        uncovered_today=uncovered_display,
        graph_facts_block=graph_facts_block,
    )

    messages = [{"role": "system", "content": system}]
    for m in fetch_chat_history(conversation_id, user_id):
        messages.append({"role": m["role"], "content": m["content"]})

    return messages, _goal_tools()


def generate_bot_reply(
    conversation_id: int, user_id: UUID, graph_synthesis: Optional[str] = None
) -> str:
    """Generate the bot's reply.

    For analytical messages, `graph_synthesis` is the digest produced by
    `app.agents.synthesizer.synthesize_response` — pre-summarized facts pulled
    from the user's Neo4j history graph. It's injected as a GRAPH_FACTS block
    so the bot can weave the relevant pieces in under its Q&A priority while
    keeping its normal voice (LISTENER + INTERVIEWER nudge intact). For pure
    journaling messages, pass None.
    """
    messages, tools = _build_reply_messages(conversation_id, user_id, graph_synthesis)

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
            result = _dispatch_goal_tool(tc.function.name, tc.function.arguments, user_id)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    # Fallback: one more call without tools to force a text reply.
    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
    )
    return (completion.choices[0].message.content or "").strip()


def generate_bot_reply_stream(
    conversation_id: int, user_id: UUID, graph_synthesis: Optional[str] = None
):
    """Streaming twin of generate_bot_reply. Yields text deltas.

    Goal-tool rounds carry no user-facing text, so they run the same as the
    non-streaming path (accumulate tool-call args, dispatch, loop). Only the
    text-producing round is streamed token-by-token. The common journaling
    case (no tools) streams from the first chunk.
    """
    messages, tools = _build_reply_messages(conversation_id, user_id, graph_synthesis)

    for _ in range(2):
        stream = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
            stream=True,
        )
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            for tc in (getattr(delta, "tool_calls", None) or []):
                acc = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    acc["arguments"] += tc.function.arguments
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                yield delta.content

        if not tool_acc:
            return  # text round complete — all deltas already yielded

        # Tool round: replay the assistant tool-call turn, dispatch, loop.
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": [
                {
                    "id": a["id"],
                    "type": "function",
                    "function": {"name": a["name"], "arguments": a["arguments"]},
                }
                for a in tool_acc.values()
            ],
        })
        for a in tool_acc.values():
            result = _dispatch_goal_tool(a["name"], a["arguments"], user_id)
            messages.append({
                "role": "tool",
                "tool_call_id": a["id"],
                "content": json.dumps(result, default=str),
            })

    # Fallback: force a final text reply (no tools), still streamed.
    stream = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
            yield chunk.choices[0].delta.content


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


def _dispatch_goal_tool(name: str, arguments_json: str, user_id: UUID) -> dict:
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
            row = goals_svc.add_user_goal(args.get("name", ""), user_id)
            return {"ok": True, "goal": row}
        if name == "fulfill_goal":
            row = goals_svc.mark_fulfilled(args.get("name", ""), user_id)
            return {"ok": True, "goal": row}
        if name == "remove_goal":
            row = goals_svc.mark_removed(args.get("name", ""), user_id)
            return {"ok": True, "goal": row}
        if name == "rename_goal":
            row = goals_svc.rename_goal(args.get("old_name", ""), args.get("new_name", ""), user_id)
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


def store_assistant_message(conversation_id: int, content: str, user_id: UUID) -> int:
    created_at = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO messages (user_id, conversation_id, role, content, created_at)
            VALUES (%s, %s, 'assistant', %s, %s)
            RETURNING id
        """, (str(user_id), conversation_id, content, created_at))
        return cursor.fetchone()["id"]


def process_message_background(conversation_id: int, message_content: str, user_id: UUID) -> None:
    """Background-task entrypoint after a user message. Routes through LangGraph:
    journaling → existing bot; analytical → Neo4j Cypher pipeline."""
    try:
        from .langgraph_flow import process_message
        process_message(conversation_id, message_content, user_id)
    except Exception as e:
        print(f"[BG] Reply error for conv {conversation_id}: {e}")
        store_assistant_message(
            conversation_id,
            "Hmm, I had trouble responding just now. Want to try saying that again?",
            user_id,
        )
