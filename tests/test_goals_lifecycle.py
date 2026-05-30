"""Lifecycle tests for the goals module.

These run against the live `journal.db` and Neo4j (Docker), but each test
isolates itself by using a unique TEST_PREFIX for goal names and cleaning
up SQLite rows + Neo4j nodes in teardown.
"""
import pytest
from unittest.mock import patch

from app import goals as goals_svc
from app.db import connect, init_db
from app.graph_db import graph_connect, init_graph


TEST_PREFIX = "__lifecycle_test__"


def _cleanup():
    with connect() as conn:
        conn.execute(
            "DELETE FROM goals WHERE name LIKE ?", (TEST_PREFIX + "%",)
        )
    with graph_connect() as s:
        s.run(
            "MATCH (g:Goal) WHERE g.name STARTS WITH $p DETACH DELETE g",
            p=TEST_PREFIX,
        )


@pytest.fixture(autouse=True)
def setup_and_teardown():
    init_db()
    init_graph()
    _cleanup()
    yield
    _cleanup()


def _graph_status(name):
    with graph_connect() as s:
        row = s.run(
            "MATCH (g:Goal {name: $name}) RETURN g.status AS s", name=name
        ).single()
    return row["s"] if row else None


def test_add_user_goal_active_under_cap():
    result = goals_svc.add_user_goal(f"{TEST_PREFIX}solo")
    assert result["status"] == "active"
    assert result["source"] == "user"
    assert _graph_status(f"{TEST_PREFIX}solo") == "active"


def test_add_user_goal_candidate_at_cap():
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}fill{i}")
    overflow = goals_svc.add_user_goal(f"{TEST_PREFIX}overflow")
    assert overflow["status"] == "candidate"
    assert _graph_status(f"{TEST_PREFIX}overflow") is None


def test_add_user_goal_409_on_duplicate_active_name():
    goals_svc.add_user_goal(f"{TEST_PREFIX}dupe")
    with pytest.raises(goals_svc.GoalExistsError):
        goals_svc.add_user_goal(f"{TEST_PREFIX}dupe")


def test_add_user_goal_resurrects_removed_at_cap_as_candidate():
    name = f"{TEST_PREFIX}revived"
    goals_svc.add_user_goal(name)
    goals_svc.mark_removed(name)
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}block{i}")
    revived = goals_svc.add_user_goal(name)
    assert revived["status"] == "candidate"
    assert revived["removed_at"] is None
    assert _graph_status(name) is None


def test_agent_goal_semantic_dedup_returns_existing():
    canonical = f"{TEST_PREFIX}canonical"
    goals_svc.add_user_goal(canonical)
    with patch(
        "app.goals._semantic_dedup_against_existing", return_value=canonical
    ):
        result = goals_svc.add_agent_goal(
            f"{TEST_PREFIX}variant_phrase", day="2026-01-01"
        )
    assert result["name"] == canonical
    with connect() as conn:
        all_test_rows = conn.execute(
            "SELECT name FROM goals WHERE name LIKE ?", (TEST_PREFIX + "%",)
        ).fetchall()
    assert {r["name"] for r in all_test_rows} == {canonical}


def test_agent_goal_inserts_when_no_match():
    with patch("app.goals._semantic_dedup_against_existing", return_value=None):
        result = goals_svc.add_agent_goal(
            f"{TEST_PREFIX}novel", day="2026-01-01"
        )
    assert result["name"] == f"{TEST_PREFIX}novel"
    assert result["status"] == "active"
    assert result["source"] == "agent"


def test_mark_fulfilled_does_not_auto_promote():
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}active{i}")
    candidate_name = f"{TEST_PREFIX}queued"
    goals_svc.add_user_goal(candidate_name)
    goals_svc.mark_fulfilled(f"{TEST_PREFIX}active0")
    with connect() as conn:
        row = conn.execute(
            "SELECT status FROM goals WHERE name = ?", (candidate_name,)
        ).fetchone()
        active_count = goals_svc._count_active(conn.cursor())
    assert row["status"] == "candidate"
    assert active_count == 2


def test_mark_removed_hard_deletes_from_graph():
    name = f"{TEST_PREFIX}toremove"
    goals_svc.add_user_goal(name)
    assert _graph_status(name) == "active"
    goals_svc.mark_removed(name)
    assert _graph_status(name) is None
    with connect() as conn:
        row = conn.execute(
            "SELECT status, removed_at FROM goals WHERE name = ?", (name,)
        ).fetchone()
    assert row["status"] == "removed"
    assert row["removed_at"] is not None


def test_promote_candidate_409_at_cap():
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}cap{i}")
    candidate_name = f"{TEST_PREFIX}wantactive"
    goals_svc.add_user_goal(candidate_name)
    with pytest.raises(goals_svc.GoalCapReachedError):
        goals_svc.promote_candidate(candidate_name)


def test_sync_goal_to_graph_idempotent_status_change():
    name = f"{TEST_PREFIX}fliptest"
    goals_svc.add_user_goal(name)
    assert _graph_status(name) == "active"
    with connect() as conn:
        conn.execute(
            "UPDATE goals SET status = 'fulfilled', fulfilled_at = '2026-01-01T00:00:00' WHERE name = ?",
            (name,),
        )
    goals_svc.sync_goal_to_graph(name)
    assert _graph_status(name) == "fulfilled"


def test_reconcile_deletes_orphan_graph_goal():
    from app.graph_maintenance import reconcile_goals
    orphan = f"{TEST_PREFIX}orphan"
    with graph_connect() as s:
        s.run(
            "MERGE (g:Goal {name: $name}) SET g.status = 'active'", name=orphan
        )
    assert _graph_status(orphan) == "active"
    result = reconcile_goals()
    assert _graph_status(orphan) is None
    assert result["goals_orphaned_deleted"] >= 1


def test_parser_addendum_excludes_fulfilled():
    active_name = f"{TEST_PREFIX}active_goal"
    fulfilled_name = f"{TEST_PREFIX}fulfilled_goal"
    goals_svc.add_user_goal(active_name)
    goals_svc.add_user_goal(fulfilled_name)
    goals_svc.mark_fulfilled(fulfilled_name)

    from app import parser
    captured = {}

    class _Result:
        def __init__(self):
            self.choices = [type("c", (), {"message": type("m", (), {"parsed": None})()})()]

    def _fake_parse(model, messages, response_format):
        captured["system"] = messages[0]["content"]
        return _Result()

    with patch.object(parser.client.beta.chat.completions, "parse", side_effect=_fake_parse):
        parser.parse_day_content("anything")

    assert active_name in captured["system"]
    assert fulfilled_name not in captured["system"]
