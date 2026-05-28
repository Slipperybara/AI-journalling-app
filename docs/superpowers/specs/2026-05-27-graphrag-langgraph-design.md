# MindForge: Hybrid SQLite + Neo4j GraphRAG with LangGraph

**Date:** 2026-05-27  
**Status:** Design approved, pending implementation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## Context

MindForge currently answers analytical questions ("What drives my highest productivity days?") through the same bot path as journaling updates — the bot assembles a flat SQLite context window (recent rows from extraction tables + transcript) and calls gpt-4o-mini. This works for recent history but cannot traverse relationships across time, detect patterns in events, or connect daily actions to long-term goals. The upgrade introduces Neo4j as a graph layer that stores relational structure, and LangGraph to route queries to the right path (journaling vs. graph analytics).

**Goal:** Enable queries like "Show momentum toward my Jane Street prep goal over the last 30 days" or "What conceptual topics correlate with my Peak Performance days?" without changing the journaling experience for non-analytical messages.

---

## Architecture Overview

### Unchanged
- All SQLite tables (`messages`, `conversations`, `emotional_analysis`, `health_metrics`, `productivity_metrics`, `events`, `todos`, `parse_log`)
- `app/db.py`, `app/extractions.py`, `app/routers/dashboard.py`
- The frontend (React SPA) — no changes needed; chat still polls the same endpoint

### New Infrastructure
- **Neo4j** — local Docker, neo4j:latest image with APOC plugin  
- **`neo4j` Python driver** — connection management via `app/graph_db.py`  
- **`langgraph`** Python package — orchestration via `app/langgraph_flow.py`

### New Modules (`app/`)
| File | Purpose |
|------|---------|
| `graph_db.py` | Neo4j driver init, `graph_connect()` context manager |
| `graph_schema.py` | Ontology constants and the schema string injected into LLM prompts |
| `graph_batch.py` | Write pipeline: SQLite extraction rows → Neo4j |
| `graph_maintenance.py` | APOC deduplication + topic hierarchy + goal dedup |
| `langgraph_flow.py` | LangGraph `StateGraph` (router + journaling path + analytical path) |
| `agents/cypher_agent.py` | Heavy DB Agent: Cypher generation + evaluation (gpt-4o) |
| `agents/synthesizer.py` | Synthesizer: format graph results into natural language (gpt-4o-mini) |

### Modified Existing Files
| File | Change |
|------|--------|
| `app/core.py` | Add `neo4j_uri`, `neo4j_user`, `neo4j_password` settings |
| `app/models.py` | Extend `EventItem` with `topics: List[str]` and `contributes_to_goals: List[str]`; add `discovered_goals: List[str]` to `JournalParserResponse` |
| `app/extractions.py` | Write `event_topics`, `event_goal_contributions`, and `goals` to three new SQLite tables |
| `app/db.py` | Add `CREATE TABLE IF NOT EXISTS event_topics` and `goals` to `init_db()` |
| `app/batch.py` | `run_scheduled_batch()` calls `graph_batch.write_day(yesterday)` after `parse_day()` succeeds |
| `app/bot.py` | `process_message_background(conversation_id, message_content)` — now delegates to LangGraph |
| `app/routers/messages.py` | Pass `message_content` to BackgroundTask alongside `conversation_id` |
| `docker-compose.yml` | New file: launches Neo4j + APOC |

---

## Graph Ontology

### Node Labels

| Label | Properties | Creation Logic |
|-------|-----------|----------------|
| `Day` | `date` (PK), `deep_work_hours`, `shallow_work_hours`, `time_block_adherence`, `cognitive_load`, `friction_points[]` | `MERGE` on `date` |
| `EmotionState` | `valence`, `arousal`, `cognitive_labels[]`, `cognitive_triggers[]`, `social_interactions[]` | `CREATE` fresh per batch (old deleted first) |
| `EmotionQuadrant` | `name` | `MERGE`; 4 fixed instances created at DB init |
| `HealthState` | `somatic_sensations[]`, `physical_performance`, `supplements[]` | `CREATE` fresh per batch; only if health data present |
| `SleepQuality` | `level` | `MERGE`; 4 fixed instances (Poor/Fair/Good/Excellent) |
| `ExerciseType` | `name` | `MERGE`; 5 fixed instances |
| `DietQuality` | `type` | `MERGE`; 4 fixed instances |
| `Event` | `canonical_id` (sha256 of lowercased title), `title`, `event_type`, `description`, `tags[]` | `MERGE` on `canonical_id` |
| `Topic` | `name` | `MERGE` on normalized (lowercased) name |
| `Category` | `name` | `MERGE`; created by maintenance pipeline |
| `Goal` | `name`, `discovered_on` | `MERGE` on `name`; created from batch extraction |

### Relationships

```
(Day)-[:NEXT_DAY]->(Day)                         # chronological chain
(Day)-[:HAD_EMOTION]->(EmotionState)             # always present after batch
(EmotionState)-[:IN_QUADRANT]->(EmotionQuadrant) # links to the 4 global quadrant nodes
(Day)-[:HAD_HEALTH]->(HealthState)               # optional; only if health data present
(HealthState)-[:HAD_SLEEP]->(SleepQuality)
(HealthState)-[:HAD_EXERCISE]->(ExerciseType)
(HealthState)-[:HAD_DIET]->(DietQuality)
(Day)-[:HAD_EVENT]->(Event)                      # 0..N per day
(Event)-[:INVOLVES]->(Topic)                     # concept tags per event
(Topic)-[:BELONGS_TO]->(Category)                # broad category, added by maintenance
(Event)-[:CONTRIBUTES_TO]->(Goal)                # goal linkage, added by batch
```

### Example Traversal Queries
```cypher
// Days linked to AI/ML work that had High-Stress emotion
MATCH (d:Day)-[:HAD_EVENT]->(e:Event)-[:INVOLVES]->(t:Topic)-[:BELONGS_TO]->(c:Category {name: 'AI/ML'}),
      (d)-[:HAD_EMOTION]->(em:EmotionState)-[:IN_QUADRANT]->(q:EmotionQuadrant {name: 'High-Stress'})
RETURN d.date, collect(e.title) ORDER BY d.date DESC LIMIT 14

// Goal momentum: count of contributing events per day for a goal
MATCH (e:Event)-[:CONTRIBUTES_TO]->(g:Goal {name: 'Jane Street Prep'}),
      (d:Day)-[:HAD_EVENT]->(e)
WHERE d.date >= date() - duration('P30D')
RETURN d.date, count(e) AS momentum ORDER BY d.date
```

---

## New SQLite Tables

```sql
CREATE TABLE IF NOT EXISTS event_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    event_title TEXT NOT NULL,
    topic TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_goal_contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    event_title TEXT NOT NULL,
    goal_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
    name TEXT PRIMARY KEY,
    discovered_on TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**Batch parser context change:** `parse_day_content()` in `app/parser.py` must fetch the current list of goal names from the `goals` SQLite table and inject them into the system prompt before calling the LLM, so the model can map events to existing goals (and discover new ones). `INSERT OR IGNORE INTO goals` handles idempotency.

---

## Write Pipeline (`app/graph_batch.py`)

Runs after each `parse_day()` call that returns `status = "succeeded"`. Called from `run_scheduled_batch()`.

**Steps (single Neo4j transaction):**
1. Read all extraction rows for `day` from SQLite (emotional_analysis, health_metrics, productivity_metrics, events, event_topics, goals)
2. `MERGE (d:Day {date: $day})` — set productivity fields as properties
3. Locate previous Day node; `MERGE (prev)-[:NEXT_DAY]->(d)`
4. Delete existing `EmotionState` and `HealthState` nodes for this day (idempotency), then `CREATE` fresh ones with correct edges
5. `MERGE` each `Event` on `canonical_id`; `MERGE (d)-[:HAD_EVENT]->(e)`
6. For each topic in `event_topics`: `MERGE (t:Topic {name: $topic})`; `MERGE (e)-[:INVOLVES]->(t)`
7. For each goal in `goals` table: `MERGE (g:Goal {name: $name})`; map events to goals via LLM-assigned `contributes_to_goals` field and `MERGE (e)-[:CONTRIBUTES_TO]->(g)` edges

After write completes: call `graph_maintenance.run(day)`.

---

## Maintenance Pipeline (`app/graph_maintenance.py`)

Three passes, all idempotent:

**Pass 1 — Event deduplication:**
- Group `Event` nodes where `apoc.text.levenshteinDistance(a.title, b.title) < 3`
- `apoc.refactor.mergeNodes(group, {properties: 'override'})` — keep shortest title as canonical

**Pass 2 — Topic deduplication + hierarchy:**
- Group `Topic` nodes where `apoc.text.levenshteinDistance(a.name, b.name) < 2` (tighter threshold)
- Merge duplicates
- gpt-4o call: given the list of all current Topic names, assign each to a broad `Category` (e.g., "LLMs" → "AI/ML", "Algorithm Practice" → "Computer Science") — gpt-4o used here (not mini) for more consistent, nuanced categorization across maintenance runs
- `MERGE (t)-[:BELONGS_TO]->(c:Category {name: $category})`

**Pass 3 — Goal deduplication:**
- Group `Goal` nodes where `apoc.text.levenshteinDistance(a.name, b.name) < 2` (very tight)
- Merge near-duplicates, keeping the longest (most descriptive) name as canonical

---

## Read Pipeline (LangGraph)

### State

```python
class GraphState(TypedDict):
    message: str            # user's original message text
    conversation_id: str
    intent: str             # "journaling" | "analytical"
    sqlite_context: str     # today's transcript + open todos (for synthesizer)
    cypher_query: str       # generated by cypher_agent
    graph_result: Any       # Neo4j result records
    query_error: str | None
    retry_count: int        # Cypher self-correction loops, max 3
    eval_retry_count: int   # evaluation broadening loops, max 2
    final_response: str     # written to messages table as assistant role
```

### Graph Topology

```
START → router_node
  ├── "journaling" ─────────────────────────────────────────────→ bot_node → END
  └── "analytical" → context_fetcher_node → cypher_agent_node
                                                    ↓
                                            db_executor_node
                                              ├── error, retry < 3 → self_correct_node → db_executor_node
                                              ├── error, retry ≥ 3 → synthesizer_node (graceful failure)
                                              └── success ──────→ evaluator_node
                                                                    ├── needs_retry, eval_retry < 2 → cypher_agent_node
                                                                    └── satisfied ─────────────────→ synthesizer_node → END
```

### Node Responsibilities

| Node | Model | Responsibility |
|------|-------|----------------|
| `router_node` | gpt-4o-mini | Classify intent: "journaling" vs "analytical". Analytical wins on mixed messages. |
| `bot_node` | gpt-4o-mini | Wraps existing `generate_bot_reply()` from `app/bot.py` unchanged. Writes response to DB. |
| `context_fetcher_node` | — | SQL queries: today's transcript + open todos. Populates `sqlite_context`. |
| `cypher_agent_node` | gpt-4o | System prompt includes graph ontology schema string. Generates Cypher for the user's question. |
| `db_executor_node` | — | Runs Cypher against Neo4j. Sets `graph_result` on success, `query_error` on failure. |
| `self_correct_node` | gpt-4o | Given query + error, rewrites Cypher using only allowed labels/relationships from ontology. |
| `evaluator_node` | gpt-4o | Checks if `graph_result` actually answers the user's question. Returns "satisfied" or "needs_retry" with a broader query hint. |
| `synthesizer_node` | gpt-4o-mini | Takes `graph_result` + `sqlite_context` + original message; formats a natural conversational response. Writes to `messages` table. |

### Model Assignments
- **gpt-4o-mini** — router, bot, synthesizer (latency-sensitive, simple reasoning)
- **gpt-4o** — Cypher agent, self-correction, evaluator (complex reasoning, structured query generation)

### Key Code Change
`process_message_background(conversation_id)` → `process_message_background(conversation_id, message_content)`.  
`app/routers/messages.py` passes the message content already in scope when queuing the BackgroundTask.  
The function now calls `langgraph_flow.process_message(conversation_id, message_content)` instead of `generate_bot_reply()`.

---

## Docker Compose

```yaml
# docker-compose.yml
services:
  neo4j:
    image: neo4j:latest
    ports:
      - "7474:7474"  # Browser UI
      - "7687:7687"  # Bolt protocol
    environment:
      NEO4J_AUTH: neo4j/mindforge
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - neo4j_data:/data
volumes:
  neo4j_data:
```

`.env` additions:
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=mindforge
```

---

## New Python Dependencies

```
neo4j          # official Python driver
langgraph      # LangGraph orchestration
apoc           # no pip package needed; enabled via Docker plugin
```

---

## Verification Plan

1. **Unit:** `graph_batch.write_day(day)` on a day already in SQLite → verify correct node/edge counts in Neo4j browser (`localhost:7474`)
2. **Maintenance:** manually insert two `Event` nodes with titles "DataTales" and "Data Tales" → run `graph_maintenance.run()` → verify single merged node
3. **Routing:** send a journaling message ("Had a good workout today") → confirm bot path used (no Neo4j call)
4. **Analytical:** send "What drives my Peak Performance days?" → confirm analytical path, Cypher generated, graph result returned, synthesized response written to DB
5. **Self-correction:** temporarily introduce an invalid label in the ontology schema → confirm self-correct loop fires and succeeds within 3 attempts
6. **Dashboard:** confirm `GET /api/dashboard` still returns correctly (SQLite-only, unchanged)
7. **End-to-end:** run `npm run build` + `npm run lint` in `journal-frontend/` — no errors
