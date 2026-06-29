"""Empathetic 7-day dashboard summary (per user).

Refreshed by the nightly batch (and startup catch-up). Reads the last 7 day-
buckets of parsed emotional / health / productivity rows, makes ONE cheap
gpt-4o-mini call to produce 1-2 warm, encouraging sentences about the week's
trends, and upserts it into `dashboard_summary`. The dashboard endpoint serves
the stored text — no LLM call on page load.

Scores mirror the frontend dashboard exactly so the prose matches the bars:
  - emotional: ((valence + arousal) / 2 + 1) / 2 * 100   (neutral = 50)
  - physical:  mean of present {sleep, diet, exercise-intensity} sub-scores * 100
  - focus:     min(deep_work_hours / FOCUS_TARGET_HOURS, 1) * 100
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from .core import client
from .db import connect
from .time_buckets import bucket_for, current_bucket

# A focused, sustainable deep-work day. 4h maps to a full focus score of 100.
FOCUS_TARGET_HOURS = 4.0

SLEEP_MAP = {"Poor": 0.0, "Fair": 0.33, "Good": 0.67, "Excellent": 1.0}
DIET_MAP = {"Junk/Heavy": 0.0, "Carbs Centered": 0.25, "Meat and Vegetable centered": 0.6, "Clean": 1.0}
EXERCISE_MAP = {
    "None": 0.0,
    "Light Cardio": 0.5, "Light Strength": 0.5,
    "Heavy Cardio": 1.0, "Heavy Strength": 1.0,
}


def _emotional_score(valence: Optional[float], arousal: Optional[float]) -> Optional[float]:
    if valence is None and arousal is None:
        return None
    v = valence if valence is not None else 0.0
    a = arousal if arousal is not None else 0.0
    return round(((v + a) / 2 + 1) / 2 * 100, 1)


def _physical_score(sleep: Optional[str], exercise: Optional[str], diet: Optional[str]) -> Optional[float]:
    parts = []
    if sleep in SLEEP_MAP:
        parts.append(SLEEP_MAP[sleep])
    if exercise in EXERCISE_MAP:
        parts.append(EXERCISE_MAP[exercise])
    if diet in DIET_MAP:
        parts.append(DIET_MAP[diet])
    if not parts:
        return None
    return round(sum(parts) / len(parts) * 100, 1)


def _focus_score(hours: Optional[float]) -> Optional[float]:
    if hours is None:
        return None
    return round(min(hours / FOCUS_TARGET_HOURS, 1.0) * 100, 1)


def _avg(vals: list[float]) -> Optional[float]:
    nums = [v for v in vals if v is not None]
    return round(sum(nums) / len(nums), 1) if nums else None


def _gather(user_id: UUID, tz: Optional[str] = None) -> dict:
    """Read the last 7 day-buckets and reduce to compact aggregates. `tz` selects
    the user's local 7-day window so it lines up with the local extraction keys."""
    uid = str(user_id)
    now = datetime.now(timezone.utc) if tz else datetime.now()
    seven_back = (bucket_for(now, tz) - timedelta(days=7)).isoformat()

    with connect() as conn:
        emo = conn.execute(
            "SELECT day, valence, arousal, primary_quadrant FROM emotional_analysis "
            "WHERE user_id = %s AND day IS NOT NULL AND day >= %s ORDER BY day",
            (uid, seven_back),
        ).fetchall()
        health = conn.execute(
            "SELECT day, sleep_quality, exercise_type, diet_quality FROM health_metrics "
            "WHERE user_id = %s AND day IS NOT NULL AND day >= %s ORDER BY day",
            (uid, seven_back),
        ).fetchall()
        prod = conn.execute(
            "SELECT day, deep_work_hours FROM productivity_metrics "
            "WHERE user_id = %s AND day IS NOT NULL AND day >= %s ORDER BY day",
            (uid, seven_back),
        ).fetchall()

    emo_scores = [_emotional_score(r["valence"], r["arousal"]) for r in emo]
    phys_scores = [_physical_score(r["sleep_quality"], r["exercise_type"], r["diet_quality"]) for r in health]
    focus_hours = [r["deep_work_hours"] for r in prod if r["deep_work_hours"] is not None]
    focus_scores = [_focus_score(h) for h in focus_hours]
    quadrants = [r["primary_quadrant"] for r in emo if r["primary_quadrant"]]

    return {
        "days_with_emotion": len(emo),
        "days_with_health": len(health),
        "days_with_focus": len(focus_hours),
        "emotional_avg": _avg(emo_scores),
        "physical_avg": _avg(phys_scores),
        "focus_avg": _avg(focus_scores),
        "focus_avg_hours": round(sum(focus_hours) / len(focus_hours), 1) if focus_hours else None,
        "emotional_series": [s for s in emo_scores if s is not None],
        "physical_series": [s for s in phys_scores if s is not None],
        "focus_hours_series": focus_hours,
        "quadrants": quadrants,
    }


def _generate_summary(agg: dict) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You write a 1-2 sentence summary of someone's past 7 days for the "
                    "top of their wellbeing dashboard. All scores are out of 100 "
                    "(emotional 50 = neutral; physical and focus 0 = none, 100 = strong). "
                    "Be warm, empathetic, and encouraging — notice the trend honestly but "
                    "frame it kindly and motivate gently. Speak to them as 'you'. No "
                    "numbers, no lists, no markdown, no emoji. If data is sparse, "
                    "acknowledge it warmly and invite them to keep journaling. "
                    "1-2 sentences, maximum 45 words."
                ),
            },
            {"role": "user", "content": "Past 7 days:\n" + json.dumps(agg, indent=2, default=str)},
        ],
        temperature=0.6,
    )
    return response.choices[0].message.content.strip()


def _store(user_id: UUID, summary: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO dashboard_summary (user_id, summary, generated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                summary = excluded.summary,
                generated_at = excluded.generated_at
            """,
            (str(user_id), summary, datetime.now().isoformat()),
        )


def refresh_dashboard_summary(user_id: UUID, tz: Optional[str] = None) -> dict:
    """Recompute and store this user's 7-day dashboard summary. Idempotent.
    Returns {status, summary}. Used by the nightly batch / catch-up. `tz` selects
    the user's local 7-day window."""
    agg = _gather(user_id, tz)
    has_data = agg["days_with_emotion"] or agg["days_with_health"] or agg["days_with_focus"]
    if not has_data:
        summary = (
            "There's nothing to look back on this week yet — that's completely okay. "
            "Whenever you're ready, share a little about your day and we'll start noticing the patterns together."
        )
    else:
        summary = _generate_summary(agg)
    _store(user_id, summary)
    return {"status": "refreshed", "summary": summary}


def get_dashboard_summary(user_id: UUID) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT summary FROM dashboard_summary WHERE user_id = %s",
            (str(user_id),),
        ).fetchone()
    return row["summary"] if row else None
