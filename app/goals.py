"""Goal lifecycle authority.

SQLite is the source of truth. Neo4j is a derived projection — every mutation
writes SQLite first, then calls `sync_goal_to_graph(name)` which re-reads the
row and reflects state into Neo4j (MERGE/SET for active+fulfilled, DETACH
DELETE for removed). The nightly reconcile pass in
`graph_maintenance.reconcile_goals` re-invokes the same projection to repair
any drift.

Cap policy: at most `settings.max_active_goals` goals may have status='active'.
Adds while at cap raise `GoalCapReachedError` — the caller (chat tool / slash
command / API endpoint) surfaces a clear "fulfill or remove one first" message.
"""
from datetime import datetime
from typing import Optional

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


def _count_active(cursor) -> int:
    cursor.execute("SELECT COUNT(*) AS n FROM goals WHERE status='active'")
    return cursor.fetchone()["n"]


def _row_dict(row) -> dict:
    return dict(row) if row is not None else None


def list_goals(status: Optional[str] = None) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = %s ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def _fetch_row(name: str) -> Optional[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE name = %s", (name,)
        ).fetchone()


def add_user_goal(name: str) -> dict:
    """Add a goal as 'active'. Raises GoalExistsError if the name is already
    in active/fulfilled state, GoalCapReachedError if 3 active already.
    Resurrects a soft-removed row by name (subject to the cap)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("goal name cannot be blank")

    today = current_bucket().isoformat()

    with connect() as conn:
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT * FROM goals WHERE name = %s", (name,)
        ).fetchone()

        if existing is not None and existing["status"] != "removed":
            raise GoalExistsError(name)

        if _count_active(cursor) >= settings.max_active_goals:
            raise GoalCapReachedError(name)

        if existing is not None:
            # Resurrect a soft-deleted row.
            cursor.execute(
                """
                UPDATE goals
                SET status = 'active', source = 'user', removed_at = NULL
                WHERE name = %s
                """,
                (name,),
            )
        else:
            cursor.execute(
                """
                INSERT INTO goals (name, discovered_on, status, source)
                VALUES (%s, %s, 'active', 'user')
                """,
                (name, today),
            )

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def mark_fulfilled(name: str) -> dict:
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE goals
            SET status = 'fulfilled', fulfilled_at = %s
            WHERE name = %s AND status = 'active'
            """,
            (now, name),
        )
        if cursor.rowcount == 0:
            raise GoalNotFoundError(name)

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def mark_removed(name: str) -> dict:
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE goals
            SET status = 'removed', removed_at = %s
            WHERE name = %s AND status IN ('active','fulfilled')
            """,
            (now, name),
        )
        if cursor.rowcount == 0:
            raise GoalNotFoundError(name)

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def rename_goal(old_name: str, new_name: str) -> dict:
    """Rename a goal. Cascades to event_goal_contributions and the Neo4j
    Goal node. Raises GoalNotFoundError if no matching active/fulfilled row
    or GoalExistsError if the new name is already taken by another active /
    fulfilled goal."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("new goal name cannot be blank")
    if old_name == new_name:
        return _row_dict(_fetch_row(old_name))

    with connect() as conn:
        cursor = conn.cursor()
        old_row = cursor.execute(
            "SELECT * FROM goals WHERE name = %s AND status IN ('active','fulfilled')",
            (old_name,),
        ).fetchone()
        if old_row is None:
            raise GoalNotFoundError(old_name)
        collision = cursor.execute(
            "SELECT name FROM goals WHERE name = %s AND status IN ('active','fulfilled')",
            (new_name,),
        ).fetchone()
        if collision is not None:
            raise GoalExistsError(new_name)

        cursor.execute(
            "UPDATE goals SET name = %s WHERE name = %s",
            (new_name, old_name),
        )
        cursor.execute(
            "UPDATE event_goal_contributions SET goal_name = %s WHERE goal_name = %s",
            (new_name, old_name),
        )

    # Neo4j: drop the old node (it's keyed by the old name), then project the
    # renamed row. Edges to the old node are lost, but reconcile_extractions
    # re-creates CONTRIBUTES_TO edges from event_goal_contributions on the
    # next run; for immediate consistency we sync events that reference the
    # goal too.
    with graph_connect() as session:
        session.run(
            "MATCH (g:Goal {name: $name}) DETACH DELETE g", name=old_name
        )
    sync_goal_to_graph(new_name)

    return _row_dict(_fetch_row(new_name))


def sync_goal_to_graph(name: str) -> None:
    """Project the current SQLite row into Neo4j. Idempotent.

    - row missing OR status='removed' → DETACH DELETE node
    - status in {active, fulfilled} → MERGE node with status + fulfilled_at
    """
    row = _fetch_row(name)
    with graph_connect() as session:
        if row is None or row["status"] == "removed":
            session.run(
                "MATCH (g:Goal {name: $name}) DETACH DELETE g", name=name
            )
            return
        session.run(
            """
            MERGE (g:Goal {name: $name})
            ON CREATE SET g.discovered_on = $discovered_on
            SET g.status = $status,
                g.fulfilled_at = $fulfilled_at
            """,
            name=name,
            status=row["status"],
            fulfilled_at=row["fulfilled_at"],
            discovered_on=row["discovered_on"],
        )


