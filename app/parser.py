"""LLM-driven structured extraction over a day's chat messages."""
from uuid import UUID

from . import tracking as tracking_svc, tracking_catalog
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

    tracked_addendum = _tracked_fields_addendum(user_id)

    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PARSER_SYSTEM_BATCH + goals_addendum + tracked_addendum},
            {"role": "user", "content": content},
        ],
        response_format=JournalParserResponse,
    )
    return completion.choices[0].message.parsed


def _tracked_fields_addendum(user_id: UUID) -> str:
    """Per-user extraction contract for the fields the user chose to track.

    Preset fields → structured `tracked_fields` readings (one per mentioned
    field, keyed by catalog key). Custom fields → captured as Events tagged with
    the field name. Mirrors how `goals_addendum` steers extraction per user.
    """
    active = tracking_svc.list_tracked_fields(user_id, status="active")
    presets = [f for f in active if f["kind"] == "preset" and tracking_catalog.is_preset_key(f["field_key"])]
    customs = [f for f in active if f["kind"] == "custom"]

    parts: list[str] = []
    if presets:
        lines = "\n".join(
            f"  - {f['field_key']}: {tracking_catalog.BY_KEY[f['field_key']]['value_hint']}"
            for f in presets
        )
        parts.append(
            "\n\nThe user tracks these custom daily fields. For EACH one the user actually "
            "mentioned today, add one entry to `tracked_fields` with `field_key` set to the "
            "exact key below and `value` following its format. Do NOT emit entries for fields "
            "not mentioned, and never use a key outside this list:\n" + lines
        )
    if customs:
        names = ", ".join(f["name"] for f in customs)
        parts.append(
            f"\n\nThe user also wants to keep track of: {names}. When any of these comes up, "
            "capture it as an event (title/description) and include its name in that event's "
            "`tags` and `topics` so it stays retrievable. Do NOT add these to `tracked_fields`."
        )
    return "".join(parts)
