"""Postgres schema bootstrap + connection pooling.

All DB access uses psycopg v3 via a process-wide ConnectionPool. Connections
yield dict-like rows so callers can use `row["column"]` access. JSON-shaped
columns are `JSONB` and return native Python dicts/lists (NULL columns return
Python None; callers normalize with `r["col"] or []`).

Multi-tenant scoping (Phase 2): every domain table carries `user_id UUID
NOT NULL`. No FK on local dev — in production, Supabase's `auth.users(id)`
will be referenced via a migration applied separately so the schema stays
portable. Every query is scoped by `user_id = %s` at the call site; row
level security can be added on top later without changing app code.
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
                user_id UUID NOT NULL,
                started_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, started_at DESC)")
        # Additive migrations: optional user-set title, and a soft-delete flag.
        # "Deleting" a chat only hides it from the sidebar — its messages stay
        # so the nightly parser + knowledge graph keep referencing them.
        cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title TEXT")
        cursor.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                conversation_id BIGINT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_created ON messages(user_id, created_at)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parse_log (
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                status TEXT NOT NULL,
                parsed_at TEXT,
                error TEXT,
                PRIMARY KEY (user_id, day)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS morning_brief_log (
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                posted_at TEXT NOT NULL,
                conversation_id BIGINT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                PRIMARY KEY (user_id, day)
            )
        """)
        # Additive: the brief text is persisted so the live bot can reuse each
        # day's recap as a per-day "summary" in its conversational memory.
        cursor.execute(
            "ALTER TABLE morning_brief_log ADD COLUMN IF NOT EXISTS brief_text TEXT"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emotional_analysis (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                valence REAL,
                arousal REAL,
                primary_quadrant TEXT,
                cognitive_labels JSONB,
                cognitive_triggers JSONB,
                social_interactions JSONB
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emotional_user_day ON emotional_analysis(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_metrics (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                sleep_quality TEXT,
                exercise_type TEXT,
                diet_quality TEXT,
                somatic_sensations JSONB,
                physical_performance TEXT,
                supplements JSONB
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_health_user_day ON health_metrics(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productivity_metrics (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                deep_work_hours REAL,
                shallow_work_hours REAL,
                time_block_adherence TEXT,
                cognitive_load TEXT,
                friction_points JSONB
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_productivity_user_day ON productivity_metrics(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                title TEXT,
                description TEXT,
                tags JSONB,
                event_type TEXT
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_user_day ON events(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_topics (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                topic TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_topics_user_day ON event_topics(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_goal_contributions (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID NOT NULL,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                goal_name TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_goal_user_day ON event_goal_contributions(user_id, day)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                user_id UUID NOT NULL,
                name TEXT NOT NULL,
                discovered_on TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                status TEXT NOT NULL DEFAULT 'active',
                fulfilled_at TEXT,
                removed_at TEXT,
                source TEXT NOT NULL DEFAULT 'agent',
                PRIMARY KEY (user_id, name)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_goals_user_status ON goals(user_id, status)")
