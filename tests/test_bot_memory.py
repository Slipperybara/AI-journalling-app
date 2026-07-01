"""Tests for the empathetic bot's conversational memory.

Covers `assemble_bot_context` (today full + 2 days full transcript + 5 days of
recaps) and `morning_brief.get_daily_summaries` (window, labeling, and the
brief_text → conversation-message fallback). Postgres is real; conftest
TRUNCATEs between tests, scoped to TEST_USER_ID.
"""
from datetime import datetime

from app.bot import assemble_bot_context
from app.db import connect
from app.morning_brief import get_daily_summaries
from tests.conftest import TEST_USER_ID

UID = str(TEST_USER_ID)
NOW = datetime(2099, 1, 20, 12, 0, 0)  # today bucket = 2099-01-20


def _insert_conversation() -> int:
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (UID, "2099-01-01T00:00:00"),
        ).fetchone()
    return row["id"]


def _insert_message(conv_id: int, role: str, content: str, created_at: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO messages (user_id, conversation_id, role, content, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (UID, conv_id, role, content, created_at),
        )


def _insert_brief(day: str, text, status: str = "posted", conversation_id: int = 0) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO morning_brief_log (user_id, day, posted_at, conversation_id, status, brief_text) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (UID, day, f"{day}T06:00:00", conversation_id, status, text),
        )


# ── get_daily_summaries ──────────────────────────────────────────────────────

def test_daily_summaries_window_labeling_and_order():
    """5 posted briefs map to the 5 days just before the 2 full-transcript days,
    each labeled with the day it recaps, newest first."""
    # Posting days 14..18 recap days 13..17. (today=20 → full days 18,19.)
    for d in range(14, 19):
        _insert_brief(f"2099-01-{d:02d}", f"recap posted on day {d}")
    # Out of window: posting day 19 recaps 18 (a full-transcript day) → excluded.
    _insert_brief("2099-01-19", "too recent")
    # Out of window: posting day 13 recaps 12 → too old, excluded.
    _insert_brief("2099-01-13", "too old")

    summaries = get_daily_summaries("2099-01-20", TEST_USER_ID, num_days=5)

    assert [s["recaps"] for s in summaries] == [
        "2099-01-17", "2099-01-16", "2099-01-15", "2099-01-14", "2099-01-13",
    ]
    assert summaries[0]["summary"] == "recap posted on day 18"
    assert all("too recent" != s["summary"] and "too old" != s["summary"] for s in summaries)


def test_daily_summaries_excludes_non_posted_status():
    _insert_brief("2099-01-17", "good recap", status="posted")
    _insert_brief("2099-01-16", "failed recap", status="failed")

    summaries = get_daily_summaries("2099-01-20", TEST_USER_ID, num_days=5)

    assert [s["summary"] for s in summaries] == ["good recap"]


def test_daily_summaries_falls_back_to_conversation_message():
    """Rows written before brief_text existed (NULL) fall back to the logged
    conversation's first assistant message."""
    conv_id = _insert_conversation()
    _insert_message(conv_id, "assistant", "Good morning, fallback recap.", "2099-01-17T06:00:00")
    _insert_message(conv_id, "user", "thanks", "2099-01-17T08:00:00")
    _insert_brief("2099-01-17", None, status="posted", conversation_id=conv_id)

    summaries = get_daily_summaries("2099-01-20", TEST_USER_ID, num_days=5)

    assert len(summaries) == 1
    assert summaries[0]["summary"] == "Good morning, fallback recap."
    assert summaries[0]["recaps"] == "2099-01-16"


def test_daily_summaries_skips_day_with_no_text_and_no_conversation():
    _insert_brief("2099-01-17", None, status="posted", conversation_id=0)
    assert get_daily_summaries("2099-01-20", TEST_USER_ID, num_days=5) == []


# ── assemble_bot_context ─────────────────────────────────────────────────────

def test_context_includes_today_and_previous_two_full_days():
    conv_id = _insert_conversation()
    _insert_message(conv_id, "user", "today message", "2099-01-20T12:00:00")
    _insert_message(conv_id, "user", "yesterday message", "2099-01-19T12:00:00")
    _insert_message(conv_id, "user", "two days ago message", "2099-01-18T12:00:00")
    # Three days back must NOT appear in full transcripts.
    _insert_message(conv_id, "user", "three days ago message", "2099-01-17T12:00:00")

    ctx = assemble_bot_context(TEST_USER_ID, now=NOW)

    assert [m["content"] for m in ctx["today_transcript"]] == ["today message"]
    # Oldest → newest: day-2 then day-1.
    assert [rt["day"] for rt in ctx["recent_transcripts"]] == ["2099-01-18", "2099-01-19"]
    flat = [m["content"] for rt in ctx["recent_transcripts"] for m in rt["messages"]]
    assert flat == ["two days ago message", "yesterday message"]
    assert "three days ago message" not in flat


def test_context_drops_legacy_analytics_keys():
    ctx = assemble_bot_context(TEST_USER_ID, now=NOW)
    assert "recent_days" not in ctx
    assert "summary_7day" not in ctx
    assert set(ctx) == {
        "today_transcript", "recent_transcripts", "daily_summaries",
        "covered_today", "uncovered_today", "about_user", "tracked_fields",
    }


def test_context_pulls_daily_summaries():
    _insert_brief("2099-01-17", "a warm recap")  # recaps 2099-01-16
    ctx = assemble_bot_context(TEST_USER_ID, now=NOW)
    assert ctx["daily_summaries"] == [{"recaps": "2099-01-16", "summary": "a warm recap"}]
