"""Tests for the redesigned 7-day dashboard.

Covers the scoring helpers, the batch-generated empathetic summary
(`dashboard_summary`), and the dashboard endpoint's new `summary` +
`journaling_week` fields. OpenAI is mocked; Postgres is real (conftest
TRUNCATEs between tests, scoped to TEST_USER_ID).
"""
from unittest.mock import MagicMock, patch

from app import dashboard_summary as ds
from app.db import connect
from app.time_buckets import current_bucket
from tests.conftest import TEST_USER_ID

UID = str(TEST_USER_ID)


def _make_openai_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _client():
    from fastapi.testclient import TestClient
    from app.auth import get_current_user_id
    import main
    main.app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    return TestClient(main.app)


def _clear_override():
    from app.auth import get_current_user_id
    import main
    main.app.dependency_overrides.pop(get_current_user_id, None)


# ── Scoring helpers ──────────────────────────────────────────────────────────

def test_emotional_score_maps_to_0_100():
    assert ds._emotional_score(0.0, 0.0) == 50.0       # neutral
    assert ds._emotional_score(1.0, 1.0) == 100.0      # peak pleasant + energetic
    assert ds._emotional_score(-1.0, -1.0) == 0.0      # rock bottom
    assert ds._emotional_score(None, None) is None     # no data


def test_physical_score_averages_present_subscores():
    # Good sleep .67, Heavy Cardio 1, Clean diet 1 → mean .89 → 89
    assert ds._physical_score("Good", "Heavy Cardio", "Clean") == 89.0
    # Only sleep present → just that sub-score
    assert ds._physical_score("Good", None, None) == 67.0
    assert ds._physical_score(None, None, None) is None


def test_focus_score_caps_at_target():
    assert ds._focus_score(4.0) == 100.0   # hits the 4h/day target
    assert ds._focus_score(2.0) == 50.0
    assert ds._focus_score(8.0) == 100.0   # capped
    assert ds._focus_score(None) is None


# ── refresh_dashboard_summary ────────────────────────────────────────────────

def test_refresh_with_no_data_uses_default_and_skips_llm():
    with patch.object(ds, "client") as mock_client:
        result = ds.refresh_dashboard_summary(TEST_USER_ID)
        assert mock_client.chat.completions.create.call_count == 0

    assert result["status"] == "refreshed"
    assert "nothing to look back" in result["summary"].lower()
    assert ds.get_dashboard_summary(TEST_USER_ID) == result["summary"]


def test_refresh_with_data_generates_and_stores_summary():
    today = current_bucket().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO emotional_analysis (user_id, day, valence, arousal, primary_quadrant, "
            "cognitive_labels, cognitive_triggers, social_interactions) "
            "VALUES (%s, %s, 0.4, 0.2, 'Peak Performance', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb)",
            (UID, today),
        )
        conn.execute(
            "INSERT INTO productivity_metrics (user_id, day, deep_work_hours) VALUES (%s, %s, 3.0)",
            (UID, today),
        )

    with patch.object(ds, "client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response(
            "You showed up steadily this week — keep leaning into that momentum."
        )
        result = ds.refresh_dashboard_summary(TEST_USER_ID)
        assert mock_client.chat.completions.create.call_count == 1

    assert result["summary"].startswith("You showed up steadily")
    assert ds.get_dashboard_summary(TEST_USER_ID) == result["summary"]


def test_get_summary_none_when_absent():
    assert ds.get_dashboard_summary(TEST_USER_ID) is None


# ── Dashboard endpoint ───────────────────────────────────────────────────────

def test_dashboard_returns_summary_and_journaling_week():
    today = current_bucket()
    today_iso = today.isoformat()

    # A journaled day (today) and the bucket 3 days back; the rest empty.
    three_back = None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (UID, f"{today_iso}T12:00:00"),
        )
        cid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO messages (user_id, conversation_id, role, content, created_at) "
            "VALUES (%s, %s, 'user', 'today entry', %s)",
            (UID, cid, f"{today_iso}T12:00:00"),
        )
        from datetime import timedelta
        three_back = (today - timedelta(days=3)).isoformat()
        cur.execute(
            "INSERT INTO messages (user_id, conversation_id, role, content, created_at) "
            "VALUES (%s, %s, 'user', 'older entry', %s)",
            (UID, cid, f"{three_back}T12:00:00"),
        )
        conn.execute(
            "INSERT INTO dashboard_summary (user_id, summary, generated_at) VALUES (%s, %s, %s)",
            (UID, "A warm weekly recap.", f"{today_iso}T06:00:00"),
        )

    client = _client()
    try:
        r = client.get("/api/dashboard")
    finally:
        _clear_override()

    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "A warm weekly recap."

    week = body["journaling_week"]
    assert len(week) == 7
    assert week[-1]["day"] == today_iso and week[-1]["journaled"] is True
    by_day = {w["day"]: w["journaled"] for w in week}
    assert by_day[three_back] is True
    assert sum(1 for w in week if w["journaled"]) == 2
