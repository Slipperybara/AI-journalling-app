"""Goal lifecycle authority.

SQLite is the source of truth. Neo4j is a derived projection — every mutation
writes SQLite first, then calls `sync_goal_to_graph(name)` which re-reads the
row and reflects state into Neo4j (MERGE/SET for active+fulfilled, DETACH
DELETE for removed/candidate). The nightly reconcile pass in
`graph_maintenance.reconcile_goals` re-invokes the same projection to repair
any drift.

Cap policy: at most `settings.max_active_goals` goals may have status='active'.
Excess goals (both agent-discovered and user-added) land as 'candidate' and
sit in SQLite only. There is no auto-promote — a freed slot stays open until
the user explicitly promotes a candidate.
"""
import json
import sqlite3
from datetime import datetime
from typing import Optional

from .core import client, settings
from .db import connect
from .graph_db import graph_connect
from .time_buckets import current_bucket


VALID_STATUSES = {"active", "fulfilled", "removed", "candidate"}


class GoalExistsError(Exception):
    """Raised when add_user_goal sees an existing non-removed row with the same name."""


class GoalNotFoundError(Exception):
    """Raised when a state transition targets a name with no matching row in the expected status."""


class GoalCapReachedError(Exception):
    """Raised when promote_candidate runs while max_active_goals is already active."""


def _count_active(cursor: sqlite3.Cursor) -> int:
    cursor.execute("SELECT COUNT(*) AS n FROM goals WHERE status='active'")
    return cursor.fetchone()["n"]


def _new_status_under_cap(cursor: sqlite3.Cursor) -> str:
    return "active" if _count_active(cursor) < settings.max_active_goals else "candidate"


def _row_dict(row: sqlite3.Row) -> dict:
    return dict(row) if row is not None else None


def list_goals(status: Optional[str] = None) -> list[dict]:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def _fetch_row(name: str) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM goals WHERE name = ?", (name,)
        ).fetchone()


def add_user_goal(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("goal name cannot be blank")

    today = current_bucket().isoformat()

    with connect() as conn:
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT * FROM goals WHERE name = ?", (name,)
        ).fetchone()

        if existing is not None and existing["status"] != "removed":
            raise GoalExistsError(name)

        new_status = _new_status_under_cap(cursor)

        if existing is not None:
            # Resurrect a soft-deleted row.
            cursor.execute(
                """
                UPDATE goals
                SET status = ?, source = 'user', removed_at = NULL
                WHERE name = ?
                """,
                (new_status, name),
            )
        else:
            cursor.execute(
                """
                INSERT INTO goals (name, discovered_on, status, source)
                VALUES (?, ?, ?, 'user')
                """,
                (name, today, new_status),
            )

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def add_agent_goal(name: str, day: str) -> dict:
    """Insert (or skip) an LLM-discovered goal. Runs semantic dedup against
    existing active+fulfilled goals; if a dupe is found, returns the canonical
    existing row and inserts nothing."""
    name = (name or "").strip()
    if not name:
        return None

    with connect() as conn:
        cursor = conn.cursor()
        existing = cursor.execute(
            "SELECT * FROM goals WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None:
            return dict(existing)

        candidates = cursor.execute(
            "SELECT name FROM goals WHERE status IN ('active','fulfilled')"
        ).fetchall()
        existing_names = [r["name"] for r in candidates]

    canonical = _semantic_dedup_against_existing(name, existing_names)
    if canonical is not None:
        return _row_dict(_fetch_row(canonical))

    with connect() as conn:
        cursor = conn.cursor()
        new_status = _new_status_under_cap(cursor)
        cursor.execute(
            """
            INSERT INTO goals (name, discovered_on, status, source)
            VALUES (?, ?, ?, 'agent')
            """,
            (name, day, new_status),
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
            SET status = 'fulfilled', fulfilled_at = ?
            WHERE name = ? AND status IN ('active','candidate')
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
            SET status = 'removed', removed_at = ?
            WHERE name = ? AND status IN ('active','fulfilled','candidate')
            """,
            (now, name),
        )
        if cursor.rowcount == 0:
            raise GoalNotFoundError(name)

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def promote_candidate(name: str) -> dict:
    with connect() as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT * FROM goals WHERE name = ?", (name,)
        ).fetchone()
        if row is None or row["status"] != "candidate":
            raise GoalNotFoundError(name)
        if _count_active(cursor) >= settings.max_active_goals:
            raise GoalCapReachedError(name)
        cursor.execute(
            "UPDATE goals SET status = 'active' WHERE name = ?", (name,)
        )

    sync_goal_to_graph(name)
    return _row_dict(_fetch_row(name))


def sync_goal_to_graph(name: str) -> None:
    """Project the current SQLite row into Neo4j. Idempotent.

    - row missing OR status in {removed, candidate} → DETACH DELETE node
    - status in {active, fulfilled} → MERGE node with status + fulfilled_at
    """
    row = _fetch_row(name)
    with graph_connect() as session:
        if row is None or row["status"] in ("removed", "candidate"):
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


def _semantic_dedup_against_existing(
    candidate_name: str, existing_names: list[str]
) -> Optional[str]:
    """Ask gpt-4o whether `candidate_name` is a semantic duplicate of any
    existing goal. Returns the matched existing name, or None.

    Defends against hallucination by verifying the returned name is in the
    provided list."""
    if not existing_names:
        return None

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are deduplicating user-tracked long-term goals. "
                    "Given a new candidate goal and a list of existing goals, "
                    "return JSON {\"duplicate_of\": \"<existing name>\"} if the "
                    "candidate is semantically the same objective as one of the "
                    "existing names (different phrasing of the same goal), or "
                    "{\"duplicate_of\": null} otherwise. Only match true duplicates; "
                    "preserve nuance — e.g. 'Run a marathon' and 'Run a half marathon' "
                    "are NOT duplicates."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Candidate: {candidate_name}\n"
                    f"Existing goals: {json.dumps(existing_names)}"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        data = json.loads(response.choices[0].message.content)
    except (json.JSONDecodeError, AttributeError):
        return None

    match = data.get("duplicate_of")
    if isinstance(match, str) and match in existing_names:
        return match
    return None
