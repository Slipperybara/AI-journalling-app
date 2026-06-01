"""Neo4j driver lifecycle, connection context manager, and per-user reference
node seeding.

Phase 2 multi-tenant: reference nodes (EmotionQuadrant, SleepQuality,
ExerciseType, DietQuality) are per-user, not global. `init_graph()` only
creates indexes/constraints at startup; per-user reference nodes are seeded
lazily via `seed_reference_nodes_for_user(user_id)` on the first write for
that user (called from `graph_batch.write_day`).
"""
from contextlib import contextmanager
from uuid import UUID

from neo4j import GraphDatabase

from .core import settings

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


@contextmanager
def graph_connect():
    """Yield a Neo4j session. Mirrors the db.connect() pattern."""
    with _get_driver().session() as session:
        yield session


def close():
    """Call on app shutdown to release driver resources."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def init_graph() -> None:
    """Create composite indexes for fast (user_id, key) lookups.

    Per-user reference nodes are NOT seeded here — that's done lazily per
    user via `seed_reference_nodes_for_user(user_id)`. This keeps the
    startup path independent of any specific user.
    """
    with graph_connect() as session:
        index_specs = [
            ("Day", "date"),
            ("EmotionState", "valence"),
            ("EmotionQuadrant", "name"),
            ("HealthState", "physical_performance"),
            ("SleepQuality", "level"),
            ("ExerciseType", "name"),
            ("DietQuality", "type"),
            ("Event", "canonical_id"),
            ("Topic", "name"),
            ("Category", "name"),
            ("Goal", "name"),
        ]
        for label, prop in index_specs:
            idx_name = f"{label.lower()}_user_{prop}"
            session.run(
                f"CREATE INDEX {idx_name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.user_id, n.{prop})"
            )
    print("[graph_db] indexes ensured")


def seed_reference_nodes_for_user(user_id: UUID) -> None:
    """Idempotent MERGE of every reference node for one user. Called by
    `graph_batch.write_day` on first write so the user's domain nodes can
    relate to their own reference vocabulary."""
    from .graph_schema import (
        DIET_QUALITIES, EMOTION_QUADRANTS, EXERCISE_TYPES, SLEEP_QUALITIES
    )
    uid = str(user_id)
    with graph_connect() as session:
        for name in EMOTION_QUADRANTS:
            session.run(
                "MERGE (:EmotionQuadrant {user_id: $user_id, name: $name})",
                user_id=uid, name=name,
            )
        for level in SLEEP_QUALITIES:
            session.run(
                "MERGE (:SleepQuality {user_id: $user_id, level: $level})",
                user_id=uid, level=level,
            )
        for name in EXERCISE_TYPES:
            session.run(
                "MERGE (:ExerciseType {user_id: $user_id, name: $name})",
                user_id=uid, name=name,
            )
        for type_ in DIET_QUALITIES:
            session.run(
                "MERGE (:DietQuality {user_id: $user_id, type: $type})",
                user_id=uid, type=type_,
            )
