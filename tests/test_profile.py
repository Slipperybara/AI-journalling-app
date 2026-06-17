"""Onboarding profile: round-trip, partial-update preservation, and the
ABOUT_USER block the bot system prompt reads."""
from uuid import UUID

from app.bot import assemble_bot_context
from app.profile import format_about_user, get_profile, upsert_profile

USER = UUID("00000000-0000-0000-0000-0000000000a1")


def test_upsert_then_get_round_trips():
    upsert_profile(USER, {
        "name": "Maya", "emotional": "Running low", "occupation": "Student",
        "familiarity": "I've tried, but it never stuck", "issues": ["School", "Family"],
    })
    p = get_profile(USER)
    assert p["name"] == "Maya"
    assert p["issues"] == ["School", "Family"]


def test_partial_update_preserves_scalars_replaces_issues():
    upsert_profile(USER, {"name": "Maya", "emotional": "Running low", "issues": ["School"]})
    upsert_profile(USER, {"emotional": "Pretty good", "issues": ["Work"]})
    p = get_profile(USER)
    assert p["name"] == "Maya"          # preserved via COALESCE
    assert p["emotional"] == "Pretty good"  # updated
    assert p["issues"] == ["Work"]      # replaced wholesale


def test_format_about_user_empty_when_missing():
    assert format_about_user(None) == ""
    assert format_about_user({}) == ""


def test_about_user_block_lands_in_bot_context():
    upsert_profile(USER, {"name": "Maya", "emotional": "Running low", "issues": ["School"]})
    ctx = assemble_bot_context(USER)
    assert "ABOUT_USER" in ctx["about_user"]
    assert "Maya" in ctx["about_user"]
    assert "School" in ctx["about_user"]
