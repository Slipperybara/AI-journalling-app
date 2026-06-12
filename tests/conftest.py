"""Postgres test isolation (Phase 2: per-user scoping).

One pool for the whole session, TRUNCATE every table before each test so
the per-test state is identical to a fresh-install DB. A constant
`TEST_USER_ID` is exposed for tests to scope their fixture data to a
single user; `TEST_USER_ID_B` is provided for cross-user isolation tests.

Neo4j is still shared across the test session; tests that touch the graph
should clean up their nodes themselves OR rely on the schema-isolation
property (every node carries user_id, and TEST_USER_ID_B never reads
TEST_USER_ID's data).
"""
from uuid import UUID

import pytest

from app import db as db_module
from app.core import settings


TEST_USER_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_ID_B = UUID("00000000-0000-0000-0000-000000000002")


_TABLES_TO_RESET = (
    "messages",
    "conversations",
    "parse_log",
    "morning_brief_log",
    "dashboard_summary",
    "device_tokens",
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
    # Pin the dev_user_id to TEST_USER_ID so any code path that resolves
    # the current user via the dev shim sees the test user.
    settings.dev_user_id = str(TEST_USER_ID)
    db_module.init_db()
    yield
    db_module.close_pool()


@pytest.fixture(autouse=True)
def _reset_db_state():
    with db_module.connect() as conn:
        for t in _TABLES_TO_RESET:
            conn.execute(f"TRUNCATE TABLE {t} RESTART IDENTITY CASCADE")
    yield
