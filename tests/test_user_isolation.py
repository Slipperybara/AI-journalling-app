"""Cross-user isolation tests (Phase 2).

Verifies that data written under TEST_USER_ID is invisible to TEST_USER_ID_B
in both Postgres and Neo4j, and that name collisions across users don't
collapse into shared rows / nodes.
"""
import pytest

from app import goals as goals_svc
from app.db import init_db
from app.graph_db import (
    graph_connect, init_graph, seed_reference_nodes_for_user
)
from tests.conftest import TEST_USER_ID, TEST_USER_ID_B


UID_A = str(TEST_USER_ID)
UID_B = str(TEST_USER_ID_B)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    init_graph()
    seed_reference_nodes_for_user(TEST_USER_ID)
    seed_reference_nodes_for_user(TEST_USER_ID_B)
    yield
    # Clean up Neo4j Day/Event/Goal nodes that this test file may have
    # created for either user — TRUNCATE handles Postgres via conftest.
    with graph_connect() as s:
        s.run("MATCH (n) WHERE n.user_id IN [$a, $b] DETACH DELETE n", a=UID_A, b=UID_B)


def test_goals_with_same_name_are_separate_per_user():
    goals_svc.add_user_goal("Marathon Training", TEST_USER_ID)
    goals_svc.add_user_goal("Marathon Training", TEST_USER_ID_B)

    a_goals = goals_svc.list_goals(TEST_USER_ID)
    b_goals = goals_svc.list_goals(TEST_USER_ID_B)

    assert len(a_goals) == 1
    assert len(b_goals) == 1
    assert a_goals[0]["name"] == "Marathon Training"
    assert b_goals[0]["name"] == "Marathon Training"


def test_list_goals_does_not_leak_across_users():
    goals_svc.add_user_goal("A-only goal", TEST_USER_ID)
    goals_svc.add_user_goal("B-only goal", TEST_USER_ID_B)

    a_names = {g["name"] for g in goals_svc.list_goals(TEST_USER_ID)}
    b_names = {g["name"] for g in goals_svc.list_goals(TEST_USER_ID_B)}

    assert a_names == {"A-only goal"}
    assert b_names == {"B-only goal"}


def test_neo4j_goal_nodes_keyed_by_user_id():
    """Adding a goal with the same name under two users produces two distinct
    Neo4j Goal nodes, one per user."""
    goals_svc.add_user_goal("Shared Name", TEST_USER_ID)
    goals_svc.add_user_goal("Shared Name", TEST_USER_ID_B)

    with graph_connect() as s:
        rows = s.run(
            "MATCH (g:Goal {name: 'Shared Name'}) RETURN g.user_id AS uid"
        ).data()
    uids = {r["uid"] for r in rows}
    assert uids == {UID_A, UID_B}


def test_mark_removed_one_user_does_not_affect_other():
    """User A marking a goal removed leaves User B's same-named goal active."""
    goals_svc.add_user_goal("To remove for A", TEST_USER_ID)
    goals_svc.add_user_goal("To remove for A", TEST_USER_ID_B)

    goals_svc.mark_removed("To remove for A", TEST_USER_ID)

    a_active = {g["name"] for g in goals_svc.list_goals(TEST_USER_ID, status="active")}
    b_active = {g["name"] for g in goals_svc.list_goals(TEST_USER_ID_B, status="active")}

    assert "To remove for A" not in a_active
    assert "To remove for A" in b_active


def test_neo4j_day_nodes_isolated_per_user():
    """Two users with a Day on the same date produce two distinct Day nodes."""
    from app.graph_batch import _write_day_node

    with graph_connect() as session:
        _write_day_node(session, "2030-01-01", None, TEST_USER_ID)
        _write_day_node(session, "2030-01-01", None, TEST_USER_ID_B)

        rows = session.run(
            "MATCH (d:Day {date: '2030-01-01'}) RETURN d.user_id AS uid"
        ).data()
    uids = {r["uid"] for r in rows}
    assert uids == {UID_A, UID_B}


def test_validator_rejects_unscoped_query():
    """The LangGraph guardrail rejects a generated Cypher that omits
    user_id on any label pattern."""
    from app.graph_schema import validate_user_id_scoping

    # Missing user_id on :Day — should be rejected.
    bad = "MATCH (d:Day) RETURN d.date AS date"
    err = validate_user_id_scoping(bad)
    assert err is not None and "user_id" in err

    # Proper scoping via property body — should pass.
    good_inline = "MATCH (d:Day {user_id: $user_id}) RETURN d.date AS date"
    assert validate_user_id_scoping(good_inline) is None

    # Proper scoping via WHERE clause — should pass.
    good_where = "MATCH (d:Day) WHERE d.user_id = $user_id RETURN d.date AS date"
    assert validate_user_id_scoping(good_where) is None

    # Multi-label with one missing — should be rejected.
    partial = (
        "MATCH (d:Day {user_id: $user_id})-[:HAD_EVENT]->(e:Event) "
        "RETURN e.title"
    )
    assert validate_user_id_scoping(partial) is not None
