"""LLM-driven structured extraction over a day's chat messages."""
from uuid import UUID

from .core import client
from .models import HealthMetrics, JournalParserResponse, ProductivityMetrics


PARSER_SYSTEM_BATCH = (
    "You are a strict structured-extraction assistant. You will be given the user's chat messages "
    "from across a full day (timestamps included; conversations separated by '---'). Produce ONE "
    "consolidated extraction describing the WHOLE day: events, emotions, health metrics, and "
    "productivity metrics. When the user gives multiple data points for the same field (e.g., "
    "reports sleep quality twice), use the value most consistent with the broader day. ONLY "
    "populate fields with content the user actually mentioned. Use null / empty list for anything "
    "not present. Do NOT invent or infer beyond what was said. Always provide "
    "valence/arousal/primary_quadrant — pick values that best summarize the day's overall affect; "
    "use 0.0 / 'Recovery & Clarity' if affect is fully absent."
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


def parse_day_content(content: str, user_id: UUID) -> JournalParserResponse:
    from .db import connect as db_connect
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT name FROM goals WHERE user_id = %s AND status = 'active' ORDER BY name",
            (str(user_id),),
        ).fetchall()
    active_goals = [r["name"] for r in rows]

    goals_addendum = ""
    if active_goals:
        goals_addendum = (
            f"\n\nCurrently active long-term goals: {', '.join(active_goals)}. "
            "When filling contributes_to_goals on each event, only use names from this active list "
            "exactly as written. Do not propose contributes_to_goals entries that aren't in this list. "
            "Goals themselves are user-managed — do not invent or propose new goal names."
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
