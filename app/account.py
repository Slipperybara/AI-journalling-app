"""Account deletion (GDPR / App Store guideline 5.1.1(v)).

Erases every trace of a user from our datastores: all Postgres rows across the
multi-tenant tables (scoped by `user_id`), then the user's entire Neo4j
subgraph. The Supabase *auth identity* itself is deleted client-side via a
`SECURITY DEFINER` RPC (`public.delete_account`) — the backend never holds the
service-role key (CLAUDE.md: "No service-role usage from the backend").

Postgres is the source of truth, so its wipe runs first and is authoritative.
The graph is a derived projection; if it's momentarily unreachable the account
data is still gone and the graph can be reconciled away later.
"""
from uuid import UUID

from .db import connect

# Every user-scoped table. `messages` references `conversations`, so it's
# deleted first; the rest are independent. `goals` IS included here — unlike the
# nightly re-parse (which preserves it), a full account deletion removes it too.
_USER_TABLES = (
    "messages",
    "conversations",
    "parse_log",
    "morning_brief_log",
    "notification_prefs",
    "dashboard_summary",
    "user_profile",
    "device_tokens",
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "event_topics",
    "event_goal_contributions",
    "goals",
)


def _delete_graph(user_id: UUID) -> int:
    """DETACH DELETE the user's entire Neo4j subgraph; returns nodes removed."""
    from .graph_db import graph_connect

    uid = str(user_id)
    with graph_connect() as session:
        rec = session.run(
            "MATCH (n) WHERE n.user_id = $user_id "
            "WITH collect(n) AS nodes, count(n) AS n "
            "FOREACH (x IN nodes | DETACH DELETE x) RETURN n",
            user_id=uid,
        ).single()
        return rec["n"] if rec else 0


def delete_account(user_id: UUID) -> dict:
    """Wipe all of a user's app data. Table names are a fixed whitelist (never
    user input), so the f-string DELETE is injection-safe."""
    uid = str(user_id)
    deleted: dict[str, int] = {}
    with connect() as conn:
        for table in _USER_TABLES:
            cur = conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (uid,))
            deleted[table] = cur.rowcount

    # Best-effort: a graph outage must not block (or reverse) the Postgres wipe.
    try:
        graph_nodes = _delete_graph(user_id)
    except Exception:
        graph_nodes = -1  # graph unreachable; data is still gone in Postgres

    return {"postgres": deleted, "graph_nodes_deleted": graph_nodes}
