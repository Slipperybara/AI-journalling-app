"""Tests for the morning brief job (multi-tenant).

OpenAI calls are mocked. Postgres is real but conftest TRUNCATEs between
tests, so every test starts from a clean slate scoped to TEST_USER_ID.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.db import connect, init_db
from tests.conftest import TEST_USER_ID


TEST_DAY = "2099-01-01"
TEST_YESTERDAY = "2098-12-31"
TEST_SEED_DAYS = ["2098-12-29", "2098-12-30", "2098-12-31"]
UID = str(TEST_USER_ID)


def _make_openai_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.fixture(autouse=True)
def setup_db_only():
    init_db()
    yield


def _seed_yesterday_data():
    """Seed parse_log + emotional_analysis + health_metrics for the 3-day
    window so the brief sees rich data and _has_pattern_data() returns True."""
    with connect() as conn:
        for d in TEST_SEED_DAYS:
            conn.execute(
                "INSERT INTO parse_log (user_id, day, status, parsed_at) "
                "VALUES (%s, %s, 'succeeded', %s)",
                (UID, d, f"{d}T23:59:00"),
            )
            conn.execute(
                "INSERT INTO emotional_analysis (user_id, day, valence, arousal, primary_quadrant, "
                "cognitive_labels, cognitive_triggers, social_interactions) "
                "VALUES (%s, %s, 0.3, 0.5, 'Peak Performance', '[\"motivated\"]'::jsonb, '[]'::jsonb, '[]'::jsonb)",
                (UID, d),
            )
            conn.execute(
                "INSERT INTO health_metrics (user_id, day, sleep_quality, exercise_type, diet_quality, "
                "somatic_sensations, physical_performance, supplements) "
                "VALUES (%s, %s, 'Good', 'Light Cardio', 'Clean', '[]'::jsonb, null, '[]'::jsonb)",
                (UID, d),
            )


def test_post_brief_creates_conversation_and_message():
    from app import morning_brief
    _seed_yesterday_data()

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        mock_client.chat.completions.create.side_effect = [
            _make_openai_response("NONE"),  # _detect_pattern
            _make_openai_response("Good morning. Yesterday felt steady. How are you doing today?"),
        ]
        result = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)

    assert result["status"] == "posted"
    assert isinstance(result["conversation_id"], int) and result["conversation_id"] > 0

    with connect() as conn:
        log = conn.execute(
            "SELECT status, conversation_id, brief_text FROM morning_brief_log WHERE user_id = %s AND day = %s",
            (UID, TEST_DAY),
        ).fetchone()
        assert log["status"] == "posted"
        # The brief text is persisted so the live bot can reuse it as a recap.
        assert log["brief_text"] == "Good morning. Yesterday felt steady. How are you doing today?"
        msgs = conn.execute(
            "SELECT role, content FROM messages WHERE user_id = %s AND conversation_id = %s",
            (UID, log["conversation_id"]),
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
        first = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)
        second = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)

    assert first["status"] == "posted"
    assert second["status"] == "already_done"
    assert second["conversation_id"] == first["conversation_id"]

    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM morning_brief_log WHERE user_id = %s AND day = %s",
            (UID, TEST_DAY),
        ).fetchone()["n"]
    assert n == 1


def test_sparse_day_still_posts():
    """Yesterday has parse_log but no extraction data → brief still posts with
    the sparse path. Status stays 'posted', not 'skipped_empty'."""
    from app import morning_brief
    with connect() as conn:
        conn.execute(
            "INSERT INTO parse_log (user_id, day, status, parsed_at) VALUES (%s, %s, 'empty', %s)",
            (UID, TEST_YESTERDAY, "2098-12-31T23:59:00"),
        )
        conn.execute(
            "INSERT INTO goals (user_id, name, discovered_on, status, source) "
            "VALUES (%s, 'test-goal-keep-context', %s, 'active', 'user') "
            "ON CONFLICT (user_id, name) DO NOTHING",
            (UID, TEST_YESTERDAY),
        )

    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={"test-goal-keep-context": 0}):
        mock_client.chat.completions.create.side_effect = [
            _make_openai_response("Good morning. Yesterday was quiet. How are you doing today?"),
        ]
        result = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)
    assert result["status"] == "posted"
    # Only the brief LLM call; _detect_pattern was skipped because there's <3 days of data.
    assert mock_client.chat.completions.create.call_count == 1


def test_brand_new_user_posts_welcome():
    from app import morning_brief

    # conftest TRUNCATEs before every test, so the goals table is empty here.
    with patch.object(morning_brief, "client") as mock_client, \
         patch.object(morning_brief, "_fetch_goal_momentum", return_value={}):
        result = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)

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
        result = morning_brief.post_morning_brief(TEST_DAY, TEST_USER_ID)

    assert result["status"] == "failed"
    assert "LLM exploded" in result["error"]

    with connect() as conn:
        log = conn.execute(
            "SELECT status, error, conversation_id FROM morning_brief_log "
            "WHERE user_id = %s AND day = %s",
            (UID, TEST_DAY),
        ).fetchone()
    assert log["status"] == "failed"
    assert "LLM exploded" in log["error"]
    # No conversation tied to the failed brief.
    assert log["conversation_id"] == 0
