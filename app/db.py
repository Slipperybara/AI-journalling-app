"""SQLite schema bootstrap + small DB-shaped helpers.

All DB access in this app uses raw `sqlite3` with `conn.row_factory = sqlite3.Row`.
No ORM. `init_db()` is idempotent. If it detects drift from a previous schema
(vestigial `message_id` column on extraction tables), it drops and recreates
the extraction tables and clears `parse_log` so the batch can backfill.
"""
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .core import DB_NAME


# Set to True by `init_db()` when the one-shot extraction-table cleanup runs,
# so the scheduler can trigger a backfill across every day with messages.
migration_ran = False


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


EXTRACTION_TABLES = (
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "todos",
)


def _has_legacy_extraction_schema(cursor: sqlite3.Cursor) -> bool:
    """Detect the pre-day-keyed schema: extraction tables carry a vestigial
    `message_id` column from when each message was parsed inline."""
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='emotional_analysis'")
    if not cursor.fetchone():
        return False
    cursor.execute("PRAGMA table_info(emotional_analysis)")
    return any(r[1] == "message_id" for r in cursor.fetchall())


def init_db() -> None:
    global migration_ran

    with connect() as conn:
        cursor = conn.cursor()

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
            CREATE TABLE IF NOT EXISTS parse_log (
                day TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                parsed_at TEXT,
                error TEXT
            )
        """)

        if _has_legacy_extraction_schema(cursor):
            for t in EXTRACTION_TABLES:
                cursor.execute(f"DROP TABLE IF EXISTS {t}")
            cursor.execute("DELETE FROM parse_log")
            migration_ran = True
            print("[db] migrated extraction tables to day-keyed schema; backfill will run")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emotional_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                valence REAL,
                arousal REAL,
                primary_quadrant TEXT,
                cognitive_labels TEXT,
                cognitive_triggers TEXT,
                social_interactions TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS health_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                sleep_quality TEXT,
                exercise_type TEXT,
                diet_quality TEXT,
                somatic_sensations TEXT,
                physical_performance TEXT,
                supplements TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS productivity_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                deep_work_hours REAL,
                shallow_work_hours REAL,
                time_block_adherence TEXT,
                cognitive_load TEXT,
                friction_points TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                title TEXT,
                description TEXT,
                tags TEXT,
                event_type TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                task_description TEXT NOT NULL,
                is_completed INTEGER DEFAULT 0,
                due_date TEXT
            )
        """)

        # Todos v2 migration: add audit/carryover columns
        for col, definition in [
            ("created_at", "TEXT"),
            ("fulfilled_at", "TEXT"),
            ("source_day", "TEXT"),
        ]:
            cursor.execute("PRAGMA table_info(todos)")
            if not any(r[1] == col for r in cursor.fetchall()):
                cursor.execute(f"ALTER TABLE todos ADD COLUMN {col} {definition}")
