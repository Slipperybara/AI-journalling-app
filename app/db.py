"""SQLite schema bootstrap + small DB-shaped helpers.

All DB access in this app uses raw `sqlite3` with `conn.row_factory = sqlite3.Row`.
No ORM. `init_db()` is idempotent and self-migrates via `ALTER TABLE … ADD COLUMN`.
"""
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .core import DB_NAME


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a sqlite3 connection with Row factory enabled. Commits on exit."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def loads(s: Any) -> list:
    """JSON-decode a stored list field, tolerating None / bad data."""
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []


def _column_exists(cursor: sqlite3.Cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cursor.fetchall())


def _add_day_column(cursor: sqlite3.Cursor, table: str) -> None:
    if not _column_exists(cursor, table, "day"):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN day TEXT")


def init_db() -> None:
    with connect() as conn:
        cursor = conn.cursor()

        # One-shot migration from the original brain-dump schema. If the legacy
        # `journal_entries` table exists but the conversation-based schema does
        # not, drop the old set so we start clean.
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='journal_entries'")
        has_old = cursor.fetchone() is not None
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='conversations'")
        has_new = cursor.fetchone() is not None
        if has_old and not has_new:
            for t in ("journal_entries", "daily_habits", "todos", "ideas"):
                cursor.execute(f"DROP TABLE IF EXISTS {t}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emotional_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                valence REAL,
                arousal REAL,
                primary_quadrant TEXT,
                cognitive_labels TEXT,
                cognitive_triggers TEXT,
                social_interactions TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                sleep_quality TEXT,
                exercise_type TEXT,
                diet_quality TEXT,
                somatic_sensations TEXT,
                physical_performance TEXT,
                supplements TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productivity_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                deep_work_hours REAL,
                shallow_work_hours REAL,
                time_block_adherence TEXT,
                cognitive_load TEXT,
                friction_points TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                title TEXT,
                description TEXT,
                tags TEXT,
                event_type TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                task_description TEXT NOT NULL,
                is_completed INTEGER DEFAULT 0,
                due_date TEXT,
                FOREIGN KEY(message_id) REFERENCES messages(id)
            )
        """)

        # Day-keyed migration. Existing rows leave `day` NULL (they were per-
        # message under the legacy inline-parse flow). New rows from the
        # nightly batch set `day` to the bucket they represent.
        for t in ("emotional_analysis", "health_metrics", "productivity_metrics", "events", "todos"):
            _add_day_column(cursor, t)

        # Tracks which day-buckets the nightly batch has processed.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS parse_log (
                day TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                parsed_at TEXT,
                error TEXT
            )
        """)
