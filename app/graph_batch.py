"""Write pipeline: SQLite extraction rows → Neo4j graph for one day-bucket."""
import hashlib
from datetime import date, timedelta

from .db import connect, loads
from .graph_db import graph_connect


def _canonical_id(title: str) -> str:
    return hashlib.sha256(title.lower().strip().encode()).hexdigest()[:16]


def write_day(day: str) -> dict:
    """Write one day's extractions from SQLite into Neo4j. Idempotent."""
    with connect() as conn:
        log_row = conn.execute(
            "SELECT status FROM parse_log WHERE day = ?", (day,)
        ).fetchone()

    if not log_row or log_row["status"] != "succeeded":
        return {"status": "skipped", "reason": "parse_log not succeeded", "day": day}

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
            "SELECT name, discovered_on FROM goals"
        ).fetchall()

    topics_by_event: dict[str, list[str]] = {}
    for r in topic_rows:
        topics_by_event.setdefault(r["event_title"], []).append(r["topic"])

    goals_by_event: dict[str, list[str]] = {}
    for r in goal_rows:
        goals_by_event.setdefault(r["event_title"], []).append(r["goal_name"])

    with graph_connect() as session:
        _write_day_node(session, day, productivity)
        _write_next_day_chain(session, day)
        _write_goals(session, goals)
        if emotion:
            _write_emotion(session, day, emotion)
        if health:
            _write_health(session, day, health)
        for event in events:
            _write_event(session, day, event, topics_by_event, goals_by_event)

    return {"status": "ok", "day": day, "events": len(events)}


def _write_goals(session, goals) -> None:
    """MERGE each goal node; set discovered_on only on first create."""
    for row in goals:
        session.run("""
            MERGE (g:Goal {name: $name})
            ON CREATE SET g.discovered_on = $discovered_on
        """, name=row["name"], discovered_on=row["discovered_on"])


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


def _write_next_day_chain(session, day: str) -> None:
    prev = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    session.run("""
        MERGE (prev:Day {date: $prev})
        MERGE (d:Day {date: $day})
        MERGE (prev)-[:NEXT_DAY]->(d)
    """, prev=prev, day=day)


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
        # ON CREATE SET handles the edge case where an event references a goal
        # that wasn't pre-seeded by _write_goals — keeps schema invariant intact.
        session.run("""
            MERGE (g:Goal {name: $name})
            ON CREATE SET g.discovered_on = $day
            WITH g
            MATCH (e:Event {canonical_id: $cid})
            MERGE (e)-[:CONTRIBUTES_TO]->(g)
        """, name=goal_name, cid=cid, day=day)
