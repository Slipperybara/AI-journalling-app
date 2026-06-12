"""Write pipeline: Postgres extraction rows → Neo4j graph for one (user, day-bucket).

Phase 2 multi-tenant: every domain + reference node carries `user_id`. Every
MERGE keys on `(user_id, ...)` so two users with same-named events / goals /
topics stay isolated. Per-user reference nodes are seeded lazily on the
first write_day call for a user.
"""
import hashlib
from datetime import date, timedelta
from uuid import UUID

from .db import connect
from .graph_db import graph_connect, seed_reference_nodes_for_user


def _canonical_id(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def write_day(day: str, user_id: UUID) -> dict:
    """Batch entrypoint: project one (user, day)'s Postgres extractions into
    Neo4j and ensure the chain links yesterday→day. Idempotent."""
    seed_reference_nodes_for_user(user_id)

    with connect() as conn:
        log_row = conn.execute(
            "SELECT status FROM parse_log WHERE user_id = %s AND day = %s",
            (str(user_id), day),
        ).fetchone()

    if not log_row or log_row["status"] != "succeeded":
        return {"status": "skipped", "reason": "parse_log not succeeded", "day": day}

    sync_result = sync_day_to_graph(day, user_id)
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    ensure_day_chain(prev, day, user_id)
    return {"status": "ok", **sync_result}


def sync_day_to_graph(day: str, user_id: UUID) -> dict:
    """Idempotent projection of one (user, day)'s Postgres state into Neo4j.

    Reads emotion/health/productivity/events/event_topics/event_goal_contributions
    plus the user's active+fulfilled goals, then projects them. Safe to call
    repeatedly.
    """
    uid = str(user_id)
    with connect() as conn:
        emotion = conn.execute(
            "SELECT * FROM emotional_analysis WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchone()
        health = conn.execute(
            "SELECT * FROM health_metrics WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchone()
        productivity = conn.execute(
            "SELECT * FROM productivity_metrics WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchone()
        events = conn.execute(
            "SELECT * FROM events WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchall()
        topic_rows = conn.execute(
            "SELECT event_title, topic FROM event_topics WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchall()
        goal_rows = conn.execute(
            "SELECT event_title, goal_name FROM event_goal_contributions WHERE user_id = %s AND day = %s",
            (uid, day),
        ).fetchall()
        goals = conn.execute(
            "SELECT name, discovered_on, status, fulfilled_at FROM goals "
            "WHERE user_id = %s AND status IN ('active','fulfilled')",
            (uid,),
        ).fetchall()

    topics_by_event: dict[str, list[str]] = {}
    for r in topic_rows:
        topics_by_event.setdefault(r["event_title"], []).append(r["topic"])

    goals_by_event: dict[str, list[str]] = {}
    for r in goal_rows:
        goals_by_event.setdefault(r["event_title"], []).append(r["goal_name"])

    with graph_connect() as session:
        _write_day_node(session, day, productivity, user_id)
        _write_goals(session, goals, user_id)
        if emotion:
            _write_emotion(session, day, emotion, user_id)
        if health:
            _write_health(session, day, health, user_id)
        for event in events:
            _write_event(session, day, event, topics_by_event, goals_by_event, user_id)

    return {"day": day, "user_id": uid, "events": len(events)}


def ensure_day_chain(start_day: str, end_day: str, user_id: UUID) -> dict:
    """Walk [start_day, end_day] inclusive. MERGE a Day node for the user for
    every date in the range and a NEXT_DAY edge between each consecutive pair.
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

    uid = str(user_id)
    with graph_connect() as session:
        for d in days:
            session.run(
                "MERGE (:Day {user_id: $user_id, date: $date})",
                user_id=uid, date=d,
            )
        for prev, nxt in zip(days, days[1:]):
            session.run(
                """
                MATCH (a:Day {user_id: $user_id, date: $a})
                MATCH (b:Day {user_id: $user_id, date: $b})
                MERGE (a)-[:NEXT_DAY]->(b)
                """,
                user_id=uid, a=prev, b=nxt,
            )

    return {"nodes_ensured": len(days), "edges_ensured": max(0, len(days) - 1)}


def _write_goals(session, goals, user_id: UUID) -> None:
    """MERGE each goal node, projecting current Postgres status. Only called
    with rows whose status is active or fulfilled — candidates and removed
    goals never appear in Neo4j."""
    uid = str(user_id)
    for row in goals:
        session.run("""
            MERGE (g:Goal {user_id: $user_id, name: $name})
            ON CREATE SET g.discovered_on = $discovered_on
            SET g.status = $status,
                g.fulfilled_at = $fulfilled_at
        """,
            user_id=uid,
            name=row["name"],
            status=row["status"],
            fulfilled_at=row["fulfilled_at"],
            discovered_on=row["discovered_on"],
        )


def _write_day_node(session, day: str, productivity, user_id: UUID) -> None:
    session.run("""
        MERGE (d:Day {user_id: $user_id, date: $date})
        SET d.deep_work_hours      = $deep_work_hours,
            d.shallow_work_hours   = $shallow_work_hours,
            d.time_block_adherence = $time_block_adherence,
            d.cognitive_load       = $cognitive_load,
            d.friction_points      = $friction_points
    """,
        user_id=str(user_id),
        date=day,
        deep_work_hours=productivity["deep_work_hours"] if productivity else None,
        shallow_work_hours=productivity["shallow_work_hours"] if productivity else None,
        time_block_adherence=productivity["time_block_adherence"] if productivity else None,
        cognitive_load=productivity["cognitive_load"] if productivity else None,
        friction_points=(productivity["friction_points"] or []) if productivity else [],
    )


def _write_emotion(session, day: str, emotion, user_id: UUID) -> None:
    uid = str(user_id)
    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (d:Day {user_id: $user_id, date: $day})-[:HAD_EMOTION]->(old:EmotionState {user_id: $user_id})
            DETACH DELETE old
        """, user_id=uid, day=day)
        tx.run("""
            MATCH (d:Day {user_id: $user_id, date: $day})
            MATCH (q:EmotionQuadrant {user_id: $user_id, name: $quadrant})
            CREATE (es:EmotionState {
                user_id:              $user_id,
                valence:              $valence,
                arousal:              $arousal,
                cognitive_labels:     $labels,
                cognitive_triggers:   $triggers,
                social_interactions:  $social
            })
            MERGE (d)-[:HAD_EMOTION]->(es)
            MERGE (es)-[:IN_QUADRANT]->(q)
        """,
            user_id=uid,
            day=day,
            quadrant=emotion["primary_quadrant"],
            valence=emotion["valence"],
            arousal=emotion["arousal"],
            labels=(emotion["cognitive_labels"] or []),
            triggers=(emotion["cognitive_triggers"] or []),
            social=(emotion["social_interactions"] or []),
        )
        tx.commit()


def _write_health(session, day: str, health, user_id: UUID) -> None:
    uid = str(user_id)
    sleep = health["sleep_quality"]
    exercise = health["exercise_type"]
    diet = health["diet_quality"]

    query = """
        MATCH (d:Day {user_id: $user_id, date: $day})
        CREATE (hs:HealthState {
            user_id:              $user_id,
            somatic_sensations:   $somatic,
            physical_performance: $performance,
            supplements:          $supplements
        })
        MERGE (d)-[:HAD_HEALTH]->(hs)
    """
    params = dict(
        user_id=uid,
        day=day,
        somatic=(health["somatic_sensations"] or []),
        performance=health["physical_performance"],
        supplements=(health["supplements"] or []),
    )

    if sleep:
        query += " WITH hs MATCH (sq:SleepQuality {user_id: $user_id, level: $sleep}) MERGE (hs)-[:HAD_SLEEP]->(sq)"
        params["sleep"] = sleep
    if exercise:
        query += " WITH hs MATCH (et:ExerciseType {user_id: $user_id, name: $exercise}) MERGE (hs)-[:HAD_EXERCISE]->(et)"
        params["exercise"] = exercise
    if diet:
        query += " WITH hs MATCH (dq:DietQuality {user_id: $user_id, type: $diet}) MERGE (hs)-[:HAD_DIET]->(dq)"
        params["diet"] = diet

    with session.begin_transaction() as tx:
        tx.run("""
            MATCH (d:Day {user_id: $user_id, date: $day})-[:HAD_HEALTH]->(old:HealthState {user_id: $user_id})
            DETACH DELETE old
        """, user_id=uid, day=day)
        tx.run(query, params)
        tx.commit()


def _write_event(session, day: str, event, topics_by_event: dict, goals_by_event: dict, user_id: UUID) -> None:
    uid = str(user_id)
    cid = _canonical_id(event["title"])
    tags_field = event["tags"]
    if isinstance(tags_field, str):
        tags = [t.strip() for t in tags_field.split(",") if t.strip()]
    elif isinstance(tags_field, list):
        tags = [t for t in tags_field if t]
    else:
        tags = []

    session.run("""
        MERGE (e:Event {user_id: $user_id, canonical_id: $cid})
        SET e.title       = $title,
            e.event_type  = $event_type,
            e.description = $description,
            e.tags        = $tags
        WITH e
        MATCH (d:Day {user_id: $user_id, date: $day})
        MERGE (d)-[:HAD_EVENT]->(e)
    """,
        user_id=uid, cid=cid, title=event["title"], event_type=event["event_type"],
        description=event["description"] or "", tags=tags, day=day,
    )

    # Topic links come from two LLM-extracted sources, unioned: the conceptual
    # `topics` field (event_topics rows — precise but only populated for events
    # with a "clear intellectual or skill domain") and the near-universal `tags`
    # field. Sourcing from tags too ensures lifestyle/health/social events still
    # get Topic nodes instead of floating disconnected. MERGE keeps it idempotent
    # and dedups overlap; graph_maintenance later categorises every Topic.
    topic_names = {t.lower().strip() for t in tags if t.strip()}
    topic_names.update(
        t.lower().strip() for t in topics_by_event.get(event["title"], []) if t.strip()
    )
    for name in topic_names:
        session.run("""
            MERGE (t:Topic {user_id: $user_id, name: $name})
            WITH t
            MATCH (e:Event {user_id: $user_id, canonical_id: $cid})
            MERGE (e)-[:INVOLVES]->(t)
        """, user_id=uid, name=name, cid=cid)

    for goal_name in goals_by_event.get(event["title"], []):
        # Strict MATCH: only this user's active goals accumulate new
        # CONTRIBUTES_TO edges. Cross-user collisions are impossible because
        # both the goal and event MATCHes filter by user_id.
        session.run("""
            MATCH (g:Goal {user_id: $user_id, name: $name})
            WHERE g.status = 'active'
            WITH g
            MATCH (e:Event {user_id: $user_id, canonical_id: $cid})
            MERGE (e)-[:CONTRIBUTES_TO]->(g)
        """, user_id=uid, name=goal_name, cid=cid)
