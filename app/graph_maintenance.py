"""Post-write maintenance: deduplication and topic category hierarchy."""
import json

from .core import client
from .graph_db import graph_connect


def run() -> dict:
    """Run all maintenance passes. Safe to call multiple times.

    Goal reconciliation runs BEFORE goal Levenshtein dedup so the latter
    operates on a graph that already matches the SQLite source of truth.
    """
    reconcile = reconcile_goals()
    events_merged = _deduplicate_events()
    topics_merged = _deduplicate_and_categorise_topics()
    goals_merged = _deduplicate_goals()
    return {
        "events_merged": events_merged,
        "topics_merged": topics_merged,
        "goals_merged": goals_merged,
        **reconcile,
    }


def reconcile_goals() -> dict:
    """Project SQLite goal state into Neo4j and prune orphans.

    SQLite is the source of truth; every goal mutation calls
    `goals.sync_goal_to_graph` already, but drift can happen if the graph
    write fails mid-flight or if Neo4j was edited directly. This pass
    re-projects every SQLite row and removes any Goal node not present in
    SQLite with status active or fulfilled.
    """
    from . import goals as goals_svc
    from .db import connect

    with connect() as conn:
        rows = conn.execute("SELECT name, status FROM goals").fetchall()

    # After sync_goal_to_graph runs over every SQLite row, the only Neo4j
    # Goal nodes still present are the ones SQLite says should be there.
    # Anything else is an orphan from manual graph edits or earlier drift.
    expected_in_graph = {
        r["name"] for r in rows if r["status"] in ("active", "fulfilled")
    }

    for r in rows:
        goals_svc.sync_goal_to_graph(r["name"])
    reconciled = len(rows)

    orphans_deleted = 0
    with graph_connect() as session:
        result = session.run("MATCH (g:Goal) RETURN g.name AS name")
        graph_names = {r["name"] for r in result}
        for name in graph_names - expected_in_graph:
            print(f"[reconcile_goals] orphan in graph, deleting: {name}")
            session.run("MATCH (g:Goal {name: $name}) DETACH DELETE g", name=name)
            orphans_deleted += 1

    return {"goals_reconciled": reconciled, "goals_orphaned_deleted": orphans_deleted}


def _get_similar_pairs(session, label: str, prop: str, threshold: int) -> list[tuple]:
    # apoc.text.distance is Levenshtein distance — same semantics as the
    # now-removed apoc.text.levenshteinDistance in APOC 2026.04+.
    result = session.run(f"""
        MATCH (a:{label}), (b:{label})
        WHERE id(a) < id(b)
          AND apoc.text.distance(a.{prop}, b.{prop}) < $threshold
        RETURN a.{prop} AS name_a, b.{prop} AS name_b
    """, threshold=threshold)
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


def _deduplicate_events() -> int:
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Event", "title", threshold=3)
        if not pairs:
            return 0
        groups = _connected_components(pairs)
        for group in groups:
            canonical = min(group, key=len)
            others = list(group - {canonical})
            session.run("""
                MATCH (canonical:Event {title: $canonical})
                MATCH (other:Event) WHERE other.title IN $others
                WITH canonical, collect(other) AS dupes
                CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                YIELD node
                RETURN node
            """, canonical=canonical, others=others)
            merged += len(others)
    return merged


def _deduplicate_and_categorise_topics() -> int:
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Topic", "name", threshold=2)
        if pairs:
            groups = _connected_components(pairs)
            for group in groups:
                canonical = min(group, key=len)
                others = list(group - {canonical})
                session.run("""
                    MATCH (canonical:Topic {name: $canonical})
                    MATCH (other:Topic) WHERE other.name IN $others
                    WITH canonical, collect(other) AS dupes
                    CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                    YIELD node
                    SET node.name = $canonical
                    RETURN node
                """, canonical=canonical, others=others)
                merged += len(others)

        result = session.run("MATCH (t:Topic) RETURN t.name AS name")
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
                MATCH (t:Topic {name: $topic})
                MERGE (c:Category {name: $category})
                MERGE (t)-[:BELONGS_TO]->(c)
            """, topic=topic_name, category=category_name)

    return merged


def _deduplicate_goals() -> int:
    merged = 0
    with graph_connect() as session:
        pairs = _get_similar_pairs(session, "Goal", "name", threshold=2)
        if not pairs:
            return 0
        groups = _connected_components(pairs)
        for group in groups:
            canonical = max(group, key=len)
            others = list(group - {canonical})
            session.run("""
                MATCH (canonical:Goal {name: $canonical})
                MATCH (other:Goal) WHERE other.name IN $others
                WITH canonical, collect(other) AS dupes
                CALL apoc.refactor.mergeNodes([canonical] + dupes, {properties: 'override'})
                YIELD node
                SET node.name = $canonical
                RETURN node
            """, canonical=canonical, others=others)
            merged += len(others)
    return merged
