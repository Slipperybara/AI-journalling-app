"""User onboarding profile — the self-report captured during the onboarding
funnel and synced once after login.

This is *not* an extraction table. It exists so the empathetic bot's earliest
replies already know the user (name, current emotional state, what's weighing on
them) before any nightly-batch graph data exists — the "day-1 win". The nightly
batch never writes here; the only writer is the client sync via PUT /api/profile.
"""
import json
from typing import Optional
from uuid import UUID

from .db import connect

# Free-text self-report fields, stored verbatim from onboarding.
_SCALAR_FIELDS = ("name", "age", "gender", "occupation", "emotional", "familiarity")


def upsert_profile(user_id: UUID, fields: dict) -> dict:
    """Idempotently store the user's onboarding answers. Unknown keys are
    ignored; missing keys are left untouched on update."""
    name = (fields.get("name") or None)
    age = fields.get("age") or None
    gender = fields.get("gender") or None
    occupation = fields.get("occupation") or None
    emotional = fields.get("emotional") or None
    familiarity = fields.get("familiarity") or None
    issues = fields.get("issues")
    if not isinstance(issues, list):
        issues = []

    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_profile
                (user_id, name, age, gender, occupation, emotional, familiarity, issues, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, user_profile.name),
                age = COALESCE(EXCLUDED.age, user_profile.age),
                gender = COALESCE(EXCLUDED.gender, user_profile.gender),
                occupation = COALESCE(EXCLUDED.occupation, user_profile.occupation),
                emotional = COALESCE(EXCLUDED.emotional, user_profile.emotional),
                familiarity = COALESCE(EXCLUDED.familiarity, user_profile.familiarity),
                issues = EXCLUDED.issues,
                updated_at = NOW()
            RETURNING user_id, name, age, gender, occupation, emotional, familiarity, issues
            """,
            (
                str(user_id), name, age, gender, occupation, emotional, familiarity,
                json.dumps(issues),
            ),
        )
        return dict(cursor.fetchone())


def get_profile(user_id: UUID) -> Optional[dict]:
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT name, age, gender, occupation, emotional, familiarity, issues
            FROM user_profile WHERE user_id = %s
            """,
            (str(user_id),),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def format_about_user(profile: Optional[dict]) -> str:
    """Render the profile as a short ABOUT_USER block for the bot system prompt.
    Returns '' when there's no usable profile so the prompt stays clean."""
    if not profile:
        return ""
    name = (profile.get("name") or "").strip()
    emotional = (profile.get("emotional") or "").strip()
    occupation = (profile.get("occupation") or "").strip()
    familiarity = (profile.get("familiarity") or "").strip()
    issues = profile.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]

    lines: list[str] = []
    if name:
        lines.append(f"  - Name: {name} (greet and address them by it naturally)")
    if emotional:
        lines.append(f"  - How they said they've been feeling lately: {emotional}")
    if issues:
        lines.append(f"  - What's weighing on them: {', '.join(issues)}")
    if occupation:
        lines.append(f"  - What fills their days: {occupation}")
    if familiarity:
        lines.append(f"  - Their relationship with journaling: {familiarity}")
    if not lines:
        return ""

    return (
        "ABOUT_USER (what they told us when they joined — this is self-reported "
        "context, not something they've said to you in conversation yet. Let it "
        "quietly inform your warmth and what you ask about; weave it in gently "
        "and only when it fits. Never recite it back like a form or claim they "
        "told you in chat):\n" + "\n".join(lines) + "\n"
    )
