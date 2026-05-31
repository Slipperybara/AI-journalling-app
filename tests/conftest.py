"""Postgres test isolation.

One pool for the whole session (so we don't blow up Postgres' connection cap),
TRUNCATE every table before each test. Equivalent isolation to per-test
schemas without the socket churn. This also fixes the 4 pre-existing
goals-lifecycle test failures from Phase 0 — those broke because tests ran
against the live DB with name-prefix cleanup; now every test starts empty.

Neo4j is still shared across the test session and cleaned up by the per-file
fixtures that touch it (e.g. `tests/test_goals_lifecycle.py::_cleanup`).
Phase 2 introduces per-user_id isolation in Neo4j too.
"""
import pytest

from app import db as db_module


_TABLES_TO_RESET = (
    "messages",
    "conversations",
    "parse_log",
    "morning_brief_log",
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "event_topics",
    "event_goal_contributions",
    "goals",
)


@pytest.fixture(scope="session", autouse=True)
def _initialize_db():
    db_module.init_db()
    yield
    db_module.close_pool()


@pytest.fixture(autouse=True)
def _reset_db_state():
    with db_module.connect() as conn:
        for t in _TABLES_TO_RESET:
            conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")
    yield
