"""LLM-driven structured extraction over a day's chat messages."""
from .core import client
from .models import HealthMetrics, JournalParserResponse, ProductivityMetrics


PARSER_SYSTEM_BATCH = (
    "You are a strict structured-extraction assistant. You will be given the user's chat messages "
    "from across a full day (timestamps included; conversations separated by '---'). Produce ONE "
    "consolidated extraction describing the WHOLE day: a single set of todos, events, emotions, "
    "health metrics, and productivity metrics. When the user gives multiple data points for the "
    "same field (e.g., reports sleep quality twice), use the value most consistent with the broader "
    "day. ONLY populate fields with content the user actually mentioned. Use null / empty list for "
    "anything not present. Do NOT invent or infer beyond what was said. Always provide "
    "valence/arousal/primary_quadrant — pick values that best summarize the day's overall affect; "
    "use 0.0 / 'Recovery & Clarity' if affect is fully absent. "
    "For todos specifically: emit ONLY daily executables each taking under 3 hours. "
    "If the user mentions a project or goal that would exceed 3 hours (e.g. 'build X', "
    "'finish Y project', 'prepare for Z'), decompose it into concrete sub-tasks each under "
    "3 hours and emit each as a separate TodoItem. If you cannot infer sub-tasks, emit a "
    "single first-next-action todo (e.g. 'Research options for X — 1h'). "
    "Never emit a multi-day project as a single todo."
)


def is_health_meaningful(h: HealthMetrics) -> bool:
    return any([
        h.sleep_quality, h.exercise_type, h.diet_quality,
        h.somatic_sensations, h.physical_performance, h.supplements,
    ])


def is_productivity_meaningful(p: ProductivityMetrics) -> bool:
    return any([
        p.deep_work_hours is not None,
        p.shallow_work_hours is not None,
        p.time_block_adherence, p.cognitive_load, p.friction_points,
    ])


def parse_day_content(content: str) -> JournalParserResponse:
    from .db import connect as db_connect
    with db_connect() as conn:
        rows = conn.execute("SELECT name FROM goals ORDER BY name").fetchall()
    existing_goals = [r["name"] for r in rows]

    goals_addendum = ""
    if existing_goals:
        goals_addendum = (
            f"\n\nCurrently tracked goals: {', '.join(existing_goals)}. "
            "When filling contributes_to_goals on each event, only use names from this list exactly as written. "
            "Add new goal names to discovered_goals only if the user explicitly states a new objective today."
        )

    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PARSER_SYSTEM_BATCH + goals_addendum},
            {"role": "user", "content": content},
        ],
        response_format=JournalParserResponse,
    )
    return completion.choices[0].message.parsed
