"""Post-write maintenance: deduplication and topic category hierarchy.

Phase 2 multi-tenant: every pass takes `user_id` and scopes both Postgres
reads and Neo4j writes. Dedup compares only nodes belonging to the same
user — two users' "morning run" events stay separate.
"""
import json
from uuid import UUID

from .core import client
from .graph_db import graph_connect


def run(user_id: UUID) -> dict:
    """Run all maintenance passes for one user. Safe to call multiple times.

    Reconciliation runs BEFORE Levenshtein dedup so the latter operates on
    a graph that already matches the Postgres source of truth.
    """
    days_reconciled = reconcile_extractions_and_chain(user_id)
    goals_reconciled = reconcile_goals(user_id)
    events_merged = _deduplicate_events(user_id)
    topics_merged = _deduplicate_and_categorise_topics(user_id)
    goals_merged = _deduplicate_goals(user_id)
    return {
        "events_merged": events_merged,
        "topics_merged": topics_merged,
        "goals_merged": goals_merged,
        **goals_reconciled,
        **days_reconciled,
    }


def reconcile_extractions_and_chain(user_id: UUID) -> dict:
    """Project Postgres per-day state into Neo4j and ensure the Day chain
    for this user."""
    from . import graph_batch
    from .db import connect
    from .time_buckets import bucket_sql_expr

    uid = str(user_id)
    bucket_expr = bucket_sql_expr("m.created_at")
    with connect() as conn:
        sqlite_days = {
            r["day"] for r in conn.execute(
                "SELECT day FROM parse_log WHERE user_id = %s", (uid,)
            ).fetchall()
        }
        msg_days = {
            r["day"]
            for r in conn.execute(
                f"SELECT DISTINCT {bucket_expr}::text AS day "
                "FROM messages m WHERE m.user_id = %s AND m.role = 'user'",
                (uid,),
            ).fetchall()
        }
        succeeded_days = {
            r["day"]
            for r in conn.execute(
                "SELECT day FROM parse_log WHERE user_id = %s AND status = 'succeeded'",
                (uid,),
            ).fetchall()
        }

    all_known = sqlite_days | msg_days
    if not all_known:
        return {
            "days_chain_nodes": 0,
            "days_chain_edges": 0,
            "days_synced": 0,
            "days_orphaned_deleted": 0,
        }

    start_day = min(all_known)
    end_day = max(all_known)

    chain_result = graph_batch.ensure_day_chain(start_day, end_day, user_id)

    synced = 0
    for day in sorted(succeeded_days):
        graph_batch.sync_day_to_graph(day, user_id)
        synced += 1

    # Prune any Day node belonging to this user that falls outside the
    # known range — catches stale test fixtures and manual graph drift.
    orphans_deleted = 0
    with graph_connect() as session:
        result = session.run(
            "MATCH (d:Day {user_id: $user_id}) "
            "WHERE d.date < $start OR d.date > $end RETURN d.date AS date",
            user_id=uid, start=start_day, end=end_day,
        )
        orphan_dates = [r["date"] for r in result]
        for d in orphan_dates:
            session.run(
                "MATCH (d:Day {user_id: $user_id, date: $date}) DETACH DELETE d",
                user_id=uid, date=d,
            )
            orphans_deleted += 1
            print(f"[reconcile_days] user={uid} pruned orphan Day: {d}")

    return {
        "days_chain_nodes": chain_result["nodes_ensured"],
        "days_chain_edges": chain_result["edges_ensured"],
        "days_synced": synced,
        "days_orphaned_deleted": orphans_deleted,
    }


def reconcile_goals(user_id: UUID) -> dict:
    """Project Postgres goal state into Neo4j and prune orphans for one user."""
    from . import goals as goals_svc
    from .db import connect

    uid = str(user_id)
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, status FROM goals WHERE user_id = %s", (uid,)
        ).fetchall()

    expected_in_graph = {
        r["name"] for r in rows if r["status"] in ("active", "fulfilled")
    }

    for r in rows:
        goals_svc.sync_goal_to_graph(r["name"], user_id)
    reconciled = len(rows)

    orphans_deleted = 0
    with graph_connect() as session:
        result = session.run(
            "MATCH (g:Goal {user_id: $user_id}) RETURN g.name AS name",
            user_id=uid,
        )
        graph_names = {r["name"] for r in result}
        for name in graph_names - expected_in_graph:
            print(f"[reconcile_goals] user={uid} orphan in graph, deleting: {name}")
            session.run(
                "MATCH (g:Goal {user_id: $user_id, name: $name}) DETACH DELETE g",
                user_id=uid, name=name,
            )
            orphans_deleted += 1

    return {"goals_reconciled": reconciled, "goals_orphaned_deleted": orphans_deleted}


def _get_similar_pairs(session, label: str, prop: str, threshold: int, user_id: str) -> list[tuple]:
    # apoc.text.distance is Levenshtein. Compare only nodes belonging to the
    # same user — cross-user dedup would corrupt isolation.
    result = session.run(f"""
        MATCH (a:{label} {{user_id: $user_id}}), (b:{label} {{user_id: $user_id}})
        WHERE id(a) < id(b)
          AND apoc.text.distance(a.{prop}, b.{prop}) < $threshold
        RETURN a.{prop} AS name_a, b.{prop} AS name_b
    """, threshold=threshold, user_id=user_id)
    return [(r["name_a"], r["name_b"]) for r in result]


def _connected_components(pairs: list[tuple]) -> list[set]:
    """Union-find to group connected pairs into clusters."""
    parent: dict[str, str] = {}

    def find(x):
        if x not in parent:
            parent[x] = x
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(x, y):
        parent[find(x)] = find(y)

    for a, b in pairs:
        union(a, b)

    groups: dict[str, set] = {}
    for node in parent:
        root = find(node)
        groups.setdefault(root, set()).add(node)

    return [g for g in groups.values() if len(g) > 1]


def _deduplicate_events(user_id: UUID) -> int:
    uid = str(user_id)
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Event", "title", threshold=3, user_id=uid)
        if not pairs:
            return 0
        groups = _connected_components(pairs)
        for group in groups:
            canonical = min(group, key=len)
            others = list(group - {canonical})
            session.run("""
                MATCH (canonical:Event {user_id: $user_id, title: $canonical})
                MATCH (other:Event {user_id: $user_id}) WHERE other.title IN $others
                WITH canonical, collect(other) AS dupes
                CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                YIELD node
                RETURN node
            """, user_id=uid, canonical=canonical, others=others)
            merged += len(others)
    return merged


def _deduplicate_and_categorise_topics(user_id: UUID) -> int:
    uid = str(user_id)
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Topic", "name", threshold=2, user_id=uid)
        if pairs:
            groups = _connected_components(pairs)
            for group in groups:
                canonical = min(group, key=len)
                others = list(group - {canonical})
                session.run("""
                    MATCH (canonical:Topic {user_id: $user_id, name: $canonical})
                    MATCH (other:Topic {user_id: $user_id}) WHERE other.name IN $others
                    WITH canonical, collect(other) AS dupes
                    CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                    YIELD node
                    SET node.name = $canonical
                    RETURN node
                """, user_id=uid, canonical=canonical, others=others)
                merged += len(others)

        result = session.run(
            "MATCH (t:Topic {user_id: $user_id}) RETURN t.name AS name",
            user_id=uid,
        )
        all_topics = [r["name"] for r in result]

    if not all_topics:
        return merged

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Assign each topic to a broad category. "
                    "Respond with JSON only: {\"topic_name\": \"category_name\"}. "
                    "Use 2-4 word categories like 'AI/ML', 'Career', 'Health', "
                    "'Computer Science', 'Personal Development', 'Finance', 'Systems'. "
                    "Every topic must be assigned."
                ),
            },
            {"role": "user", "content": f"Topics: {', '.join(all_topics)}"},
        ],
        response_format={"type": "json_object"},
    )

    assignments: dict[str, str] = json.loads(response.choices[0].message.content)

    with graph_connect() as session:
        for topic_name, category_name in assignments.items():
            session.run("""
                MATCH (t:Topic {user_id: $user_id, name: $topic})
                MERGE (c:Category {user_id: $user_id, name: $category})
                MERGE (t)-[:BELONGS_TO]->(c)
            """, user_id=uid, topic=topic_name, category=category_name)

    return merged


def _deduplicate_goals(user_id: UUID) -> int:
    uid = str(user_id)
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Goal", "name", threshold=2, user_id=uid)
        if not pairs:
            return 0
        groups = _connected_components(pairs)
        for group in groups:
            canonical = max(group, key=len)
            others = list(group - {canonical})
            session.run("""
                MATCH (canonical:Goal {user_id: $user_id, name: $canonical})
                MATCH (other:Goal {user_id: $user_id}) WHERE other.name IN $others
                WITH canonical, collect(other) AS dupes
                CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                YIELD node
                SET node.name = $canonical
                RETURN node
            """, user_id=uid, canonical=canonical, others=others)
            merged += len(others)
    return merged
