"""Postgres schema bootstrap + connection pooling.

All DB access uses psycopg v3 via a process-wide ConnectionPool. Connections
yield dict-like rows so callers can use `row["column"]` access. JSON-shaped
columns are `JSONB` and return native Python dicts/lists (NULL columns return
Python None — callers normalize with `r["col"] or []`).
"""
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .core import settings


_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
        )
    return _pool


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a pooled psycopg connection. Commits on context exit, rolls back on exception."""
    with _get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """Drain and close the process-wide pool. Call from FastAPI's shutdown hook."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


EXTRACTION_TABLES = (
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "event_topics",
    "event_goal_contributions",
)
# `goals` is intentionally excluded — it's user-managed via chat/slash
# commands and must survive day re-parses.


def init_db() -> None:
    with connect() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id BIGSERIAL PRIMARY KEY,
                started_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                conversation_id BIGINT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parse_log (
                day TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                parsed_at TEXT,
                error TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS morning_brief_log (
                day TEXT PRIMARY KEY,
                posted_at TEXT NOT NULL,
                conversation_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emotional_analysis (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                valence REAL,
                arousal REAL,
                primary_quadrant TEXT,
                cognitive_labels JSONB,
                cognitive_triggers JSONB,
                social_interactions JSONB
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_metrics (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                sleep_quality TEXT,
                exercise_type TEXT,
                diet_quality TEXT,
                somatic_sensations JSONB,
                physical_performance TEXT,
                supplements JSONB
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productivity_metrics (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                deep_work_hours REAL,
                shallow_work_hours REAL,
                time_block_adherence TEXT,
                cognitive_load TEXT,
                friction_points JSONB
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                title TEXT,
                description TEXT,
                tags JSONB,
                event_type TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_topics (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                topic TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_goal_contributions (
                id BIGSERIAL PRIMARY KEY,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                goal_name TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                name TEXT PRIMARY KEY,
                discovered_on TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                status TEXT NOT NULL DEFAULT 'active',
                fulfilled_at TEXT,
                removed_at TEXT,
                source TEXT NOT NULL DEFAULT 'agent'
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)")
