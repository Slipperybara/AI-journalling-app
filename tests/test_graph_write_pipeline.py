"""Integration tests for the Neo4j write pipeline (multi-tenant)."""
import pytest

from app.db import init_db
from app.graph_db import graph_connect, init_graph, seed_reference_nodes_for_user
from tests.conftest import TEST_USER_ID


UID = str(TEST_USER_ID)


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    init_graph()
    seed_reference_nodes_for_user(TEST_USER_ID)


def _clear_test_day(session, day: str):
    session.run(
        "MATCH (d:Day {user_id: $user_id, date: $day}) DETACH DELETE d",
        user_id=UID, day=day,
    )


TEST_DAY = "1999-01-01"


def test_write_day_creates_day_node():
    from app.graph_batch import _write_day_node
    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None, TEST_USER_ID)
        result = session.run(
            "MATCH (d:Day {user_id: $user_id, date: $day}) RETURN d.date AS date",
            user_id=UID, day=TEST_DAY,
        ).single()
    assert result["date"] == TEST_DAY


def test_ensure_day_chain_fills_gaps():
    """ensure_day_chain over a range MERGEs every Day node and creates
    NEXT_DAY edges between consecutive days for the given user."""
    from app.graph_batch import ensure_day_chain

    days = ["1999-02-01", "1999-02-02", "1999-02-03"]
    with graph_connect() as s:
        for d in days:
            s.run(
                "MATCH (d:Day {user_id: $user_id, date: $d}) DETACH DELETE d",
                user_id=UID, d=d,
            )

    result = ensure_day_chain("1999-02-01", "1999-02-03", TEST_USER_ID)
    assert result["nodes_ensured"] == 3
    assert result["edges_ensured"] == 2

    with graph_connect() as s:
        nodes = [r["date"] for r in s.run(
            "MATCH (d:Day {user_id: $user_id}) WHERE d.date IN $days "
            "RETURN d.date AS date ORDER BY d.date",
            user_id=UID, days=days,
        )]
        edges = [(r["a"], r["b"]) for r in s.run(
            "MATCH (a:Day {user_id: $user_id})-[:NEXT_DAY]->(b:Day {user_id: $user_id}) "
            "WHERE a.date IN $days RETURN a.date AS a, b.date AS b ORDER BY a.date",
            user_id=UID, days=days,
        )]
        for d in days:
            s.run(
                "MATCH (d:Day {user_id: $user_id, date: $d}) DETACH DELETE d",
                user_id=UID, d=d,
            )

    assert nodes == days
    assert edges == [("1999-02-01", "1999-02-02"), ("1999-02-02", "1999-02-03")]


def test_ensure_day_chain_idempotent():
    from app.graph_batch import ensure_day_chain

    days = ["1999-03-01", "1999-03-02"]
    with graph_connect() as s:
        for d in days:
            s.run(
                "MATCH (d:Day {user_id: $user_id, date: $d}) DETACH DELETE d",
                user_id=UID, d=d,
            )

    ensure_day_chain("1999-03-01", "1999-03-02", TEST_USER_ID)
    ensure_day_chain("1999-03-01", "1999-03-02", TEST_USER_ID)

    with graph_connect() as s:
        edge_count = s.run(
            "MATCH (a:Day {user_id: $user_id, date: '1999-03-01'})-[r:NEXT_DAY]->"
            "(b:Day {user_id: $user_id, date: '1999-03-02'}) RETURN count(r) AS n",
            user_id=UID,
        ).single()["n"]
        for d in days:
            s.run(
                "MATCH (d:Day {user_id: $user_id, date: $d}) DETACH DELETE d",
                user_id=UID, d=d,
            )

    assert edge_count == 1


def test_write_emotion_creates_in_quadrant_edge():
    from app.graph_batch import _write_day_node, _write_emotion
    from unittest.mock import MagicMock

    emotion = MagicMock()
    emotion.__getitem__ = lambda self, key: {
        "primary_quadrant": "Peak Performance",
        "valence": 0.8,
        "arousal": 0.6,
        "cognitive_labels": ["motivated"],
        "cognitive_triggers": [],
        "social_interactions": [],
    }[key]

    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None, TEST_USER_ID)
        _write_emotion(session, TEST_DAY, emotion, TEST_USER_ID)
        result = session.run("""
            MATCH (d:Day {user_id: $user_id, date: $day})
                  -[:HAD_EMOTION]->(es:EmotionState {user_id: $user_id})
                  -[:IN_QUADRANT]->(q:EmotionQuadrant {user_id: $user_id})
            RETURN q.name AS quadrant
        """, user_id=UID, day=TEST_DAY).single()

    assert result["quadrant"] == "Peak Performance"


def test_write_event_creates_involves_edge():
    from app.graph_batch import _write_day_node, _write_event
    from unittest.mock import MagicMock

    event = MagicMock()
    event.__getitem__ = lambda self, key: {
        "title": "Test Event",
        "event_type": "idea",
        "description": "a test event",
        "tags": "testing,pipeline",
    }[key]

    topics_by_event = {"Test Event": ["testing", "pipeline"]}
    goals_by_event = {}

    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None, TEST_USER_ID)
        _write_event(session, TEST_DAY, event, topics_by_event, goals_by_event, TEST_USER_ID)
        result = session.run("""
            MATCH (d:Day {user_id: $user_id, date: $day})
                  -[:HAD_EVENT]->(e:Event {user_id: $user_id})
                  -[:INVOLVES]->(t:Topic {user_id: $user_id})
            RETURN e.title AS event, collect(t.name) AS topics
        """, user_id=UID, day=TEST_DAY).single()

    assert result["event"] == "Test Event"
    assert set(result["topics"]) == {"testing", "pipeline"}


def test_maintenance_merges_duplicate_events():
    from app.graph_maintenance import _deduplicate_events

    with graph_connect() as session:
        session.run("""
            CREATE (:Event {user_id: $user_id, title: 'FooBar', canonical_id: 'test_foo', event_type: 'idea', description: '', tags: []})
            CREATE (:Event {user_id: $user_id, title: 'FooBars', canonical_id: 'test_foos', event_type: 'idea', description: '', tags: []})
        """, user_id=UID)

    _deduplicate_events(TEST_USER_ID)

    with graph_connect() as session:
        result = session.run(
            "MATCH (e:Event {user_id: $user_id}) WHERE e.title IN ['FooBar', 'FooBars'] RETURN count(e) AS n",
            user_id=UID,
        ).single()
        session.run(
            "MATCH (e:Event {user_id: $user_id}) WHERE e.title IN ['FooBar', 'FooBars'] DETACH DELETE e",
            user_id=UID,
        )

    assert result["n"] == 1
