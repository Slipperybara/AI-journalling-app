# CLAUDE.md

Guidance for future Claude Code agents working in this repo, and a reference for the human owner (Jerry) when explaining the project end-to-end.

---

## Project overview

**MindForge AI** is a multi-tenant journaling app. Users sign in with Google, chat with a warm companion bot throughout the day, and the next morning receive a generated brief that summarizes the prior day from a Neo4j knowledge graph derived from their own conversations. Today's structured data only appears the next morning — the design is deliberately reflective, not real-time.

The interesting engineering surface is the chat-to-graph pipeline: every user message goes through a LangGraph state machine that routes to either a journaling reply or a GraphRAG analytical pipeline, and a nightly batch projects raw chat into a per-user Neo4j knowledge graph that the analytical path queries.

### Production deployment (as of 2026-06-01)

| Component | URL / location |
|---|---|
| Frontend (Vercel) | https://ai-journalling-app-frontend.vercel.app |
| Backend (Render free tier) | https://ai-journalling-app-api.onrender.com |
| Postgres (Supabase, asymmetric JWT signing) | Session pooler, `iazqotzjesomoyqfelgt.supabase.co` |
| Neo4j Community 5.26 (self-hosted on DigitalOcean Droplet) | `bolt+ssc://mindforge-neo4j.duckdns.org:7687` |
| Nightly batch | GitHub Actions cron at `0 6 * * *` UTC → HMAC-protected webhook |

---

## Tech stack

**Backend** — Python 3.12, FastAPI, Uvicorn, `psycopg` v3 + `psycopg_pool.ConnectionPool`, OpenAI SDK (`client.beta.chat.completions.parse` for structured output, `client.chat.completions.create` for free-form), Pydantic v2 + `pydantic-settings`, APScheduler (gated off in prod), `python-jose` for JWT verification, `httpx` for JWKS fetch, `langgraph` for the chat state machine, `neo4j` Python driver.

**Frontend** — React 19, Vite 8, Tailwind CSS v4 (via `@tailwindcss/vite` plugin, not PostCSS), `@supabase/supabase-js` client, no state library (everything in `App.jsx`).

**Data** — PostgreSQL 15+ on Supabase (no ORM, raw SQL via psycopg), Neo4j 5.26 Community (with APOC plugin) on a self-hosted VPS, no Redis/queue (background tasks via FastAPI's `BackgroundTask`).

**Auth** — Supabase Google OAuth, asymmetric JWT signing (ES256/RS256) verified against JWKS endpoint. No service-role usage from the backend.

### Things to avoid

- Don't add an ORM — all DB access uses raw `psycopg` via `app.db.connect()`. Schema lives in `init_db()` as `CREATE TABLE IF NOT EXISTS`.
- Don't use `async def` for the bot-reply background task — it runs as a sync FastAPI `BackgroundTask`. The LangGraph state machine is also sync.
- Don't parse messages inline at chat time. The only writer to extraction tables (`emotional_analysis`, `health_metrics`, `productivity_metrics`, `events`, `event_topics`, `event_goal_contributions`) is the nightly batch in `app/batch.py`.
- Don't use CommonJS in the frontend (`"type": "module"` everywhere).
- Don't use Tailwind v3 config patterns. v4 is the Vite plugin only.
- Don't access Neo4j without `user_id` in every label pattern — the `validate_user_id_scoping` regex guardrail rejects any Cypher missing it for any user-scoped label.

---

## Architecture overview

```
        ┌──────────────────────────────────────┐
        │  Vercel — React + Vite (App.jsx)     │
        │  Supabase JS client, Google OAuth    │
        └──────────────────┬───────────────────┘
                           │ apiFetch (Authorization: Bearer <JWT>)
                           ▼
        ┌──────────────────────────────────────┐
        │  Render — FastAPI / Uvicorn          │
        │  - JWKS verification (HS256/ES256)   │
        │  - Routers scope every query by uid  │
        │  - LangGraph state machine for chat  │
        └──────┬────────────────────────┬──────┘
               │                        │
               │ psycopg v3 pool        │ neo4j+ssc (Bolt+TLS)
               ▼                        ▼
       ┌──────────────────┐    ┌────────────────────────┐
       │ Supabase         │    │ DO Droplet (2 GB)      │
       │ PostgreSQL 15+   │    │ Neo4j 5.26 Community   │
       │ (source of truth)│    │ APOC plugin            │
       └────────┬─────────┘    │ Self-signed TLS cert   │
                │              └────────────────────────┘
                │ Derived projection (idempotent)
                └─────────────────────────┘

        ┌──────────────────────────────────────┐
        │  GitHub Actions cron, 06:00 UTC      │
        │  → POST /api/admin/run-batch         │
        │  with X-Webhook-Secret               │
        └──────────────────────────────────────┘
```

**Source-of-truth rule**: Postgres is canonical. Neo4j is a *derived* projection — every Neo4j node is produced by `graph_batch.write_day()` from Postgres extraction rows (plus `graph_maintenance.run()` for dedup + topic categorization). There is no code path that writes to Neo4j without going through Postgres first. Disaster recovery is `python -m app.graph_rebuild --all` — rebuild the entire graph from Postgres.

### Directory map

```
/
├── main.py                # FastAPI app + middleware + router wiring + lifecycle hooks
├── app/
│   ├── core.py            # Settings (pydantic-settings), OpenAI client singleton
│   ├── auth.py            # get_current_user_id — JWKS / HS256 / dev-shim verifier
│   ├── db.py              # psycopg pool + init_db (idempotent CREATE TABLE IF NOT EXISTS)
│   ├── time_buckets.py    # 6 AM day-bucketing helpers (Python + SQL expression)
│   ├── models.py          # Pydantic schemas — JournalParserResponse + nested
│   ├── parser.py          # parse_day_content — single gpt-4o-mini structured call
│   ├── extractions.py     # store_extractions — day-keyed JSONB writes
│   ├── day_messages.py    # get_messages_for_day / get_all_user_ids_with_messages
│   ├── bot.py             # ASSISTANT_SYSTEM_TMPL + assemble_bot_context + generate_bot_reply
│   ├── batch.py           # parse_day / catch_up_parses / run_scheduled_batch
│   ├── scheduler.py       # APScheduler gated by RUN_INLINE_SCHEDULER
│   ├── graph_db.py        # Neo4j driver lifecycle + seed_reference_nodes_for_user
│   ├── graph_schema.py    # ONTOLOGY_SCHEMA constant + validate_user_id_scoping
│   ├── graph_batch.py     # write_day, sync_day_to_graph, ensure_day_chain
│   ├── graph_maintenance.py # reconcile + Levenshtein dedup + gpt-4o topic categorization
│   ├── graph_rebuild.py   # python -m app.graph_rebuild — disaster recovery
│   ├── morning_brief.py   # post_morning_brief — gpt-4o digest + new conversation
│   ├── goals.py           # add_user_goal / mark_fulfilled / mark_removed / rename + Neo4j sync
│   ├── langgraph_flow.py  # StateGraph: router → cypher → executor → eval → synthesize
│   ├── agents/
│   │   ├── cypher_agent.py  # generate_cypher / correct_cypher / evaluate_result
│   │   └── synthesizer.py   # gpt-4o digester → FACTS/OBSERVATIONS/SUGGESTIONS
│   └── routers/
│       ├── conversations.py
│       ├── messages.py
│       ├── dashboard.py
│       ├── goals.py
│       └── admin.py       # parse-day, morning-brief, inspect, eval, run-batch (HMAC)
├── journal-frontend/
│   ├── src/
│   │   ├── App.jsx        # Whole UI — single component (chat + dashboard + inspector)
│   │   └── supabase.js    # createClient init
│   └── vite.config.js
├── infra/neo4j/           # docker-compose.yml + README — VPS provisioning
├── .github/workflows/
│   └── nightly-batch.yml  # cron 0 6 * * * → HMAC-verified webhook POST
├── tests/                 # pytest — 71 tests; conftest TRUNCATEs between tests
└── docs/superpowers/plans/2026-05-31-multi-tenant-cloud-deployment.md
```

---

## Multi-tenant isolation

Every persisted thing carries `user_id`.

**Postgres** — 11 tables, all with `user_id UUID NOT NULL`. Composite PKs:
- `goals.PRIMARY KEY (user_id, name)` — two users can have the same goal name
- `parse_log.PRIMARY KEY (user_id, day)`
- `morning_brief_log.PRIMARY KEY (user_id, day)`
- Composite `(user_id, day)` indexes on every day-keyed table

The `user_id` references Supabase's `auth.users(id)` — but **no FK constraint** in the schema. This is intentional so the schema is portable across dev/prod without depending on Supabase's auth schema being present.

**Neo4j** — every node (both domain and reference) carries a `user_id` property. Reference nodes (`EmotionQuadrant`, `SleepQuality`, `ExerciseType`, `DietQuality`) are seeded **per-user**, not globally — this closes the "traversal-through-shared-node leak" where two users' `EmotionState`s could be bridged via a shared quadrant node. Every `MERGE` keys on `(user_id, …)` so same-named events/goals/topics across users stay distinct. Every read query must filter every label pattern by `user_id = $user_id`, including reference labels.

**LangGraph guardrail** — `validate_user_id_scoping` regex-checks every LLM-generated Cypher before execution. If any of `Day | Event | Topic | Goal | EmotionState | HealthState | EmotionQuadrant | SleepQuality | ExerciseType | DietQuality | Category` appears without an accompanying `user_id` filter, the query is rejected and routed back into the self-correction loop.

---

## Day boundaries

A "day" runs **06:00 → 06:00 local** (`settings.day_boundary_hour`, default 6). Bucketing applies to message `created_at`. SQL expression: `(created_at::timestamp - INTERVAL '6 hours')::date` (via `time_buckets.bucket_sql_expr`). Python: `bucket_for(ts)`.

This means a message sent at 03:00 belongs to *yesterday's* bucket. A conversation started at 23:00 and extending past midnight stays bucketed with the start day.

---

## Data flow

**Live chat path** (one LLM call per user message, no structured writes):

1. `POST /api/conversations/{conv_id}/messages` writes the user message → enqueues `BackgroundTask(process_message_background, conv_id, content, user_id)`.
2. The background task invokes `langgraph_flow.process_message` which feeds the LangGraph state machine (see next section).
3. The state machine produces one assistant reply and writes it to `messages` as the `assistant` role.

**Nightly batch path** (writes structured tables + projects to Neo4j + posts brief):

Cron fires at 06:00 UTC via GitHub Actions → `POST /api/admin/run-batch` with `X-Webhook-Secret` → backend HMAC-verifies → iterates `get_all_user_ids_with_messages()` → per user:

1. `parse_day(yesterday, user_id)` — concatenate all of yesterday's user messages, one `gpt-4o-mini` call against `JournalParserResponse`, delete + insert extraction rows in Postgres, UPSERT `parse_log`.
2. `graph_batch.write_day(yesterday, user_id)` — read Postgres extraction rows for the day, project into Neo4j (MERGE-based for Day/Event/Topic/Goal, DELETE-then-CREATE for EmotionState/HealthState).
3. `graph_maintenance.run(user_id)` — reconcile (re-sync every succeeded day), Levenshtein dedup events/topics/goals, gpt-4o topic categorization.
4. `morning_brief.post_morning_brief(today, user_id)` — guarded by `morning_brief_log.status`. If not yet posted, creates a fresh conversation in today's bucket, generates a warm brief with gpt-4o (yesterday's emotion/health/productivity + 7-day pattern + goal momentum), writes the brief as the first assistant message.

**Startup catch-up** (when the backend restarts after the cron was missed):

`catch_up_parses` sweeps the last 7 days per user — for any day not marked `succeeded` in `parse_log`, runs `parse_day`. Then runs `graph_maintenance.run` to reconcile the graph (idempotent — MERGE-based, so re-running over already-projected days is a no-op for state but does cost one gpt-4o categorize call). Then posts today's morning brief if not yet done. The fix that makes catch-up *equivalent* to the cron is the key reason backend restarts don't leave you with an empty graph.

**Dedup invariants** — `parse_day` skips days where `parse_log.status='succeeded'`, `morning_brief` early-returns on `morning_brief_log.status IN ('posted','skipped_empty')` before any LLM call, every Neo4j MERGE is keyed by `user_id`. The only operation that double-fires when both cron and catch_up run the same day is the topic-categorization LLM call inside `_deduplicate_and_categorise_topics` (~$0.001 per run; explicitly accepted).

---

## LangGraph state machine

`app/langgraph_flow.py` defines a `StateGraph` with 7 nodes. The entry point is `_router_node`.

```
                       ┌──────────────────┐
   user message ──────▶│   router_node    │ (gpt-4o-mini, classifies intent)
                       └────────┬─────────┘
                  journaling   │   analytical
                ┌──────────────┘ └──────────────┐
                ▼                               ▼
         ┌────────────┐               ┌──────────────────┐
         │  bot_node  │               │ cypher_agent_node│◀──┐
         │ generate   │               │ generate_cypher  │   │
         │ _bot_reply │               │ (ReAct, gpt-4o)  │   │
         └─────┬──────┘               └────────┬─────────┘   │
               │                               │             │
               ▼                               ▼             │
              END                ┌──────────────────────┐    │
                                 │   db_executor_node    │   │
                                 │ - validate_user_id    │   │
                                 │   scoping (regex)     │   │
                                 │ - execute Cypher with │   │
                                 │   $user_id parameter  │   │
                                 └──┬─────────────────┬──┘   │
                                    │                 │      │
                          error,    │  no error       │      │
                          retry<3   │                 │      │
                                    ▼                 ▼      │
                          ┌──────────────────┐ ┌──────────────┐
                          │ self_correct_    │ │ evaluator_   │
                          │ node             │ │ node         │
                          │ (gpt-4o rewrite) │ │ (gpt-4o      │
                          └────────┬─────────┘ │ semantic     │
                                   │           │ check)       │
                                   └──────────▶└──────┬───────┘
                                                      │
                                  satisfied / 3-retry │ not satisfied,
                                  cap reached         │ eval_retry_count<3
                                                      │      │
                                                      ▼      └──────┐
                                          ┌──────────────────┐      │
                                          │ synthesizer_node │      │
                                          │ FACTS /          │      │
                                          │ OBSERVATIONS /   │      │
                                          │ SUGGESTIONS      │      │
                                          │ (gpt-4o digest)  │      │
                                          └────────┬─────────┘      │
                                                   │                │
                                                   ▼                │
                                       generate_bot_reply           │
                                       with graph_synthesis ──▶ END │
                                                                    │
                                                  (back to cypher) ◀┘
```

**Cycle 1 — self-correct loop** (syntax + scoping errors): `db_executor` validates user_id scoping then attempts execution. On any error (or scoping rejection), routes to `self_correct_node` which asks gpt-4o to rewrite the query given the error message, then back to `db_executor`. Capped at 3 retries; after that the synthesizer is called with `failed=True` and the bot generates an apology reply.

**Cycle 2 — eval/broaden loop** (semantic dissatisfaction): if execution succeeded but the `evaluator_node` (gpt-4o) judges the result doesn't fully answer the user's question, control returns to `cypher_agent_node` for another attempt. The cypher agent receives the full `search_history` (every prior query + result sample + evaluator hint) so it can BROADEN, PIVOT, REFINE, or DEEPEN deliberately. Capped at 3 broadening attempts; after that the synthesizer is called with whatever data is in hand.

`GraphState` carries `user_id` as a string so it can be passed as a `$user_id` parameter directly into every Cypher execution.

---

## GraphRAG architecture

The analytical path is a 4-stage GraphRAG pipeline:

**1) Retrieve** (`cypher_agent.generate_cypher`) — gpt-4o is given (a) the user's question, (b) `ONTOLOGY_SCHEMA` describing the per-user Neo4j ontology with the user_id scoping rule explicit, (c) the ReAct `search_history` of prior attempts. It returns a single Cypher query parameterized with `$user_id`.

**2) Validate + Execute** (`_db_executor_node`) — `validate_user_id_scoping` runs first as a defense-in-depth guardrail; any user-scoped label without a `user_id` filter rejects the query into the self-correct loop. If valid, the query runs against Neo4j with `user_id` bound, returns a list of dicts.

**3) Evaluate** (`cypher_agent.evaluate_result`) — gpt-4o judges whether the result actually answers the user's question (`{satisfied: bool, hint: str}`). If not satisfied, the search_history entry is appended (recording the query, count, sample, verdict, hint) and the cypher_agent gets called again with the full history visible.

**4) Synthesize** (`agents.synthesizer.synthesize_response`) — gpt-4o digests up to 30 records into a three-section internal context: `FACTS:` (concrete data points), `OBSERVATIONS:` (1–2 advisor-lens patterns), `SUGGESTIONS:` (1–2 grounded candidate recommendations). This is not user-facing prose — it's internal context handed to `generate_bot_reply` which weaves it into a reply in the journaling bot's voice. The split was added because gpt-4o on raw Neo4j JSON either dumped data verbatim or buried the relevant fact.

The "RAG" part is conceptual rather than embedding-based — the knowledge "retrieval" is structured Cypher over a per-user knowledge graph, not vector similarity. Trade-off accepted: the graph is small (single user's journaling) and the ontology is fixed, so a deterministic-schema query is more controllable than an embedding lookup. The ReAct loop compensates for the LLM's first-shot misses by giving it explicit history.

---

## CI/CD deployment pipeline

**Auto-deploy on push to `main`:**

```
git push origin main
   │
   ├─▶ Render watches main → builds → deploys backend (~3 min)
   │      pip install -r requirements.txt
   │      uvicorn main:app --host 0.0.0.0 --port $PORT
   │      init_db() materializes any new CREATE TABLE IF NOT EXISTS additions
   │      init_graph() creates Neo4j composite indexes (idempotent)
   │
   └─▶ Vercel watches main → builds → deploys frontend (~2 min)
          npm install
          npm run build
          Vite injects VITE_* env vars at build time
```

**Nightly cron** (independent of code pushes):

```
GitHub Actions @ 0 6 * * * UTC
   │
   ▼  curl -X POST -H "X-Webhook-Secret: $SECRET" $RENDER_URL/api/admin/run-batch
   ▼
admin.run_batch_webhook (hmac.compare_digest verifies secret)
   │
   ▼  for uid in get_all_user_ids_with_messages():
   ▼    parse_day(yesterday, uid)
   ▼    graph_batch.write_day(yesterday, uid)
   ▼    graph_maintenance.run(uid)
   ▼    morning_brief.post_morning_brief(today, uid)
```

### CI/CD flow when pushing a new feature — checklist

1. **Branch locally** (optional but recommended for non-trivial changes): `git checkout -b feat/<name>` off `main`.
2. **Code + tests**. Add tests under `tests/`. The existing conftest TRUNCATEs every table per test, so isolation is automatic.
3. **Verify locally**:
   ```bash
   # backend
   DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest -q
   # frontend (if frontend changed)
   cd journal-frontend && npm run build && npm run lint
   ```
4. **If you changed `app/db.py::init_db`** (new table / new column / new index) — verify the change is additive (`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` if your Postgres version supports it). Destructive changes (renames, drops, NOT NULL on existing columns) need a separate migration script you run once; the auto-`init_db()` won't apply them.
5. **If you changed Neo4j schema** (new node label, new relationship type, new property used in MERGE keys) — also update `app/graph_schema.py::ONTOLOGY_SCHEMA` because the LangGraph Cypher generator reads it. And update `USER_SCOPED_LABELS` if the new label needs user_id scoping.
6. **If you added/changed environment variables** — add them to `.env.example`, set them on Render via the dashboard or `mcp__render__update_environment_variables`, set them on Vercel (Settings → Environment Variables) if they're `VITE_*`. Restart isn't required on Render (env var change auto-triggers redeploy).
7. **Commit + push**:
   ```bash
   git checkout main && git merge --ff-only feat/<name>   # or PR + squash-merge
   git push origin main
   ```
8. **Render auto-deploys** — watch the build log (`mcp__render__list_logs` or dashboard). Green light = `[graph_db] indexes ensured` appears in app logs.
9. **Vercel auto-deploys** — green light = `Ready` state in dashboard.
10. **Smoke-test on the live URL** — log in, send a message, check `/api/dashboard`.
11. **If the change affects the nightly cron** — trigger the workflow manually from the Actions tab (`workflow_dispatch`) instead of waiting for 06:00 UTC.

### Things that DON'T auto-update

- **`infra/neo4j/`** files (docker-compose, Caddyfile, README) — these are reference only; the actual files on the DO Droplet are managed by `scp` + `docker compose up -d` manually. If you change Neo4j config, also `scp` the file over and restart.
- **Render env vars** — change in dashboard or via Render MCP. Render auto-triggers a redeploy after the change.
- **GitHub Actions secrets** — add via Settings → Secrets and variables → Actions. `RENDER_URL` and `BATCH_WEBHOOK_SECRET` must match the values on Render.
- **Supabase auth redirect allowlist** — when you add a new domain (e.g., custom domain), add it to Supabase → Authentication → URL Configuration → Redirect URLs.
- **DigitalOcean firewall** — port 7687 must stay open for Render → Neo4j. If you ever rotate to a new VPS, update the DO Firewall and the `NEO4J_URI` on Render.

### Things that DO trigger Render redeploy automatically

- Pushing to `main` on GitHub.
- Updating an environment variable on Render (via dashboard or MCP).
- Clicking "Manual Deploy → Deploy latest commit" in the Render dashboard.

### Disaster recovery

- **Neo4j VPS dies** — provision a new Droplet, run the steps in `infra/neo4j/README.md`, update `NEO4J_URI` on Render, then `python -m app.graph_rebuild --all` from anywhere with the production env vars set. The graph is fully rebuildable from Postgres.
- **Postgres data corruption** — restore from Supabase's automatic daily backup (Supabase Free tier keeps 7 days).
- **Render service unhealthy** — `mcp__render__list_logs` for diagnosis, `mcp__render__update_environment_variables` for env fixes, or roll back to a prior deploy from the dashboard.

---

## Coding conventions

- **Pydantic model `Field(description=…)`** strings are the LLM instructions for structured-output extraction — keep them precise. Changes ripple to gpt-4o-mini's parse quality.
- **Emotional quadrant values** must be exactly one of: `Peak Performance`, `High-Stress`, `Low-Energy`, `Recovery & Clarity` — string-matched in both Postgres rows and frontend badge coloring (`getQuadrantBadgeColor` in `App.jsx`).
- **Frontend state lives entirely in `App.jsx`** — no external state library. The file is large but cohesive; resist the urge to extract sub-components unless they cross a clear boundary.
- **Tailwind classes** only — no inline styles except dynamic width calculations (e.g., progress bars).
- 2-space indentation throughout; destructure imports.
- **Backend module discipline** — new domain logic under `app/`, new endpoints in a router under `app/routers/`. Don't grow `main.py` beyond its wiring role.
- **Always go through `app.db.connect()`** — it yields a pooled `psycopg.Connection` with `dict_row` factory and commits on exit.
- **JSONB columns** return native Python lists/dicts on read. Normalize `None` with `r["col"] or []`. On write, wrap with `json.dumps(...)` and use `%s::jsonb` cast in the SQL.
- **Every domain operation takes `user_id: UUID`** as a parameter and threads it into queries. Routes inject it via `Depends(get_current_user_id)`.

---

## Essential commands

**Local backend:**
```bash
# Install deps
pip install -r requirements.txt

# Run a local Postgres (Homebrew default) + dev DB
createdb mindforge_dev

# Run the app (auto-reload)
DATABASE_URL=postgresql://localhost:5432/mindforge_dev python main.py
# or: uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Local frontend (from `journal-frontend/`):**
```bash
npm install
npm run dev       # HMR dev server on 5173
npm run build     # production build
npm run lint      # ESLint
```

**Tests:**
```bash
DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest -q
# 71 tests; conftest.py TRUNCATEs every table between tests.
```

**Disaster recovery:**
```bash
# Rebuild the user's Neo4j subgraph from Postgres
python -m app.graph_rebuild --user 00000000-0000-0000-0000-000000000001

# Rebuild every user
python -m app.graph_rebuild --all
```

**Production operations (via MCP):**
- `mcp__render__list_logs` / `list_deploys` / `update_environment_variables` — service ID `srv-d8ejae3bc2fs73ckcifg`.
- `mcp__plugin_vercel_vercel__list_deployments` / `get_deployment_build_logs` — project ID `prj_thOAsx5SF4FbLINK9QjpI8AY6kTf`, team ID `team_ne7l7YARYd3xTM8AKyxJIid4`.
- GitHub Actions workflow ID `286722821` — `Nightly batch`.

---

## Workflow triggers (when changing X, also change Y)

| You change | Also update |
|---|---|
| `JournalParserResponse` or nested Pydantic models | Verify `Field(description=…)` strings are clear, and confirm dashboard handles any new/renamed keys |
| `app/db.py::init_db` (new table/column) | Confirm idempotency (`IF NOT EXISTS`); deploy will auto-apply on next boot |
| Neo4j label / relationship / scoped property | `app/graph_schema.py::ONTOLOGY_SCHEMA` AND `USER_SCOPED_LABELS` |
| Emotional quadrant strings | Pydantic `Field` description AND `getQuadrantBadgeColor` in `App.jsx` |
| `settings.day_boundary_hour` | No code changes; existing `parse_log` entries stay keyed to old buckets |
| Bot 3-role behavior | Edit `ASSISTANT_SYSTEM_TMPL` in `app/bot.py`; manually test journaling / question / dimension-nudge paths |
| New env var | `.env.example` + Render env vars + Vercel env vars (if `VITE_*`) |
| New API route | Add `Depends(get_current_user_id)` to scope by user_id; thread it into the service layer |
| New `MERGE` in Neo4j | Include `user_id` in the matching properties — otherwise two users with same name collapse into one node |

---

## Plan + history reference

The end-to-end deployment plan lives at `docs/superpowers/plans/2026-05-31-multi-tenant-cloud-deployment.md` — 5 phases (0 plumbing, 1 Postgres, 2 multi-tenant, 3 Supabase OAuth, 4 deploy infra), all complete with verification notes. Use it as the canonical reference for the multi-tenant + auth + deploy decisions and trade-offs.

Recent significant commits to understand the codebase:
- `f39923b` — Push to production phase 2-4: multi-tenant + Supabase auth + deploy infra
- `881ccfa` — Push to production phase 1.1 (Postgres migration follow-up)
- `abac6d0` — Push to production phase 1 (Postgres migration)
- `b9d964f` — Drop TODOs entirely; redesign Goals UX
- `c0cada1` — Morning brief + advisor mode in synthesizer/bot
- `1098bb2` — ReAct-style cypher_agent with search history; +1 eval loop
- `30920f9` — LangGraph agentic and GraphRAG integration
