# Neo4j Write Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Neo4j as a graph layer that is populated nightly from SQLite extraction data, enabling the LangGraph read pipeline (Plan 2) to query rich relational history.

**Architecture:** A new `graph_batch.write_day(day)` function reads from SQLite extraction tables after each successful `parse_day()` call and writes Day/Emotion/Health/Event/Topic/Goal nodes to Neo4j. A maintenance pipeline runs after each write to deduplicate Event nodes, normalise Topics, and assign Category hierarchy. The existing SQLite tables and dashboard endpoint are completely unchanged.

**Tech Stack:** Neo4j 5 (Docker + APOC plugin), `neo4j` Python driver, `pytest` for tests. No ORM, no async — all Neo4j calls use the sync driver session to stay consistent with existing BackgroundTask conventions.

**Note:** This is Plan 1 of 2. Plan 2 (LangGraph Read Pipeline) wires the analytical query path and depends on Neo4j being populated by this plan.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `docker-compose.yml` | Neo4j + APOC container definition |
| Create | `app/graph_db.py` | Driver init, `graph_connect()` context manager, `init_graph()` reference-node seeding |
| Create | `app/graph_schema.py` | Ontology constants (`EMOTION_QUADRANTS` etc.) + `ONTOLOGY_SCHEMA` string for LLM prompts |
| Create | `app/graph_batch.py` | `write_day(day)` — SQLite → Neo4j write pipeline |
| Create | `app/graph_maintenance.py` | `run()` — 3-pass APOC deduplication + Topic category assignment |
| Create | `tests/test_graph_write_pipeline.py` | Integration tests (require Neo4j running) |
| Modify | `app/core.py` | Add `neo4j_uri`, `neo4j_user`, `neo4j_password` settings |
| Modify | `app/models.py` | Add `topics`, `contributes_to_goals` to `EventItem`; add `discovered_goals` to `JournalParserResponse` |
| Modify | `app/db.py` | Add 3 new tables + expand `EXTRACTION_TABLES` |
| Modify | `app/extractions.py` | Write to `event_topics`, `event_goal_contributions`, `goals` |
| Modify | `app/parser.py` | Fetch existing goals from SQLite and inject into `parse_day_content()` system prompt |
| Modify | `app/batch.py` | Call `graph_batch.write_day(yesterday)` + `graph_maintenance.run()` after successful `parse_day()` |
| Modify | `main.py` | Call `graph_db.init_graph()` at startup to seed reference nodes |

---

## Task 1: Docker + Neo4j Setup

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create docker-compose.yml**

```yaml
services:
  neo4j:
    image: neo4j:latest
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/mindforge
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - neo4j_data:/data
volumes:
  neo4j_data:
```

- [ ] **Step 2: Start Neo4j and verify**

```bash
docker compose up -d
```

Wait ~15 seconds for Neo4j to initialise, then open `http://localhost:7474` in a browser. Log in with username `neo4j`, password `mindforge`. You should see the Neo4j Browser with an empty database. Run `:server status` — it should say "Connected".

- [ ] **Step 3: Verify APOC is loaded**

In the Neo4j Browser, run:
```cypher
RETURN apoc.version()
```
Expected: a version string like `"5.x.x"`. If it errors, Neo4j hasn't finished loading — wait 10 more seconds and retry.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "infra: add Neo4j + APOC docker-compose"
```

---

## Task 2: Neo4j Settings + Connection Module

**Files:**
- Modify: `app/core.py`
- Create: `app/graph_db.py`

- [ ] **Step 1: Add Neo4j settings to `app/core.py`**

The current file ends at line 16. Add three new fields to `Settings`:

```python
from openai import OpenAI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str
    day_boundary_hour: int = 6
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "mindforge"

    class Config:
        env_file = ".env"


settings = Settings()
client = OpenAI(api_key=settings.openai_api_key)

DB_NAME = "journal.db"
```

- [ ] **Step 2: Install the Neo4j Python driver**

```bash
pip install neo4j
```

- [ ] **Step 3: Create `app/graph_db.py`**

```python
"""Neo4j driver lifecycle and connection context manager."""
from contextlib import contextmanager

from neo4j import GraphDatabase

from .core import settings

_driver = None


def _get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


@contextmanager
def graph_connect():
    """Yield a Neo4j session. Mirrors the db.connect() pattern."""
    with _get_driver().session() as session:
        yield session


def close():
    """Call on app shutdown to release driver resources."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
```

- [ ] **Step 4: Verify the connection works**

Start the FastAPI backend (`python main.py`) in one terminal, then in another:

```python
# Run from repo root: python -c "..."
from app.graph_db import graph_connect
with graph_connect() as s:
    result = s.run("RETURN 1 AS n").single()
    print(result["n"])  # Expected: 1
```

- [ ] **Step 5: Commit**

```bash
git add app/core.py app/graph_db.py
git commit -m "feat: add Neo4j settings and graph_connect() context manager"
```

---

## Task 3: Graph Schema Constants + Reference Node Seeding

**Files:**
- Create: `app/graph_schema.py`
- Modify: `app/graph_db.py` (add `init_graph()`)
- Modify: `main.py` (call `init_graph()` at startup)

- [ ] **Step 1: Create `app/graph_schema.py`**

```python
"""Graph ontology constants. ONTOLOGY_SCHEMA is injected into LLM prompts."""

EMOTION_QUADRANTS = [
    "Peak Performance",
    "High-Stress",
    "Low-Energy",
    "Recovery & Clarity",
]

SLEEP_QUALITIES = ["Poor", "Fair", "Good", "Excellent"]

EXERCISE_TYPES = [
    "Light Cardio",
    "Heavy Cardio",
    "Light Strength",
    "Heavy Strength",
    "None",
]

DIET_QUALITIES = [
    "Clean",
    "Junk/Heavy",
    "Carbs Centered",
    "Meat and Vegetable centered",
]

ONTOLOGY_SCHEMA = """
Graph Ontology — ONLY use these labels and relationship types.

Node Labels and key properties:
- Day: date (string YYYY-MM-DD, primary key), deep_work_hours (float), shallow_work_hours (float), time_block_adherence (string: High|Medium|Low), cognitive_load (string: High|Medium|Low), friction_points (list of strings)
- EmotionState: valence (float -1 to 1), arousal (float -1 to 1), cognitive_labels (list), cognitive_triggers (list), social_interactions (list)
- EmotionQuadrant: name (string: Peak Performance|High-Stress|Low-Energy|Recovery & Clarity)
- HealthState: somatic_sensations (list), physical_performance (string), supplements (list)
- SleepQuality: level (string: Poor|Fair|Good|Excellent)
- ExerciseType: name (string: Light Cardio|Heavy Cardio|Light Strength|Heavy Strength|None)
- DietQuality: type (string: Clean|Junk/Heavy|Carbs Centered|Meat and Vegetable centered)
- Event: canonical_id (string), title (string), event_type (string: idea|location|milestone|media), description (string), tags (list)
- Topic: name (string, normalised lowercase)
- Category: name (string)
- Goal: name (string), discovered_on (string YYYY-MM-DD)

Relationship Types:
- (Day)-[:NEXT_DAY]->(Day)
- (Day)-[:HAD_EMOTION]->(EmotionState)
- (EmotionState)-[:IN_QUADRANT]->(EmotionQuadrant)
- (Day)-[:HAD_HEALTH]->(HealthState)
- (HealthState)-[:HAD_SLEEP]->(SleepQuality)
- (HealthState)-[:HAD_EXERCISE]->(ExerciseType)
- (HealthState)-[:HAD_DIET]->(DietQuality)
- (Day)-[:HAD_EVENT]->(Event)
- (Event)-[:INVOLVES]->(Topic)
- (Topic)-[:BELONGS_TO]->(Category)
- (Event)-[:CONTRIBUTES_TO]->(Goal)
"""
```

- [ ] **Step 2: Add `init_graph()` to `app/graph_db.py`**

Append this function to the bottom of the file:

```python
def init_graph() -> None:
    """Seed fixed reference nodes. Safe to call multiple times (all MERGE)."""
    from .graph_schema import (
        DIET_QUALITIES, EMOTION_QUADRANTS, EXERCISE_TYPES, SLEEP_QUALITIES
    )
    with graph_connect() as session:
        for name in EMOTION_QUADRANTS:
            session.run("MERGE (:EmotionQuadrant {name: $name})", name=name)
        for level in SLEEP_QUALITIES:
            session.run("MERGE (:SleepQuality {level: $level})", level=level)
        for name in EXERCISE_TYPES:
            session.run("MERGE (:ExerciseType {name: $name})", name=name)
        for type_ in DIET_QUALITIES:
            session.run("MERGE (:DietQuality {type: $type})", type=type_)
    print("[graph_db] reference nodes initialised")
```

- [ ] **Step 3: Call `init_graph()` and `close()` in `main.py`**

Replace the full `main.py` with:

```python
"""MindForge AI — FastAPI entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import scheduler
from app.db import init_db
from app.graph_db import close as graph_close, init_graph
from app.routers import admin, conversations, dashboard, messages, todos


app = FastAPI(title="MindForge AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()
init_graph()

app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(todos.router)


@app.on_event("startup")
def _startup() -> None:
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()
    graph_close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
```

- [ ] **Step 4: Verify reference nodes were created**

Restart the backend (`python main.py`), then in the Neo4j Browser run:

```cypher
MATCH (n) RETURN labels(n)[0] AS label, count(n) AS count
```

Expected result:
| label | count |
|-------|-------|
| EmotionQuadrant | 4 |
| SleepQuality | 4 |
| ExerciseType | 5 |
| DietQuality | 4 |

- [ ] **Step 5: Commit**

```bash
git add app/graph_schema.py app/graph_db.py main.py
git commit -m "feat: graph schema constants and reference node seeding"
```

---

## Task 4: SQLite Schema Extensions

**Files:**
- Modify: `app/db.py`

The existing `EXTRACTION_TABLES` tuple and `init_db()` need three new tables. The new tables also need to be included in `_delete_existing_rows` in `batch.py` so re-parses stay idempotent.

- [ ] **Step 1: Expand `EXTRACTION_TABLES` in `app/db.py`**

Find line 43:
```python
EXTRACTION_TABLES = (
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "todos",
)
```

Replace with:
```python
EXTRACTION_TABLES = (
    "emotional_analysis",
    "health_metrics",
    "productivity_metrics",
    "events",
    "todos",
    "event_topics",
    "event_goal_contributions",
)
```

Note: `goals` is intentionally excluded from `EXTRACTION_TABLES` — goals accumulate over time and should never be deleted on re-parse. Only the day's event→goal mappings are re-inserted.

- [ ] **Step 2: Add three new tables at the end of `init_db()` in `app/db.py`**

Append inside the `with connect() as conn:` block, after the existing todo migration (after line 169):

```python
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                topic TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_goal_contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                event_title TEXT NOT NULL,
                goal_name TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS goals (
                name TEXT PRIMARY KEY,
                discovered_on TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
```

- [ ] **Step 3: Verify tables are created**

```bash
python -c "from app.db import init_db; init_db(); import sqlite3; conn = sqlite3.connect('journal.db'); print([r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])"
```

Expected output includes: `event_topics`, `event_goal_contributions`, `goals`

- [ ] **Step 4: Commit**

```bash
git add app/db.py
git commit -m "feat: add event_topics, event_goal_contributions, goals SQLite tables"
```

---

## Task 5: Model Extensions

**Files:**
- Modify: `app/models.py`

- [ ] **Step 1: Extend `EventItem` with two new fields**

Find the `EventItem` class (line 24) and replace it:

```python
class EventItem(BaseModel):
    title: str = Field(description="Short title for this event.")
    description: str = Field(description="One or two sentence elaboration grounded in what the user said.")
    tags: str = Field(description="Comma-separated tags. May be empty string.")
    event_type: str = Field(description="Must be one of: idea, location, milestone, media")
    topics: List[str] = Field(
        default_factory=list,
        description=(
            "1-3 specific conceptual topic tags for this event. "
            "Use precise terms, e.g. ['LLMs', 'RAG'], ['Algorithm Practice'], ['System Design']. "
            "Empty list if the event has no clear intellectual or skill domain."
        ),
    )
    contributes_to_goals: List[str] = Field(
        default_factory=list,
        description=(
            "Names of tracked goals this event directly contributes toward. "
            "Only include names that exactly match a goal from the provided goals list. "
            "Empty list if none match."
        ),
    )
```

- [ ] **Step 2: Add `discovered_goals` to `JournalParserResponse`**

Find the `JournalParserResponse` class (line 57) and replace it:

```python
class JournalParserResponse(BaseModel):
    todos: List[TodoItem]
    events: List[EventItem]
    emotions: EmotionalAnalysis
    health: HealthMetrics
    productivity: ProductivityMetrics
    discovered_goals: List[str] = Field(
        default_factory=list,
        description=(
            "New long-term goals the user explicitly stated today, "
            "e.g. 'Jane Street Prep', 'OGP Interview'. "
            "Only extract goals the user clearly named as objectives. "
            "Do NOT include todos or one-off tasks here."
        ),
    )
```

- [ ] **Step 3: Verify Pydantic still validates**

```bash
python -c "
from app.models import JournalParserResponse, EventItem, EmotionalAnalysis, HealthMetrics, ProductivityMetrics
e = EventItem(title='Test', description='desc', tags='', event_type='idea', topics=['LLMs'], contributes_to_goals=[])
r = JournalParserResponse(todos=[], events=[e], emotions=EmotionalAnalysis(valence=0, arousal=0, primary_quadrant='Recovery & Clarity', cognitive_labels=[], cognitive_triggers=[], social_interactions=[]), health=HealthMetrics(somatic_sensations=[], supplements=[]), productivity=ProductivityMetrics(friction_points=[]), discovered_goals=['Jane Street Prep'])
print('ok', r.discovered_goals)
"
```

Expected: `ok ['Jane Street Prep']`

- [ ] **Step 4: Commit**

```bash
git add app/models.py
git commit -m "feat: add topics/contributes_to_goals on EventItem and discovered_goals on JournalParserResponse"
```

---

## Task 6: Extractions Extension

**Files:**
- Modify: `app/extractions.py`

Add writes for the three new tables at the end of `store_extractions()`. The deletion of old rows is handled by `batch._delete_existing_rows()` which now includes `event_topics` and `event_goal_contributions` (from Task 4). Goals use `INSERT OR IGNORE` because they accumulate.

- [ ] **Step 1: Add new writes to `store_extractions()` in `app/extractions.py`**

Append inside the `with connect() as conn:` block, after the existing `for t in parsed.todos:` loop (after line 66):

```python
        for goal_name in (parsed.discovered_goals or []):
            if goal_name.strip():
                cursor.execute(
                    "INSERT OR IGNORE INTO goals (name, discovered_on) VALUES (?, ?)",
                    (goal_name.strip(), day),
                )

        for ev in parsed.events:
            for topic in (ev.topics or []):
                if topic.strip():
                    cursor.execute(
                        "INSERT INTO event_topics (day, event_title, topic) VALUES (?, ?, ?)",
                        (day, ev.title, topic.strip()),
                    )
            for goal_name in (ev.contributes_to_goals or []):
                if goal_name.strip():
                    cursor.execute(
                        "INSERT INTO event_goal_contributions (day, event_title, goal_name) VALUES (?, ?, ?)",
                        (day, ev.title, goal_name.strip()),
                    )
```

- [ ] **Step 2: Verify with a dry run**

```python
# python -c "..."
from app.models import JournalParserResponse, EventItem, EmotionalAnalysis, HealthMetrics, ProductivityMetrics
from app.db import init_db
from app.extractions import store_extractions
import sqlite3

init_db()

parsed = JournalParserResponse(
    todos=[],
    events=[EventItem(title='DataTales pipeline', description='worked on it', tags='', event_type='milestone', topics=['RAG', 'LLMs'], contributes_to_goals=['DataTales MVP'])],
    emotions=EmotionalAnalysis(valence=0.5, arousal=0.5, primary_quadrant='Peak Performance', cognitive_labels=['motivated'], cognitive_triggers=[], social_interactions=[]),
    health=HealthMetrics(somatic_sensations=[], supplements=[]),
    productivity=ProductivityMetrics(friction_points=[]),
    discovered_goals=['DataTales MVP'],
)

store_extractions(parsed, day='2026-05-28')

conn = sqlite3.connect('journal.db')
print('topics:', conn.execute("SELECT * FROM event_topics WHERE day='2026-05-28'").fetchall())
print('goal_contrib:', conn.execute("SELECT * FROM event_goal_contributions WHERE day='2026-05-28'").fetchall())
print('goals:', conn.execute("SELECT * FROM goals").fetchall())
```

Expected: Three rows printed — two in event_topics (RAG + LLMs), one in event_goal_contributions (DataTales MVP), one in goals.

- [ ] **Step 3: Clean up the test rows**

```bash
python -c "import sqlite3; conn = sqlite3.connect('journal.db'); conn.execute(\"DELETE FROM event_topics WHERE day='2026-05-28'\"); conn.execute(\"DELETE FROM event_goal_contributions WHERE day='2026-05-28'\"); conn.execute(\"DELETE FROM goals\"); conn.commit()"
```

- [ ] **Step 4: Commit**

```bash
git add app/extractions.py
git commit -m "feat: store event_topics, event_goal_contributions, goals in store_extractions()"
```

---

## Task 7: Parser Goal Injection

**Files:**
- Modify: `app/parser.py`

The batch LLM needs to know which goals exist so it can fill `contributes_to_goals` on each event and avoid re-adding goals that already exist. Inject a list of current goal names into the system prompt.

- [ ] **Step 1: Modify `parse_day_content()` in `app/parser.py`**

Replace the current `parse_day_content` function (lines 40-49) with:

```python
def parse_day_content(content: str) -> JournalParserResponse:
    from .db import connect as db_connect
    with db_connect() as conn:
        rows = conn.execute("SELECT name FROM goals ORDER BY name").fetchall()
    existing_goals = [r["name"] for r in rows]

    goals_addendum = ""
    if existing_goals:
        goals_addendum = (
            f"\n\nCurrently tracked goals: {', '.join(existing_goals)}. "
            "When filling contributes_to_goals on each event, only use names from this list exactly as written. "
            "Add new goal names to discovered_goals only if the user explicitly states a new objective today."
        )

    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": PARSER_SYSTEM_BATCH + goals_addendum},
            {"role": "user", "content": content},
        ],
        response_format=JournalParserResponse,
    )
    return completion.choices[0].message.parsed
```

- [ ] **Step 2: Verify the import chain is intact**

```bash
python -c "from app.parser import parse_day_content; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 3: Commit**

```bash
git add app/parser.py
git commit -m "feat: inject existing goals into batch parser system prompt"
```

---

## Task 8: Write Pipeline (`app/graph_batch.py`)

**Files:**
- Create: `app/graph_batch.py`

This is the core of Plan 1. `write_day(day)` reads from all SQLite extraction tables for a given day and writes/merges the corresponding Neo4j graph structure.

- [ ] **Step 1: Create `app/graph_batch.py`**

```python
"""Write pipeline: SQLite extraction rows → Neo4j graph for one day-bucket."""
import hashlib
import json
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

    topics_by_event: dict[str, list[str]] = {}
    for r in topic_rows:
        topics_by_event.setdefault(r["event_title"], []).append(r["topic"])

    goals_by_event: dict[str, list[str]] = {}
    for r in goal_rows:
        goals_by_event.setdefault(r["event_title"], []).append(r["goal_name"])

    with graph_connect() as session:
        _write_day_node(session, day, productivity)
        _write_next_day_chain(session, day)
        if emotion:
            _write_emotion(session, day, emotion)
        if health:
            _write_health(session, day, health)
        for event in events:
            _write_event(session, day, event, topics_by_event, goals_by_event)

    return {"status": "ok", "day": day, "events": len(events)}


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
    session.run("""
        MATCH (d:Day {date: $day})-[:HAD_EMOTION]->(old:EmotionState)
        DETACH DELETE old
    """, day=day)
    session.run("""
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


def _write_health(session, day: str, health) -> None:
    session.run("""
        MATCH (d:Day {date: $day})-[:HAD_HEALTH]->(old:HealthState)
        DETACH DELETE old
    """, day=day)

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

    session.run(query, params)  # pass as positional dict — neo4j driver param convention


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
        session.run("""
            MERGE (g:Goal {name: $name})
            WITH g
            MATCH (e:Event {canonical_id: $cid})
            MERGE (e)-[:CONTRIBUTES_TO]->(g)
        """, name=goal_name, cid=cid)
```

- [ ] **Step 2: Verify `write_day` can be imported**

```bash
python -c "from app.graph_batch import write_day; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 3: Run `write_day` on a real day**

If you have data in SQLite, pick a day that shows `succeeded` in `parse_log` and run:

```bash
python -c "
from app.graph_batch import write_day
result = write_day('2026-05-27')   # replace with an actual succeeded day
print(result)
"
```

Expected: `{'status': 'ok', 'day': '2026-05-27', 'events': N}` where N is the event count.

- [ ] **Step 4: Verify the graph in Neo4j Browser**

```cypher
MATCH (d:Day {date: '2026-05-27'})
OPTIONAL MATCH (d)-[:HAD_EMOTION]->(es)-[:IN_QUADRANT]->(q)
OPTIONAL MATCH (d)-[:HAD_EVENT]->(e)
RETURN d.date, q.name, collect(e.title)
```

Expected: your day's date, quadrant name, and event titles.

- [ ] **Step 5: Commit**

```bash
git add app/graph_batch.py
git commit -m "feat: write_day() SQLite-to-Neo4j write pipeline"
```

---

## Task 9: Maintenance Pipeline (`app/graph_maintenance.py`)

**Files:**
- Create: `app/graph_maintenance.py`

Three passes: Event dedup (Levenshtein < 3), Topic dedup + gpt-4o Category assignment (Levenshtein < 2), Goal dedup (Levenshtein < 2, keep longest name).

- [ ] **Step 1: Create `app/graph_maintenance.py`**

```python
"""Post-write maintenance: deduplication and topic category hierarchy."""
import json

from .core import client
from .graph_db import graph_connect


def run() -> dict:
    """Run all three maintenance passes. Safe to call multiple times."""
    events_merged = _deduplicate_events()
    topics_merged = _deduplicate_and_categorise_topics()
    goals_merged = _deduplicate_goals()
    return {"events_merged": events_merged, "topics_merged": topics_merged, "goals_merged": goals_merged}


def _get_similar_pairs(session, label: str, prop: str, threshold: int) -> list[tuple]:
    result = session.run(f"""
        MATCH (a:{label}), (b:{label})
        WHERE id(a) < id(b)
          AND apoc.text.levenshteinDistance(a.{prop}, b.{prop}) < $threshold
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
```

- [ ] **Step 2: Verify import**

```bash
python -c "from app.graph_maintenance import run; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 3: Test Event deduplication manually**

In the Neo4j Browser, create two near-duplicate events:
```cypher
CREATE (:Event {title: 'DataTales', canonical_id: 'aaa', event_type: 'milestone', description: '', tags: []})
CREATE (:Event {title: 'Data Tales', canonical_id: 'bbb', event_type: 'milestone', description: '', tags: []})
```

Then run:
```bash
python -c "from app.graph_maintenance import run; print(run())"
```

Then in Neo4j Browser verify only one `Event` node with title `DataTales` remains:
```cypher
MATCH (e:Event) WHERE e.title CONTAINS 'ata' RETURN e.title
```

Expected: one row — `DataTales`.

- [ ] **Step 4: Commit**

```bash
git add app/graph_maintenance.py
git commit -m "feat: graph maintenance pipeline — event/topic/goal dedup + category hierarchy"
```

---

## Task 10: Wire Into Scheduled Batch

**Files:**
- Modify: `app/batch.py`

`run_scheduled_batch()` currently calls `parse_day(yesterday)` then `carryover_unfilled_todos()`. Add the graph write + maintenance after a successful parse.

- [ ] **Step 1: Add the graph pipeline call to `run_scheduled_batch()` in `app/batch.py`**

Add imports at the top of the file (after the existing imports):

```python
from . import graph_batch, graph_maintenance
```

Replace `run_scheduled_batch()` (lines 134-150) with:

```python
def run_scheduled_batch() -> None:
    """Cron entrypoint. Parses yesterday's bucket, writes to Neo4j, carries over todos."""
    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    today = current_bucket().isoformat()

    parse_ok = False
    try:
        parse_day(yesterday)
        print(f"[batch] scheduled parse complete for {yesterday}")
        parse_ok = True
    except Exception:
        print(f"[batch] scheduled parse failed for {yesterday}")
        traceback.print_exc()

    if parse_ok:
        try:
            result = graph_batch.write_day(yesterday)
            print(f"[batch] graph write: {result}")
            maint = graph_maintenance.run()
            print(f"[batch] maintenance: {maint}")
        except Exception:
            print(f"[batch] graph pipeline failed for {yesterday}")
            traceback.print_exc()

    try:
        n = carryover_unfilled_todos(yesterday, today)
        if n:
            print(f"[batch] carried over {n} unfilled todo(s) from {yesterday} to {today}")
    except Exception:
        print(f"[batch] carryover failed")
        traceback.print_exc()
```

- [ ] **Step 2: Verify the import resolves**

```bash
python -c "from app.batch import run_scheduled_batch; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 3: Trigger a manual end-to-end test via the admin endpoint**

Start the backend (`python main.py`), then run a manual parse for a day with data:

```bash
curl -X POST "http://127.0.0.1:8000/api/admin/parse-day/2026-05-27"
```

Then manually call the graph pipeline:

```bash
python -c "
from app.graph_batch import write_day
from app.graph_maintenance import run
print(write_day('2026-05-27'))
print(run())
"
```

In Neo4j Browser run the full graph check:
```cypher
MATCH path = (d:Day)-[:HAD_EMOTION]->(es)-[:IN_QUADRANT]->(q)
WHERE d.date = '2026-05-27'
RETURN path
```

Expected: a visual path from your Day node through EmotionState to the correct EmotionQuadrant.

- [ ] **Step 4: Commit**

```bash
git add app/batch.py
git commit -m "feat: wire graph_batch + graph_maintenance into run_scheduled_batch()"
```

---

## Task 11: Integration Test Suite

**Files:**
- Create: `tests/test_graph_write_pipeline.py`

These tests require Neo4j running (`docker compose up -d`). They test the full write→read→maintenance round-trip without hitting the OpenAI API (except the maintenance category step, which is marked to skip in CI).

- [ ] **Step 1: Install pytest**

```bash
pip install pytest
```

- [ ] **Step 2: Create `tests/__init__.py`**

```bash
touch tests/__init__.py
```

- [ ] **Step 3: Create `tests/test_graph_write_pipeline.py`**

```python
"""Integration tests for the Neo4j write pipeline. Requires docker compose up -d."""
import pytest
from app.db import init_db
from app.graph_db import graph_connect, init_graph


@pytest.fixture(autouse=True)
def setup_db():
    init_db()
    init_graph()


def _clear_test_day(session, day: str):
    session.run("MATCH (d:Day {date: $day}) DETACH DELETE d", day=day)


TEST_DAY = "1999-01-01"


def test_write_day_creates_day_node():
    """write_day skips if parse_log has no succeeded row — but we can call helpers directly."""
    from app.graph_batch import _write_day_node, _write_next_day_chain
    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None)
        result = session.run("MATCH (d:Day {date: $day}) RETURN d.date AS date", day=TEST_DAY).single()
    assert result["date"] == TEST_DAY


def test_write_emotion_creates_in_quadrant_edge():
    from app.graph_batch import _write_day_node, _write_emotion
    from unittest.mock import MagicMock

    emotion = MagicMock()
    emotion.__getitem__ = lambda self, key: {
        "primary_quadrant": "Peak Performance",
        "valence": 0.8,
        "arousal": 0.6,
        "cognitive_labels": '["motivated"]',
        "cognitive_triggers": "[]",
        "social_interactions": "[]",
    }[key]

    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None)
        _write_emotion(session, TEST_DAY, emotion)
        result = session.run("""
            MATCH (d:Day {date: $day})-[:HAD_EMOTION]->(es)-[:IN_QUADRANT]->(q)
            RETURN q.name AS quadrant
        """, day=TEST_DAY).single()

    assert result["quadrant"] == "Peak Performance"


def test_write_event_creates_involves_edge():
    from app.graph_batch import _write_day_node, _write_event
    from unittest.mock import MagicMock

    event = MagicMock()
    event.__getitem__ = lambda self, key: {
        "title": "Test Event",
        "event_type": "idea",
        "description": "a test event",
        "tags": "testing,pipeline",
    }[key]

    topics_by_event = {"Test Event": ["testing", "pipeline"]}
    goals_by_event = {}

    with graph_connect() as session:
        _clear_test_day(session, TEST_DAY)
        _write_day_node(session, TEST_DAY, None)
        _write_event(session, TEST_DAY, event, topics_by_event, goals_by_event)
        result = session.run("""
            MATCH (d:Day {date: $day})-[:HAD_EVENT]->(e)-[:INVOLVES]->(t)
            RETURN e.title AS event, collect(t.name) AS topics
        """, day=TEST_DAY).single()

    assert result["event"] == "Test Event"
    assert set(result["topics"]) == {"testing", "pipeline"}


def test_maintenance_merges_duplicate_events():
    from app.graph_maintenance import _deduplicate_events

    with graph_connect() as session:
        session.run("""
            CREATE (:Event {title: 'FooBar', canonical_id: 'test_foo', event_type: 'idea', description: '', tags: []})
            CREATE (:Event {title: 'FooBars', canonical_id: 'test_foos', event_type: 'idea', description: '', tags: []})
        """)

    _deduplicate_events()

    with graph_connect() as session:
        result = session.run(
            "MATCH (e:Event) WHERE e.title IN ['FooBar', 'FooBars'] RETURN count(e) AS n"
        ).single()
        session.run("MATCH (e:Event) WHERE e.title IN ['FooBar', 'FooBars'] DETACH DELETE e")

    assert result["n"] == 1
```

- [ ] **Step 4: Run the tests**

```bash
pytest tests/test_graph_write_pipeline.py -v
```

Expected:
```
tests/test_graph_write_pipeline.py::test_write_day_creates_day_node PASSED
tests/test_graph_write_pipeline.py::test_write_emotion_creates_in_quadrant_edge PASSED
tests/test_graph_write_pipeline.py::test_write_event_creates_involves_edge PASSED
tests/test_graph_write_pipeline.py::test_maintenance_merges_duplicate_events PASSED
```

- [ ] **Step 5: Commit**

```bash
git add tests/ 
git commit -m "test: integration tests for Neo4j write pipeline"
```

---

## Verification Checklist

Before declaring Plan 1 complete, confirm all of the following:

- [ ] `docker compose up -d` starts Neo4j and APOC loads (`RETURN apoc.version()` works in browser)
- [ ] `python main.py` starts without errors; Neo4j Browser shows 17 reference nodes
- [ ] `parse_day()` for any day with data writes rows to `event_topics` and `goals` tables
- [ ] `write_day()` for a succeeded day creates the expected graph structure visible in Neo4j Browser
- [ ] `graph_maintenance.run()` merges two near-duplicate Event nodes correctly
- [ ] `run_scheduled_batch()` calls the graph pipeline after a successful parse (check log output)
- [ ] `GET /api/dashboard` still returns correctly (no regression on SQLite path)
- [ ] `cd journal-frontend && npm run build && npm run lint` — no errors

---

## What's Next

**Plan 2: LangGraph Read Pipeline** (`docs/superpowers/plans/2026-05-28-langgraph-read-pipeline.md`)

Covers: `app/langgraph_flow.py`, `app/agents/cypher_agent.py`, `app/agents/synthesizer.py`, modifying `app/bot.py::process_message_background()` and `app/routers/messages.py` to route analytical queries through Neo4j.
