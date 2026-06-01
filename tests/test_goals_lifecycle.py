"""Lifecycle tests for the goals module (multi-tenant)."""
import pytest
from unittest.mock import patch

from app import goals as goals_svc
from app.db import connect, init_db
from app.graph_db import graph_connect, init_graph
from tests.conftest import TEST_USER_ID


TEST_PREFIX = "__lifecycle_test__"
UID = str(TEST_USER_ID)


def _cleanup():
    with graph_connect() as s:
        s.run(
            "MATCH (g:Goal {user_id: $user_id}) WHERE g.name STARTS WITH $p DETACH DELETE g",
            user_id=UID, p=TEST_PREFIX,
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
            "MATCH (g:Goal {user_id: $user_id, name: $name}) RETURN g.status AS s",
            user_id=UID, name=name,
        ).single()
    return row["s"] if row else None


def test_add_user_goal_active_under_cap():
    result = goals_svc.add_user_goal(f"{TEST_PREFIX}solo", TEST_USER_ID)
    assert result["status"] == "active"
    assert result["source"] == "user"
    assert _graph_status(f"{TEST_PREFIX}solo") == "active"


def test_add_user_goal_409_at_cap():
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}fill{i}", TEST_USER_ID)
    with pytest.raises(goals_svc.GoalCapReachedError):
        goals_svc.add_user_goal(f"{TEST_PREFIX}overflow", TEST_USER_ID)


def test_add_user_goal_409_on_duplicate_active_name():
    goals_svc.add_user_goal(f"{TEST_PREFIX}dupe", TEST_USER_ID)
    with pytest.raises(goals_svc.GoalExistsError):
        goals_svc.add_user_goal(f"{TEST_PREFIX}dupe", TEST_USER_ID)


def test_resurrects_removed_under_cap():
    name = f"{TEST_PREFIX}revived"
    goals_svc.add_user_goal(name, TEST_USER_ID)
    goals_svc.mark_removed(name, TEST_USER_ID)
    revived = goals_svc.add_user_goal(name, TEST_USER_ID)
    assert revived["status"] == "active"
    assert revived["removed_at"] is None


def test_resurrect_at_cap_raises():
    name = f"{TEST_PREFIX}revived"
    goals_svc.add_user_goal(name, TEST_USER_ID)
    goals_svc.mark_removed(name, TEST_USER_ID)
    for i in range(3):
        goals_svc.add_user_goal(f"{TEST_PREFIX}block{i}", TEST_USER_ID)
    with pytest.raises(goals_svc.GoalCapReachedError):
        goals_svc.add_user_goal(name, TEST_USER_ID)


def test_mark_removed_hard_deletes_from_graph():
    name = f"{TEST_PREFIX}toremove"
    goals_svc.add_user_goal(name, TEST_USER_ID)
    assert _graph_status(name) == "active"
    goals_svc.mark_removed(name, TEST_USER_ID)
    assert _graph_status(name) is None
    with connect() as conn:
        row = conn.execute(
            "SELECT status, removed_at FROM goals WHERE user_id = %s AND name = %s",
            (UID, name),
        ).fetchone()
    assert row["status"] == "removed"
    assert row["removed_at"] is not None


def test_sync_goal_to_graph_idempotent_status_change():
    name = f"{TEST_PREFIX}fliptest"
    goals_svc.add_user_goal(name, TEST_USER_ID)
    assert _graph_status(name) == "active"
    with connect() as conn:
        conn.execute(
            "UPDATE goals SET status = 'fulfilled', fulfilled_at = '2026-01-01T00:00:00' "
            "WHERE user_id = %s AND name = %s",
            (UID, name),
        )
    goals_svc.sync_goal_to_graph(name, TEST_USER_ID)
    assert _graph_status(name) == "fulfilled"


def test_reconcile_deletes_orphan_graph_goal():
    from app.graph_maintenance import reconcile_goals
    orphan = f"{TEST_PREFIX}orphan"
    with graph_connect() as s:
        s.run(
            "MERGE (g:Goal {user_id: $user_id, name: $name}) SET g.status = 'active'",
            user_id=UID, name=orphan,
        )
    assert _graph_status(orphan) == "active"
    result = reconcile_goals(TEST_USER_ID)
    assert _graph_status(orphan) is None
    assert result["goals_orphaned_deleted"] >= 1


def test_parser_addendum_excludes_fulfilled():
    active_name = f"{TEST_PREFIX}active_goal"
    fulfilled_name = f"{TEST_PREFIX}fulfilled_goal"
    goals_svc.add_user_goal(active_name, TEST_USER_ID)
    goals_svc.add_user_goal(fulfilled_name, TEST_USER_ID)
    goals_svc.mark_fulfilled(fulfilled_name, TEST_USER_ID)

    from app import parser
    captured = {}

    class _Result:
        def __init__(self):
            self.choices = [type("c", (), {"message": type("m", (), {"parsed": None})()})()]

    def _fake_parse(model, messages, response_format):
        captured["system"] = messages[0]["content"]
        return _Result()

    with patch.object(parser.client.beta.chat.completions, "parse", side_effect=_fake_parse):
        parser.parse_day_content("anything", TEST_USER_ID)

    assert active_name in captured["system"]
    assert fulfilled_name not in captured["system"]


def test_rename_goal_updates_sqlite_and_graph():
    old = f"{TEST_PREFIX}old_name"
    new = f"{TEST_PREFIX}new_name"
    goals_svc.add_user_goal(old, TEST_USER_ID)
    # Seed an event_goal_contributions row referencing the old name to
    # confirm the cascade.
    with connect() as conn:
        conn.execute(
            "INSERT INTO event_goal_contributions (user_id, day, event_title, goal_name) "
            "VALUES (%s, '2026-01-01', 'Some Event', %s)",
            (UID, old),
        )

    result = goals_svc.rename_goal(old, new, TEST_USER_ID)
    assert result["name"] == new
    assert _graph_status(new) == "active"
    assert _graph_status(old) is None

    with connect() as conn:
        old_row = conn.execute(
            "SELECT name FROM goals WHERE user_id = %s AND name = %s", (UID, old)
        ).fetchone()
        cascade = conn.execute(
            "SELECT goal_name FROM event_goal_contributions "
            "WHERE user_id = %s AND event_title = 'Some Event'",
            (UID,),
        ).fetchone()
    assert old_row is None
    assert cascade["goal_name"] == new


def test_rename_goal_to_existing_name_raises():
    a = f"{TEST_PREFIX}a"
    b = f"{TEST_PREFIX}b"
    goals_svc.add_user_goal(a, TEST_USER_ID)
    goals_svc.add_user_goal(b, TEST_USER_ID)
    with pytest.raises(goals_svc.GoalExistsError):
        goals_svc.rename_goal(a, b, TEST_USER_ID)


def test_rename_unknown_goal_raises():
    with pytest.raises(goals_svc.GoalNotFoundError):
        goals_svc.rename_goal(f"{TEST_PREFIX}does_not_exist", f"{TEST_PREFIX}new", TEST_USER_ID)
