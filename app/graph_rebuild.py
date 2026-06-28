"""Neo4j disaster recovery: rebuild the graph from Postgres.

Postgres is the source of truth; the Neo4j graph is a derived projection of
the extraction tables + goals. When the VPS dies, provision a new one and
run this script to rehydrate.

Usage:
    python -m app.graph_rebuild --user <uuid>   # rebuild one user
    python -m app.graph_rebuild --all           # rebuild every user with data

Per user, this:
  1. DETACH DELETE every node where `n.user_id = $uid`
  2. Seeds the user's reference nodes (EmotionQuadrant / SleepQuality / …)
  3. Iterates every `succeeded` day in `parse_log` for that user and calls
     `graph_batch.sync_day_to_graph` + `ensure_day_chain` to project the
     Postgres extraction rows into Neo4j
  4. Runs `graph_maintenance.run` to deduplicate + categorize topics + re-
     project goals

LLM-driven decisions inside maintenance (Levenshtein dedup, gpt-4o topic
categorization) make this a *semantically equivalent* rebuild, not byte-
identical. For all current uses (morning brief aggregates, LangGraph reads)
that's sufficient.
"""
import argparse
import sys
import traceback
from uuid import UUID

from . import graph_batch, graph_maintenance
from .day_messages import get_all_user_ids_with_messages
from .db import connect
from .graph_db import graph_connect, seed_reference_nodes_for_user


def rebuild_user(user_id: UUID) -> dict:
    uid = str(user_id)
    print(f"[rebuild] user={uid} starting")

    with graph_connect() as session:
        result = session.run(
            "MATCH (n) WHERE n.user_id = $user_id "
            "WITH count(n) AS n_before, collect(n) AS nodes "
            "FOREACH (x IN nodes | DETACH DELETE x) "
            "RETURN n_before",
            user_id=uid,
        ).single()
        deleted = result["n_before"] if result else 0
    print(f"[rebuild] user={uid} dropped {deleted} existing nodes")

    seed_reference_nodes_for_user(user_id)

    with connect() as conn:
        rows = conn.execute(
            "SELECT day FROM parse_log WHERE user_id = %s AND status = 'succeeded' "
            "ORDER BY day",
            (uid,),
        ).fetchall()
    days = [r["day"] for r in rows]
    print(f"[rebuild] user={uid} replaying {len(days)} succeeded days")

    written = 0
    for day in days:
        try:
            graph_batch.sync_day_to_graph(day, user_id)
            written += 1
        except Exception:
            print(f"[rebuild] user={uid} failed to sync day={day}")
            traceback.print_exc()

    if days:
        try:
            graph_batch.ensure_day_chain(days[0], days[-1], user_id)
        except Exception:
            print(f"[rebuild] user={uid} ensure_day_chain failed")
            traceback.print_exc()

    try:
        from .notifications_prefs import get_user_tz
        maint = graph_maintenance.run(user_id, get_user_tz(user_id))
        print(f"[rebuild] user={uid} maintenance: {maint}")
    except Exception:
        print(f"[rebuild] user={uid} maintenance failed")
        traceback.print_exc()

    return {"user_id": uid, "nodes_deleted": deleted, "days_synced": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild Neo4j from Postgres.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", help="UUID of a single user to rebuild")
    group.add_argument("--all", action="store_true", help="Rebuild every user")
    args = parser.parse_args(argv)

    if args.all:
        user_ids = get_all_user_ids_with_messages()
    else:
        user_ids = [UUID(args.user)]

    if not user_ids:
        print("[rebuild] no users with messages; nothing to do")
        return 0

    print(f"[rebuild] processing {len(user_ids)} user(s)")
    summary = []
    for uid in user_ids:
        try:
            summary.append(rebuild_user(uid))
        except Exception:
            print(f"[rebuild] user={uid} fatal error")
            traceback.print_exc()
            summary.append({"user_id": str(uid), "error": "fatal"})

    print(f"[rebuild] done: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
