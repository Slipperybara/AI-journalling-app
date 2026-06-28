"""Personalized first greeting for a brand-new user.

When a user finishes onboarding and opens the app for the first time, we want the
very first assistant message to already feel personal — greeting them by name and
gently reflecting back what they told us during onboarding (their current mood and
what's weighing on them), then inviting them to open up. This is the "day-1 win":
before any nightly-batch graph data exists, the bot still feels like it knows them.

The greeting is generated from the onboarding `user_profile` (see app/profile.py),
reusing `format_about_user` for the grounding block. A deterministic template is
used as a fallback so a brand-new user is never left staring at an empty chat.
"""
import traceback
from typing import Optional
from uuid import UUID

from .core import client
from .profile import format_about_user, get_profile

_GREETING_SYSTEM = (
    "You are JAI, a warm, empathetic journaling companion. Write the VERY FIRST "
    "message a new user sees, right after they finished onboarding. Goals:\n"
    "  1. Greet them warmly by their first name.\n"
    "  2. Briefly introduce yourself as JAI in one short clause.\n"
    "  3. Gently reflect back ONE thing they told us during onboarding — how "
    "they've been feeling and/or what's weighing on them — without reciting it "
    "like a form.\n"
    "  4. Invite them to tell you more, ending with one open, caring question.\n\n"
    "Style: 2-4 sentences, plain warm prose. No markdown, no bullet points, no "
    "emoji. Sound like a person who cares, not a coach. Do NOT claim they've said "
    "anything to you in chat yet — this is the first message. Address them as "
    "\"you\"."
)

_FALLBACK = (
    "Hi — I'm JAI, your space to think out loud and feel a little lighter. "
    "There's no pressure and no blank page to fill. How are you feeling today?"
)


def _fallback_for(profile: Optional[dict]) -> str:
    """Deterministic greeting used when the LLM call fails or there's no profile."""
    name = (profile or {}).get("name") if profile else None
    name = (name or "").strip()
    emotional = ((profile or {}).get("emotional") or "").strip() if profile else ""
    if name and emotional:
        return (
            f"Hi {name} — I'm JAI, your space to think out loud and feel a little "
            f"lighter. You mentioned you've been feeling {emotional.lower()} lately. "
            "Want to tell me more about what's going on?"
        )
    if name:
        return (
            f"Hi {name} — I'm JAI, your space to think out loud and feel a little "
            "lighter. How are you feeling today?"
        )
    return _FALLBACK


def generate_intro_greeting(user_id: UUID) -> str:
    """Return a personalized first-message greeting for the user. Never raises —
    falls back to a warm template on any error so the flow can't break."""
    profile = get_profile(user_id)
    about = format_about_user(profile)
    if not about:
        # No usable onboarding data — a clean generic welcome is the best we can do.
        return _fallback_for(profile)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _GREETING_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Here is what they told us during onboarding. Write their "
                        "first greeting now.\n\n" + about
                    ),
                },
            ],
            temperature=0.7,
        )
        text = (response.choices[0].message.content or "").strip()
        return text or _fallback_for(profile)
    except Exception:
        traceback.print_exc()
        return _fallback_for(profile)
