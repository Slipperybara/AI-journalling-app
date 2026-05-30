"""Integration tests for the Neo4j write pipeline. Requires docker compose up -d."""
import pytest
from app.db import init_db
from app.graph_db import graph_connect, init_graph


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    init_graph()


def _clear_test_day(session, day: str):
    session.run("MATCH (d:Day {date: $day}) DETACH DELETE d", day=day)


TEST_DAY = "1999-01-01"


def test_write_day_creates_day_node():
    """write_day skips if parse_log has no succeeded row — but we can call helpers directly."""
    from app.graph_batch import _write_day_node
    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None)
        result = session.run("MATCH (d:Day {date: $day}) RETURN d.date AS date", day=TEST_DAY).single()
    assert result["date"] == TEST_DAY


def test_ensure_day_chain_fills_gaps():
    """ensure_day_chain over a range MERGEs every Day node and creates
    NEXT_DAY edges between consecutive days, regardless of whether any of
    them had a prior write_day call."""
    from app.graph_batch import ensure_day_chain

    days = ["1999-02-01", "1999-02-02", "1999-02-03"]
    with graph_connect() as s:
        for d in days:
            s.run("MATCH (d:Day {date: $d}) DETACH DELETE d", d=d)

    result = ensure_day_chain("1999-02-01", "1999-02-03")
    assert result["nodes_ensured"] == 3
    assert result["edges_ensured"] == 2

    with graph_connect() as s:
        nodes = [r["date"] for r in s.run(
            "MATCH (d:Day) WHERE d.date IN $days RETURN d.date AS date ORDER BY d.date",
            days=days,
        )]
        edges = [(r["a"], r["b"]) for r in s.run(
            "MATCH (a:Day)-[:NEXT_DAY]->(b:Day) WHERE a.date IN $days "
            "RETURN a.date AS a, b.date AS b ORDER BY a.date",
            days=days,
        )]
        for d in days:
            s.run("MATCH (d:Day {date: $d}) DETACH DELETE d", d=d)

    assert nodes == days
    assert edges == [("1999-02-01", "1999-02-02"), ("1999-02-02", "1999-02-03")]


def test_ensure_day_chain_idempotent():
    """Re-running ensure_day_chain on the same range produces no duplicates."""
    from app.graph_batch import ensure_day_chain

    days = ["1999-03-01", "1999-03-02"]
    with graph_connect() as s:
        for d in days:
            s.run("MATCH (d:Day {date: $d}) DETACH DELETE d", d=d)

    ensure_day_chain("1999-03-01", "1999-03-02")
    ensure_day_chain("1999-03-01", "1999-03-02")

    with graph_connect() as s:
        edge_count = s.run(
            "MATCH (a:Day {date: '1999-03-01'})-[r:NEXT_DAY]->(b:Day {date: '1999-03-02'}) "
            "RETURN count(r) AS n"
        ).single()["n"]
        for d in days:
            s.run("MATCH (d:Day {date: $d}) DETACH DELETE d", d=d)

    assert edge_count == 1


def test_write_emotion_creates_in_quadrant_edge():
    from app.graph_batch import _write_day_node, _write_emotion
    from unittest.mock import MagicMock

    emotion = MagicMock()
    emotion.__getitem__ = lambda self, key: {
        "primary_quadrant": "Peak Performance",
        "valence": 0.8,
        "arousal": 0.6,
        "cognitive_labels": '["motivated"]',
        "cognitive_triggers": "[]",
        "social_interactions": "[]",
    }[key]

    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None)
        _write_emotion(session, TEST_DAY, emotion)
        result = session.run("""
            MATCH (d:Day {date: $day})-[:HAD_EMOTION]->(es)-[:IN_QUADRANT]->(q)
            RETURN q.name AS quadrant
        """, day=TEST_DAY).single()

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
        _write_day_node(session, TEST_DAY, None)
        _write_event(session, TEST_DAY, event, topics_by_event, goals_by_event)
        result = session.run("""
            MATCH (d:Day {date: $day})-[:HAD_EVENT]->(e)-[:INVOLVES]->(t)
            RETURN e.title AS event, collect(t.name) AS topics
        """, day=TEST_DAY).single()

    assert result["event"] == "Test Event"
    assert set(result["topics"]) == {"testing", "pipeline"}


def test_maintenance_merges_duplicate_events():
    from app.graph_maintenance import _deduplicate_events

    with graph_connect() as session:
        session.run("""
            CREATE (:Event {title: 'FooBar', canonical_id: 'test_foo', event_type: 'idea', description: '', tags: []})
            CREATE (:Event {title: 'FooBars', canonical_id: 'test_foos', event_type: 'idea', description: '', tags: []})
        """)

    _deduplicate_events()

    with graph_connect() as session:
        result = session.run(
            "MATCH (e:Event) WHERE e.title IN ['FooBar', 'FooBars'] RETURN count(e) AS n"
        ).single()
        session.run("MATCH (e:Event) WHERE e.title IN ['FooBar', 'FooBars'] DETACH DELETE e")

    assert result["n"] == 1
