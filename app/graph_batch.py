"""Write pipeline: SQLite extraction rows → Neo4j graph for one day-bucket."""
import hashlib
from datetime import date, timedelta

from .db import connect, loads
from .graph_db import graph_connect


def _canonical_id(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def write_day(day: str) -> dict:
    """Batch entrypoint: project one day's SQLite extractions into Neo4j and
    ensure the chain links yesterday→day. Thin wrapper around the two
    idempotent primitives that the reconciliation pass also reuses."""
    with connect() as conn:
        log_row = conn.execute(
            "SELECT status FROM parse_log WHERE day = ?", (day,)
        ).fetchone()

    if not log_row or log_row["status"] != "succeeded":
        return {"status": "skipped", "reason": "parse_log not succeeded", "day": day}

    sync_result = sync_day_to_graph(day)
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    ensure_day_chain(prev, day)
    return {"status": "ok", **sync_result}


def sync_day_to_graph(day: str) -> dict:
    """Idempotent projection of one day's SQLite state into Neo4j.

    Reads emotion/health/productivity/events/event_topics/event_goal_contributions
    plus the active+fulfilled goals list, then projects:
      - Day node properties (productivity fields)
      - EmotionState (delete-then-create in one tx)
      - HealthState (delete-then-create in one tx)
      - Event nodes + INVOLVES/CONTRIBUTES_TO edges

    Does NOT touch the NEXT_DAY chain — that's `ensure_day_chain`'s job.
    Safe to call repeatedly; each step uses MERGE / DETACH DELETE+CREATE
    patterns that converge to the same state.
    """
    with connect() as conn:
        emotion = conn.execute(
            "SELECT * FROM emotional_analysis WHERE day = ?", (day,)
        ).fetchone()
        health = conn.execute(
            "SELECT * FROM health_metrics WHERE day = ?", (day,)
        ).fetchone()
        productivity = conn.execute(
            "SELECT * FROM productivity_metrics WHERE day = ?", (day,)
        ).fetchone()
        events = conn.execute(
            "SELECT * FROM events WHERE day = ?", (day,)
        ).fetchall()
        topic_rows = conn.execute(
            "SELECT event_title, topic FROM event_topics WHERE day = ?", (day,)
        ).fetchall()
        goal_rows = conn.execute(
            "SELECT event_title, goal_name FROM event_goal_contributions WHERE day = ?", (day,)
        ).fetchall()
        goals = conn.execute(
            "SELECT name, discovered_on, status, fulfilled_at FROM goals "
            "WHERE status IN ('active','fulfilled')"
        ).fetchall()

    topics_by_event: dict[str, list[str]] = {}
    for r in topic_rows:
        topics_by_event.setdefault(r["event_title"], []).append(r["topic"])

    goals_by_event: dict[str, list[str]] = {}
    for r in goal_rows:
        goals_by_event.setdefault(r["event_title"], []).append(r["goal_name"])

    with graph_connect() as session:
        _write_day_node(session, day, productivity)
        _write_goals(session, goals)
        if emotion:
            _write_emotion(session, day, emotion)
        if health:
            _write_health(session, day, health)
        for event in events:
            _write_event(session, day, event, topics_by_event, goals_by_event)

    return {"day": day, "events": len(events)}


def ensure_day_chain(start_day: str, end_day: str) -> dict:
    """Walk [start_day, end_day] inclusive. MERGE a Day node for every date
    in the range and a NEXT_DAY edge between each consecutive pair.

    Idempotent. Handles the case where the batch only ran on sparse days —
    by walking the whole range, no calendar gaps remain in the chain.
    """
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    if end < start:
        return {"nodes_ensured": 0, "edges_ensured": 0}

    days = []
    cur = start
    while cur <= end:
        days.append(cur.isoformat())
        cur += timedelta(days=1)

    with graph_connect() as session:
        for d in days:
            session.run("MERGE (:Day {date: $date})", date=d)
        for prev, nxt in zip(days, days[1:]):
            session.run(
                """
                MATCH (a:Day {date: $a})
                MATCH (b:Day {date: $b})
                MERGE (a)-[:NEXT_DAY]->(b)
                """,
                a=prev,
                b=nxt,
            )

    return {"nodes_ensured": len(days), "edges_ensured": max(0, len(days) - 1)}


def _write_goals(session, goals) -> None:
    """MERGE each goal node, projecting current SQLite status. Only called
    with rows whose status is active or fulfilled — candidates and removed
    goals never appear in Neo4j."""
    for row in goals:
        session.run("""
            MERGE (g:Goal {name: $name})
            ON CREATE SET g.discovered_on = $discovered_on
            SET g.status = $status,
                g.fulfilled_at = $fulfilled_at
        """,
            name=row["name"],
            status=row["status"],
            fulfilled_at=row["fulfilled_at"],
            discovered_on=row["discovered_on"],
        )


def _write_day_node(session, day: str, productivity) -> None:
    session.run("""
        MERGE (d:Day {date: $date})
        SET d.deep_work_hours      = $deep_work_hours,
            d.shallow_work_hours   = $shallow_work_hours,
            d.time_block_adherence = $time_block_adherence,
            d.cognitive_load       = $cognitive_load,
            d.friction_points      = $friction_points
    """,
        date=day,
        deep_work_hours=productivity["deep_work_hours"] if productivity else None,
        shallow_work_hours=productivity["shallow_work_hours"] if productivity else None,
        time_block_adherence=productivity["time_block_adherence"] if productivity else None,
        cognitive_load=productivity["cognitive_load"] if productivity else None,
        friction_points=loads(productivity["friction_points"]) if productivity else [],
    )


def _write_emotion(session, day: str, emotion) -> None:
    # Delete-then-create in one transaction so a crash mid-write cannot leave
    # the Day without an EmotionState. CREATE is mandatory because each batch
    # should produce a fresh EmotionState (old values may be stale).
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (d:Day {date: $day})-[:HAD_EMOTION]->(old:EmotionState)
            DETACH DELETE old
        """, day=day)
        tx.run("""
            MATCH (d:Day {date: $day})
            MATCH (q:EmotionQuadrant {name: $quadrant})
            CREATE (es:EmotionState {
                valence:              $valence,
                arousal:              $arousal,
                cognitive_labels:     $labels,
                cognitive_triggers:   $triggers,
                social_interactions:  $social
            })
            MERGE (d)-[:HAD_EMOTION]->(es)
            MERGE (es)-[:IN_QUADRANT]->(q)
        """,
            day=day,
            quadrant=emotion["primary_quadrant"],
            valence=emotion["valence"],
            arousal=emotion["arousal"],
            labels=loads(emotion["cognitive_labels"]),
            triggers=loads(emotion["cognitive_triggers"]),
            social=loads(emotion["social_interactions"]),
        )
        tx.commit()


def _write_health(session, day: str, health) -> None:
    sleep = health["sleep_quality"]
    exercise = health["exercise_type"]
    diet = health["diet_quality"]

    query = """
        MATCH (d:Day {date: $day})
        CREATE (hs:HealthState {
            somatic_sensations:  $somatic,
            physical_performance: $performance,
            supplements:         $supplements
        })
        MERGE (d)-[:HAD_HEALTH]->(hs)
    """
    params = dict(
        day=day,
        somatic=loads(health["somatic_sensations"]),
        performance=health["physical_performance"],
        supplements=loads(health["supplements"]),
    )

    if sleep:
        query += " WITH hs MATCH (sq:SleepQuality {level: $sleep}) MERGE (hs)-[:HAD_SLEEP]->(sq)"
        params["sleep"] = sleep
    if exercise:
        query += " WITH hs MATCH (et:ExerciseType {name: $exercise}) MERGE (hs)-[:HAD_EXERCISE]->(et)"
        params["exercise"] = exercise
    if diet:
        query += " WITH hs MATCH (dq:DietQuality {type: $diet}) MERGE (hs)-[:HAD_DIET]->(dq)"
        params["diet"] = diet

    # Delete-then-create in one transaction — see _write_emotion.
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (d:Day {date: $day})-[:HAD_HEALTH]->(old:HealthState)
            DETACH DELETE old
        """, day=day)
        tx.run(query, params)
        tx.commit()


def _write_event(session, day: str, event, topics_by_event: dict, goals_by_event: dict) -> None:
    cid = _canonical_id(event["title"])
    tags = [t.strip() for t in event["tags"].split(",") if t.strip()] if event["tags"] else []

    session.run("""
        MERGE (e:Event {canonical_id: $cid})
        SET e.title       = $title,
            e.event_type  = $event_type,
            e.description = $description,
            e.tags        = $tags
        WITH e
        MATCH (d:Day {date: $day})
        MERGE (d)-[:HAD_EVENT]->(e)
    """,
        cid=cid, title=event["title"], event_type=event["event_type"],
        description=event["description"] or "", tags=tags, day=day,
    )

    for topic in topics_by_event.get(event["title"], []):
        session.run("""
            MERGE (t:Topic {name: $name})
            WITH t
            MATCH (e:Event {canonical_id: $cid})
            MERGE (e)-[:INVOLVES]->(t)
        """, name=topic.lower().strip(), cid=cid)

    for goal_name in goals_by_event.get(event["title"], []):
        # Strict MATCH: only active goals accumulate new CONTRIBUTES_TO edges.
        # Fulfilled goals retain their existing edges as historical record but
        # do not gain new ones. If the goal is missing or not active here,
        # the MATCH yields no rows and no edge is created.
        session.run("""
            MATCH (g:Goal {name: $name})
            WHERE g.status = 'active'
            WITH g
            MATCH (e:Event {canonical_id: $cid})
            MERGE (e)-[:CONTRIBUTES_TO]->(g)
        """, name=goal_name, cid=cid)
