"""Tests for the morning brief job.

OpenAI calls are mocked. SQLite is real (shared journal.db). Tests use a
unique fake day in the far future and clean up everything they touch.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.db import connect, init_db


TEST_DAY = "2099-01-01"
TEST_YESTERDAY = "2098-12-31"
TEST_SEED_DAYS = ["2098-12-29", "2098-12-30", "2098-12-31"]


def _make_openai_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _cleanup():
    with connect() as conn:
        conv_rows = conn.execute(
            "SELECT conversation_id FROM morning_brief_log WHERE day = ?",
            (TEST_DAY,),
        ).fetchall()
        for r in conv_rows:
            cid = r["conversation_id"]
            if cid:
                conn.execute("DELETE FROM messages WHERE conversation_id = ?", (cid,))
                conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
        conn.execute("DELETE FROM morning_brief_log WHERE day = ?", (TEST_DAY,))
        for tbl in (
            "emotional_analysis",
            "health_metrics",
            "productivity_metrics",
            "events",
            "event_topics",
            "event_goal_contributions",
        ):
            for d in [TEST_YESTERDAY, *TEST_SEED_DAYS]:
                conn.execute(f"DELETE FROM {tbl} WHERE day = ?", (d,))
        for d in [TEST_YESTERDAY, *TEST_SEED_DAYS]:
            conn.execute("DELETE FROM parse_log WHERE day = ?", (d,))


@pytest.fixture(autouse=True)
def setup_and_teardown():
    init_db()
    _cleanup()
    yield
    _cleanup()


def _seed_yesterday_data():
    """Seed parse_log + emotional_analysis + health_metrics for the 3-day
    window so the brief sees rich data and _has_pattern_data() returns True."""
    with connect() as conn:
        for d in TEST_SEED_DAYS:
            conn.execute(
                "INSERT INTO parse_log (day, status, parsed_at) VALUES (?, 'succeeded', ?)",
                (d, f"{d}T23:59:00"),
            )
            conn.execute(
                "INSERT INTO emotional_analysis (day, valence, arousal, primary_quadrant, "
                "cognitive_labels, cognitive_triggers, social_interactions) "
                "VALUES (?, 0.3, 0.5, 'Peak Performance', '[\"motivated\"]', '[]', '[]')",
                (d,),
            )
            conn.execute(
                "INSERT INTO health_metrics (day, sleep_quality, exercise_type, diet_quality, "
                "somatic_sensations, physical_performance, supplements) "
                "VALUES (?, 'Good', 'Light Cardio', 'Clean', '[]', null, '[]')",
                (d,),
            )


def test_post_brief_creates_conversation_and_message():
    from app import morning_brief
    _seed_yesterday_data()

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        mock_client.chat.completions.create.side_effect = [
            _make_openai_response("NONE"),  # _detect_pattern
            _make_openai_response("Good morning. Yesterday felt steady. How are you doing today?"),  # _generate_brief
        ]
        result = morning_brief.post_morning_brief(TEST_DAY)

    assert result["status"] == "posted"
    assert isinstance(result["conversation_id"], int) and result["conversation_id"] > 0

    with connect() as conn:
        log = conn.execute(
            "SELECT status, conversation_id FROM morning_brief_log WHERE day = ?",
            (TEST_DAY,),
        ).fetchone()
        assert log["status"] == "posted"
        msgs = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ?",
            (log["conversation_id"],),
        ).fetchall()
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "Good morning" in msgs[0]["content"]


def test_idempotent_on_repeat_call():
    from app import morning_brief
    _seed_yesterday_data()

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        mock_client.chat.completions.create.side_effect = [
            _make_openai_response("NONE"),
            _make_openai_response("Good morning. How are you doing today?"),
        ]
        first = morning_brief.post_morning_brief(TEST_DAY)
        # Second call: no LLM calls should fire — idempotency short-circuits early.
        second = morning_brief.post_morning_brief(TEST_DAY)

    assert first["status"] == "posted"
    assert second["status"] == "already_done"
    assert second["conversation_id"] == first["conversation_id"]

    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM morning_brief_log WHERE day = ?", (TEST_DAY,)
        ).fetchone()["n"]
    assert n == 1


def test_sparse_day_still_posts():
    """Yesterday has parse_log but no extraction data → brief still posts with
    the sparse path. Status stays 'posted', not 'skipped_empty'."""
    from app import morning_brief
    with connect() as conn:
        conn.execute(
            "INSERT INTO parse_log (day, status, parsed_at) VALUES (?, 'empty', ?)",
            (TEST_YESTERDAY, "2098-12-31T23:59:00"),
        )
        # Seed at least one active goal so brand-new-user path doesn't trigger.
        conn.execute(
            "INSERT OR IGNORE INTO goals (name, discovered_on, status, source) "
            "VALUES ('test-goal-keep-context', ?, 'active', 'user')",
            (TEST_YESTERDAY,),
        )

    try:
        with patch.object(morning_brief, "client") as mock_client, \
             patch.object(morning_brief, "_fetch_goal_momentum", return_value={"test-goal-keep-context": 0}):
            mock_client.chat.completions.create.side_effect = [
                _make_openai_response("Good morning. Yesterday was quiet. How are you doing today?"),
            ]
            result = morning_brief.post_morning_brief(TEST_DAY)
        assert result["status"] == "posted"
        # Only the brief LLM call; _detect_pattern was skipped because there's <3 days of data.
        assert mock_client.chat.completions.create.call_count == 1
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM goals WHERE name = 'test-goal-keep-context'")


def test_brand_new_user_posts_welcome():
    from app import morning_brief

    with connect() as conn:
        existing_goal_count = conn.execute(
            "SELECT COUNT(*) AS n FROM goals WHERE status='active'"
        ).fetchone()["n"]
    if existing_goal_count > 0:
        pytest.skip(
            "Cannot run brand-new-user test against a populated journal.db"
        )

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        result = morning_brief.post_morning_brief(TEST_DAY)

    assert result["status"] == "skipped_empty"
    assert isinstance(result["conversation_id"], int)
    # No LLM call: brand-new path uses a static welcome.
    assert mock_client.chat.completions.create.call_count == 0


def test_failure_logged_not_raised():
    from app import morning_brief
    _seed_yesterday_data()

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        mock_client.chat.completions.create.side_effect = Exception("LLM exploded")
        result = morning_brief.post_morning_brief(TEST_DAY)

    assert result["status"] == "failed"
    assert "LLM exploded" in result["error"]

    with connect() as conn:
        log = conn.execute(
            "SELECT status, error, conversation_id FROM morning_brief_log WHERE day = ?",
            (TEST_DAY,),
        ).fetchone()
    assert log["status"] == "failed"
    assert "LLM exploded" in log["error"]
    # No conversation tied to the failed brief.
    assert log["conversation_id"] == 0
