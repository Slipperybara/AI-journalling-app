"""Goal lifecycle authority (multi-tenant).

Postgres is the source of truth; Neo4j is a derived projection. Every mutation
writes Postgres first then calls `sync_goal_to_graph(name, user_id)` which
re-reads the row and reflects state into Neo4j (MERGE/SET for active+
fulfilled, DETACH DELETE for removed). The nightly reconcile pass in
`graph_maintenance.reconcile_goals(user_id)` re-invokes the same projection
to repair any drift.

Cap policy: at most `settings.max_active_goals` active goals per user. Adds
while at cap raise `GoalCapReachedError` — the caller (chat tool / slash
command / API endpoint) surfaces a clear "fulfill or remove one first"
message.

Every Neo4j Goal node carries `user_id`. Every Cypher MERGE keys on
`(user_id, name)` so two users with a same-named goal stay isolated.
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from .core import settings
from .db import connect
from .graph_db import graph_connect
from .time_buckets import current_bucket


VALID_STATUSES = {"active", "fulfilled", "removed"}


class GoalExistsError(Exception):
    """Raised when add_user_goal sees an existing non-removed row with the same name."""


class GoalNotFoundError(Exception):
    """Raised when a state transition targets a name with no matching row in the expected status."""


class GoalCapReachedError(Exception):
    """Raised when add or rename would push active goal count above the cap."""


def _count_active(cursor, user_id: UUID) -> int:
    cursor.execute(
        "SELECT COUNT(*) AS n FROM goals WHERE user_id = %s AND status = 'active'",
        (str(user_id),),
    )
    return cursor.fetchone()["n"]


def _row_dict(row) -> dict:
    return dict(row) if row is not None else None


def list_goals(user_id: UUID, status: Optional[str] = None) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM goals WHERE user_id = %s AND status = %s ORDER BY created_at DESC",
                (str(user_id), status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM goals WHERE user_id = %s ORDER BY created_at DESC",
                (str(user_id),),
            ).fetchall()
    return [dict(r) for r in rows]


def _fetch_row(name: str, user_id: UUID) -> Optional[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE user_id = %s AND name = %s",
            (str(user_id), name),
        ).fetchone()


def add_user_goal(name: str, user_id: UUID) -> dict:
    """Add a goal as 'active'. Raises GoalExistsError if the name is already
    in active/fulfilled state for this user, GoalCapReachedError if the user
    is already at the active-goals cap. Resurrects a soft-removed row by
    name (subject to the cap)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("goal name cannot be blank")

    today = current_bucket().isoformat()

    with connect() as conn:
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT * FROM goals WHERE user_id = %s AND name = %s",
            (str(user_id), name),
        ).fetchone()

        if existing is not None and existing["status"] != "removed":
            raise GoalExistsError(name)

        if _count_active(cursor, user_id) >= settings.max_active_goals:
            raise GoalCapReachedError(name)

        if existing is not None:
            cursor.execute(
                """
                UPDATE goals
                SET status = 'active', source = 'user', removed_at = NULL
                WHERE user_id = %s AND name = %s
                """,
                (str(user_id), name),
            )
        else:
            cursor.execute(
                """
                INSERT INTO goals (user_id, name, discovered_on, status, source)
                VALUES (%s, %s, %s, 'active', 'user')
                """,
                (str(user_id), name, today),
            )

    sync_goal_to_graph(name, user_id)
    return _row_dict(_fetch_row(name, user_id))


def mark_fulfilled(name: str, user_id: UUID) -> dict:
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE goals
            SET status = 'fulfilled', fulfilled_at = %s
            WHERE user_id = %s AND name = %s AND status = 'active'
            """,
            (now, str(user_id), name),
        )
        if cursor.rowcount == 0:
            raise GoalNotFoundError(name)

    sync_goal_to_graph(name, user_id)
    return _row_dict(_fetch_row(name, user_id))


def mark_removed(name: str, user_id: UUID) -> dict:
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE goals
            SET status = 'removed', removed_at = %s
            WHERE user_id = %s AND name = %s AND status IN ('active','fulfilled')
            """,
            (now, str(user_id), name),
        )
        if cursor.rowcount == 0:
            raise GoalNotFoundError(name)

    sync_goal_to_graph(name, user_id)
    return _row_dict(_fetch_row(name, user_id))


def rename_goal(old_name: str, new_name: str, user_id: UUID) -> dict:
    """Rename a goal. Cascades to event_goal_contributions and the Neo4j
    Goal node. Raises GoalNotFoundError if no matching active/fulfilled row
    or GoalExistsError if the new name is already taken by another active /
    fulfilled goal *for the same user*."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("new goal name cannot be blank")
    if old_name == new_name:
        return _row_dict(_fetch_row(old_name, user_id))

    with connect() as conn:
        cursor = conn.cursor()
        old_row = cursor.execute(
            "SELECT * FROM goals WHERE user_id = %s AND name = %s AND status IN ('active','fulfilled')",
            (str(user_id), old_name),
        ).fetchone()
        if old_row is None:
            raise GoalNotFoundError(old_name)
        collision = cursor.execute(
            "SELECT name FROM goals WHERE user_id = %s AND name = %s AND status IN ('active','fulfilled')",
            (str(user_id), new_name),
        ).fetchone()
        if collision is not None:
            raise GoalExistsError(new_name)

        cursor.execute(
            "UPDATE goals SET name = %s WHERE user_id = %s AND name = %s",
            (new_name, str(user_id), old_name),
        )
        cursor.execute(
            "UPDATE event_goal_contributions SET goal_name = %s WHERE user_id = %s AND goal_name = %s",
            (new_name, str(user_id), old_name),
        )

    with graph_connect() as session:
        session.run(
            "MATCH (g:Goal {user_id: $user_id, name: $name}) DETACH DELETE g",
            user_id=str(user_id), name=old_name,
        )
    sync_goal_to_graph(new_name, user_id)

    return _row_dict(_fetch_row(new_name, user_id))


def sync_goal_to_graph(name: str, user_id: UUID) -> None:
    """Project the current Postgres row into Neo4j. Idempotent.

    - row missing OR status='removed' → DETACH DELETE node
    - status in {active, fulfilled} → MERGE node with status + fulfilled_at

    All operations keyed on `(user_id, name)` so two users' same-named goals
    stay separate.
    """
    row = _fetch_row(name, user_id)
    with graph_connect() as session:
        if row is None or row["status"] == "removed":
            session.run(
                "MATCH (g:Goal {user_id: $user_id, name: $name}) DETACH DELETE g",
                user_id=str(user_id), name=name,
            )
            return
        session.run(
            """
            MERGE (g:Goal {user_id: $user_id, name: $name})
            ON CREATE SET g.discovered_on = $discovered_on
            SET g.status = $status,
                g.fulfilled_at = $fulfilled_at
            """,
            user_id=str(user_id),
            name=name,
            status=row["status"],
            fulfilled_at=row["fulfilled_at"],
            discovered_on=row["discovered_on"],
        )
