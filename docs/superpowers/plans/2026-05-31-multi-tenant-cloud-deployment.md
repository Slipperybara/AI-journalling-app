# Take MindForge AI online: SQLite → Supabase Postgres, multi-tenant, hosted

## Status

| Phase | Description | Status |
|---|---|---|
| **0** | Dependency & config hygiene | ✅ **Complete** (2026-05-31) |
| **1** | Postgres migration (still single-user) | ✅ **Complete** (2026-05-31) |
| **2** | Multi-tenant data model (Postgres + Neo4j) | ⬜ Pending |
| **3** | Supabase Google OAuth | ⬜ Pending |
| **4** | Deploy (Supabase + Hetzner + Render + Vercel) | ⬜ Pending |

## Context

The app today is a fully working single-user journaling app on the user's laptop: FastAPI + raw `sqlite3` + Neo4j-in-Docker + React/Vite frontend. The recent 2026-05-28 plans (Neo4j write pipeline, LangGraph read pipeline) are merged — the graph layer (`app/graph_batch.py`, `app/graph_maintenance.py`, `app/langgraph_flow.py`, `app/morning_brief.py`) is in place. There is **zero notion of users or auth** anywhere in the code.

The goal is to put the app online so the user and ~10 friends can use it from anywhere, with an architecture that can transition to public scale without a second rewrite. Concretely:

- SQLite → **Supabase Postgres** (Free tier sufficient for now).
- **Supabase Google OAuth** as the only sign-in method.
- Every row and every Neo4j node carries a `user_id`; strict logical isolation between users.
- **Neo4j Community self-hosted** on a small VPS (Hetzner CX22 or Oracle Cloud Free) for cost efficiency.
- **Frontend on Vercel**, **backend on Render free tier**.
- Nightly 06:00 batch fires via a **GitHub Actions cron** hitting an HMAC-protected admin webhook (Render free sleeps after 15 min, so in-process APScheduler will not fire reliably).
- **No data migration** — fresh start.

These choices come from a structured brainstorm; they bound the design space below.

## Phasing strategy

Five sequenced phases. Each phase ends with the app working end-to-end (locally for phases 0–3, on real infra for phase 4). Do not collapse phases — each is a reviewable, reversible change.

### Phase 0 — Dependency & config hygiene  ✅ Complete

Pure plumbing; no behavior change. Unblocks every later phase.

- [x] Create `requirements.txt` at repo root with pinned versions for every observed backend import: `fastapi`, `uvicorn[standard]`, `openai`, `pydantic`, `pydantic-settings`, `apscheduler`, `neo4j`, `langgraph`, `langchain-core`. Add (for later phases) `psycopg[binary,pool]` and `python-jose[cryptography]`.
- [x] Create `.env.example` at repo root listing every var read in `app/core.py::Settings`, plus the new ones we'll add (`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, `BATCH_WEBHOOK_SECRET`, `CORS_ORIGINS`, `DEV_USER_ID`, `RUN_INLINE_SCHEDULER`).
- [x] Create `journal-frontend/.env.example` with `VITE_API_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
- [x] `journal-frontend/src/App.jsx:3` — replace `const API = 'http://127.0.0.1:8000'` with `const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'`.
- [x] `main.py:13-19` — replace `allow_origins=["*"]` with a comma-split env var (`CORS_ORIGINS`), defaulting to `http://localhost:5173` locally.
- [x] Add `pytest` to `requirements-dev.txt`. (Dropped `pytest-asyncio` from the plan — no async tests in the suite.)
- [x] Extend `.gitignore` with `.env.local` and `.env.*.local` so Vite-style local env files can't be committed.

**Verification (executed 2026-05-31):** `python -c "import main"` boots and CORS origins parse from env (`['http://localhost:5173']`); `npm run build` produces a 215 KB bundle; `npm run lint` is clean; `pytest -q` is 40 passed, 4 pre-existing failures in `tests/test_goals_lifecycle.py` caused by the live `journal.db` already containing 2 active goals (test assumes empty slate). The 4 failures are **not Phase 0 regression** — Phase 0 didn't touch the goals path. Phase 1's per-test schema fixture (`tests/conftest.py`) will fix them.

**What landed:**
- New files: `requirements.txt`, `requirements-dev.txt`, `.env.example`, `journal-frontend/.env.example`
- Modified: `.gitignore`, `app/core.py` (`cors_origins` field), `main.py` (env-var CORS), `journal-frontend/src/App.jsx:3`

### Phase 1 — Postgres migration (still single-user)  ✅ Complete

Replace SQLite end-to-end. App still works for one user, no multi-tenant changes yet.

- [x] **Driver:** `psycopg` v3 with `psycopg_pool.ConnectionPool`, min=1 max=4.
- [x] **`app/db.py`:**
  - [x] Replace `connect()` body so it yields a pooled `psycopg.Connection` with `row_factory = dict_row`.
  - [x] Pool sourced from `settings.database_url`.
  - [x] Delete the `loads()` helper — `JSONB` columns return native dicts/lists; callers use `r["col"] or []` for None-normalization.
  - [x] Translate every `CREATE TABLE IF NOT EXISTS` in `init_db()`: `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`, JSON-as-TEXT columns (`cognitive_labels`, `cognitive_triggers`, `social_interactions`, `tags`, `somatic_sensations`, `supplements`, `friction_points`) become `JSONB`, `day TEXT` stays as `TEXT`.
  - [x] Drop the legacy-schema migration block + `_has_legacy_extraction_schema` helper — fresh start.
  - [x] Drop the `goals` `ALTER TABLE` loop — `status`, `fulfilled_at`, `removed_at`, `source` are in the `CREATE TABLE`.
  - [x] Add `close_pool()` and wire into `main.py`'s shutdown hook.
- [x] **Placeholder + idiom translation across `app/extractions.py`, `app/goals.py`, `app/batch.py`, `app/day_messages.py`, `app/graph_maintenance.py`, `app/morning_brief.py`, `app/bot.py`, `app/parser.py`, every `app/routers/*.py`, both test files that hit the DB:**
  - [x] `?` → `%s` (44 occurrences).
  - [x] `INSERT … ON CONFLICT(day) DO UPDATE …` works verbatim in Postgres.
  - [x] `date(m.created_at, '-6 hours')` (`app/graph_maintenance.py`, `app/day_messages.py`) replaced with `bucket_sql_expr()` helper in `app/time_buckets.py` that yields `(col::timestamp - INTERVAL 'N hours')::date`. The old `sqlite_bucket_modifier()` is gone.
  - [x] `datetime('now')` defaults → `NOW()`.
  - [x] `INSERT OR IGNORE` in `tests/test_morning_brief.py` → `INSERT ... ON CONFLICT (name) DO NOTHING`.
  - [x] `cursor.lastrowid` → `INSERT ... RETURNING id` + `fetchone()["id"]` in `app/routers/conversations.py`, `app/routers/messages.py`, `app/morning_brief.py`, `app/bot.py`.
  - [x] JSONB inserts use `%s::jsonb` casts on the placeholder; `json.dumps()` preserved on Python side. Lists/dicts come back natively on read.
  - [x] `app/routers/admin.py::inspect_day` — removed dead `SELECT … FROM todos` query (table was dropped in earlier work; was about to throw "relation does not exist").
  - [x] `app/scheduler.py` — removed dead `db.migration_ran` branch (legacy SQLite-only).
  - [x] `app/goals.py` — removed stale `import sqlite3` + `sqlite3.Cursor` / `sqlite3.Row` type hints.
  - [x] Dropped the FK on `morning_brief_log.conversation_id` — failure path writes 0 there, matches original SQLite behavior (SQLite never enforced FKs by default).
- [x] **Local Postgres for dev:** using the existing Homebrew Postgres 14 (no Supabase CLI needed for Phase 1 — auth.users gets introduced in Phase 2). Created database `mindforge_dev`; `DATABASE_URL=postgresql://localhost:5432/mindforge_dev`.
- [x] **Tests:** `tests/conftest.py` — session-scoped pool + per-test TRUNCATE of every table. Simpler than per-test schemas, doesn't blow up Postgres' connection cap, equivalent isolation. **All 45 tests pass** (the 4 pre-existing goals-lifecycle failures from Phase 0 are fixed by this fixture).

**Verification (2026-05-31):**
- `pytest -q`: **45 passed**.
- `uvicorn main:app` boots cleanly; `GET /api/dashboard` returns empty structure; `POST /api/conversations` returns `{"id": 1, ...}` (confirming `RETURNING id` round-trip works); `GET /api/conversations` lists the row back.

### Phase 2 — Multi-tenant data model (Postgres + Neo4j)  ⬜

Every persisted thing now belongs to a user. App still uses a hardcoded `DEV_USER_ID` env var; real auth comes in phase 3.

**Postgres:**

- [ ] Use Supabase's built-in `auth.users` table as the canonical user registry. Do **not** create a parallel `users` table.
- [ ] Add `user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE` to: `conversations`, `messages`, `parse_log`, `morning_brief_log`, `emotional_analysis`, `health_metrics`, `productivity_metrics`, `events`, `event_topics`, `event_goal_contributions`, `goals`.
- [ ] Composite indexes on `(user_id, day)` for every day-keyed table.
- [ ] Change `goals` primary key from `(name)` to `(user_id, name)`.
- [ ] Change `parse_log` and `morning_brief_log` primary keys from `(day)` to `(user_id, day)`. Update the `ON CONFLICT` clause in `app/batch.py` to match.
- [ ] All inserts include `user_id`; all reads have `WHERE user_id = %s` added. Touched files: `app/extractions.py`, `app/goals.py`, `app/batch.py`, `app/day_messages.py`, `app/morning_brief.py`, every `app/routers/*.py`.

**Neo4j (`app/graph_schema.py`, `app/graph_batch.py`, `app/graph_maintenance.py`, `app/morning_brief.py`, `app/langgraph_flow.py`):**

Isolation strategy: **property-based scoping with composite indexes, applied to EVERY node label including reference nodes.** Every node — domain (`Day`, `EmotionState`, `HealthState`, `Event`, `Topic`, `Goal`) and reference (`EmotionQuadrant`, `SleepQuality`, `ExerciseType`, `DietQuality`, `Category`) — carries a `user_id` UUID property. No `(:User)` root node, no `[:OWNS]` edges; the property is the single source of isolation truth.

**Why per-user reference nodes (instead of global shared ones):** if reference nodes were global, a query like `MATCH (es:EmotionState {user_id: $uid})-[:IN_QUADRANT]->(q:EmotionQuadrant)<-[:IN_QUADRANT]-(other:EmotionState) RETURN other` would traverse *through* the shared quadrant node and silently return another user's EmotionStates unless every pattern variable is also filtered. That discipline is impossible to enforce on LLM-generated Cypher in `app/langgraph_flow.py`. Duplicating reference nodes per user costs ~17 nodes per user (4 quadrants + 4 sleep + 5 exercise + 4 diet) which is trivial, and makes leakage structurally impossible.

- [ ] Add composite indexes for every label that gets a `user_id`: `CREATE INDEX day_user IF NOT EXISTS FOR (d:Day) ON (d.user_id, d.day)`, same shape for Event/Topic/Goal/EmotionState/HealthState, and for every reference label (EmotionQuadrant/SleepQuality/ExerciseType/DietQuality/Category) `ON (n.user_id, n.label)` or equivalent natural key.
- [ ] **Per-user reference-node seeding:** the existing one-shot seed in `app/graph_schema.py` (that creates the global 4 quadrants, 4 sleep, 5 exercise, 4 diet) becomes a `seed_reference_nodes_for_user(user_id)` helper. Call it the first time we ever write data for a user (idempotent `MERGE` keyed on `(user_id, label)`). For `Category` nodes that `graph_maintenance.py` creates dynamically via gpt-4o, just include `user_id` in the MERGE key — they get created lazily per user.
- [ ] **Every `MERGE` must include `user_id` in the matching key.** This is non-negotiable: today `app/graph_batch.py` does things like `MERGE (e:Event {title: $title, day: $day})` — if two users have a "morning run" on the same day, MERGE silently collapses them into a shared node, corrupting isolation. Change every MERGE to `MERGE (e:Event {user_id: $uid, title: $title, day: $day})`. Audit every `MERGE` and `CREATE` in `app/graph_batch.py`, `app/graph_maintenance.py`, `app/goals.py` — including reference-node MERGEs.
- [ ] Every read query takes `$user_id` as a parameter and filters by it on **every label pattern in the query**, not just one: `MATCH (es:EmotionState {user_id: $user_id})-[:IN_QUADRANT]->(q:EmotionQuadrant {user_id: $user_id})`. Because all reference nodes are also per-user, traversals through them stay inside the user's subgraph by construction.
- [ ] Refactor: `app/graph_batch.py::write_day`, `app/graph_maintenance.py::run_maintenance` (dedup queries must scope by user_id — otherwise dedup compares user A's events to user B's), `app/morning_brief.py` rollup queries, `app/langgraph_flow.py` static queries.
- [ ] **LangGraph guardrail (in `app/langgraph_flow.py`):** the schema description fed to the Cypher-generating LLM must explicitly state "every node — domain and reference — has a `user_id` property; every label pattern in the query MUST filter by `user_id = $user_id`". Add a post-generation validator that regex-checks the generated Cypher and rejects any query containing a label pattern (Day, Event, Topic, Goal, EmotionState, HealthState, EmotionQuadrant, SleepQuality, ExerciseType, DietQuality, Category) without an accompanying `user_id` filter — feed the rejection back into the self-correction loop (the LangGraph plan already supports up to 3 self-correction passes).

**App scaffolding:**

- [ ] Add `dev_user_id: str | None = None` to `app/core.py::Settings`.
- [ ] Add `app/auth.py` containing a `get_current_user_id` FastAPI dependency. In phase 2 it returns `UUID(settings.dev_user_id)`; in phase 3 it gets replaced with the JWT-verifying implementation.
- [ ] Wire `user_id: UUID = Depends(get_current_user_id)` into every route in `app/routers/{conversations,messages,dashboard,goals}.py` and the non-webhook admin routes.
- [ ] Update `app/batch.py::run_scheduled_batch` to iterate over all users (`SELECT id FROM auth.users`) and run `parse_day(yesterday, user_id)` for each.

**Tests:**

- [ ] Update fixtures to create a test user_id and pass it through.
- [ ] Add a cross-user isolation test: write data for two user_ids, assert every read scoped to one never returns the other's rows. Same for Neo4j.

**Verify:** with `DEV_USER_ID` set, the app behaves exactly as in phase 1. Insert a row manually with a different `user_id`, hit `/api/dashboard`, confirm only the configured user's data appears.

### Phase 3 — Supabase Google OAuth  ⬜

Real auth replaces the `DEV_USER_ID` hardcode.

**Supabase setup:**

- [ ] In Supabase dashboard, enable Google OAuth provider; register a Google OAuth app in Google Cloud Console with redirect URI `https://<project>.supabase.co/auth/v1/callback`.

**Frontend (`journal-frontend/`):**

- [ ] `npm i @supabase/supabase-js`.
- [ ] New file `src/supabase.js`: initialize `createClient(VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY)`.
- [ ] In `App.jsx`, add a `LoginScreen` component (a single "Sign in with Google" button calling `supabase.auth.signInWithOAuth({ provider: 'google' })`).
- [ ] Subscribe to `supabase.auth.onAuthStateChange`. If no session → render `<LoginScreen />`; if session → render the existing chat/dashboard.
- [ ] Centralize fetch: add a small `apiFetch(path, opts)` helper that grabs the current `session.access_token` and adds `Authorization: Bearer <token>`. Replace every direct `fetch(`${API}/…`)` in `App.jsx` with `apiFetch(…)`.

**Backend:**

- [ ] Rewrite `app/auth.py::get_current_user_id`: decode the Bearer token, verify against `SUPABASE_JWT_SECRET` using `python-jose` (HS256), check `exp`, return `UUID(payload['sub'])`.
- [ ] Cache the JWKS / secret in module scope (Supabase rotates rarely).
- [ ] Routes don't change — the dependency injection from phase 2 already gives every endpoint the right user_id.

**Tests:**

- [ ] Add tests with a fake-but-valid JWT signed by the same `SUPABASE_JWT_SECRET` to verify accept/reject paths.

**Verify:** open `npm run dev`, see the Google login screen, click, log in, see your own (empty) dashboard. Log in from an incognito window with a different Google account, see an independent empty dashboard. Send messages from each; neither sees the other.

### Phase 4 — Deploy  ⬜

Provision real infra and ship.

**Order of provisioning:**

1. [ ] **Supabase cloud project** (Free tier: 500MB DB, 50K MAU). Apply schema via `supabase db push`. Note `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET`, `DATABASE_URL` (the "session pooler" connection string).
2. [ ] **Hetzner CX22** (€4.51/mo, 4 GB RAM) or **Oracle Cloud Free Tier** ARM box (free forever, 24 GB RAM).
   - Install Docker.
   - Deploy `infra/neo4j/docker-compose.yml` (new file) with Neo4j Community 5.x + Caddy in front terminating TLS for Bolt-over-WSS on port 443. Open only ports 22, 80, 443 in the firewall.
   - Create a strong `NEO4J_PASSWORD`. Note the `neo4j+s://<vps-domain>` URI for Render.
3. [ ] **Render web service** for the backend: connect the repo, build command `pip install -r requirements.txt`, start command `uvicorn main:app --host 0.0.0.0 --port $PORT`. Set every env var from `.env.example` (the production values). `RUN_INLINE_SCHEDULER=false`.
4. [ ] **Vercel project** for `journal-frontend/`: import the subdirectory, framework preset Vite, env vars `VITE_API_URL=https://<render-url>`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. Add the Vercel URL to `CORS_ORIGINS` on the Render service.

**Backend changes for serverless-ish runtime:**

- [ ] `app/scheduler.py` — gate the APScheduler `start()` behind `if settings.run_inline_scheduler:`. Default off. Keep the catch-up sweep logic — it's still useful when triggered by the webhook.
- [ ] `main.py:31-38` — `_startup` now calls `scheduler.start()` only if the env flag is set; `_shutdown` calls `scheduler.stop()` conditionally.
- [ ] Add `POST /api/admin/run-batch` to `app/routers/admin.py`:
  - Header `X-Webhook-Secret: <BATCH_WEBHOOK_SECRET>` checked with `hmac.compare_digest`.
  - Body: optional `{"day": "YYYY-MM-DD"}`, defaults to yesterday in UTC.
  - Iterates `SELECT id FROM auth.users` and calls `parse_day(day, user_id)` + `graph_batch.write_day(day, user_id)` + `graph_maintenance.run_maintenance(user_id)` for each.
  - Returns `{user_id: status}` per user.

**GitHub Actions cron** (`.github/workflows/nightly-batch.yml`):

```yaml
on:
  schedule:
    - cron: '0 6 * * *'   # 06:00 UTC
jobs:
  batch:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -fsS -X POST \
            -H "X-Webhook-Secret: ${{ secrets.BATCH_WEBHOOK_SECRET }}" \
            -H "Content-Type: application/json" \
            "${{ secrets.RENDER_URL }}/api/admin/run-batch"
```

## Critical files to modify (cross-phase)

- **`app/db.py`** — full rewrite for `psycopg` v3 + `JSONB`. Phase 1.
- **`app/core.py`** — add `database_url`, `supabase_url`, `supabase_jwt_secret`, `batch_webhook_secret`, `cors_origins` ✅ (Phase 0), `dev_user_id`, `run_inline_scheduler`. Phases 0–4.
- **`app/extractions.py`, `app/goals.py`, `app/batch.py`, `app/day_messages.py`, `app/morning_brief.py`** — every query gets `%s` placeholders (phase 1) then `user_id` threading (phase 2).
- **`app/graph_schema.py`, `app/graph_batch.py`, `app/graph_maintenance.py`, `app/morning_brief.py`, `app/langgraph_flow.py`** — per-query `$user_id` scoping (property-based; every MERGE keyed by `user_id`); add composite indexes. Phase 2.
- **`app/scheduler.py`** — gate `start()` behind `settings.run_inline_scheduler`. Phase 4.
- **`app/routers/*.py`** — `Depends(get_current_user_id)` on every route. Phase 2 (with dev middleware) then phase 3 (real JWT).
- **`app/auth.py`** — new file: dev passthrough in phase 2, real Supabase JWT verifier in phase 3.
- **`app/routers/admin.py`** — add `POST /api/admin/run-batch` HMAC webhook. Phase 4.
- **`main.py`** — CORS from env var ✅ (Phase 0); conditional scheduler startup (phase 4).
- **`journal-frontend/src/App.jsx`** — env-var the `API` constant ✅ (Phase 0); add login gating + `apiFetch` helper (phase 3).
- **`journal-frontend/src/supabase.js`** — new file (phase 3).
- **New files:** `requirements.txt` ✅, `requirements-dev.txt` ✅, `.env.example` ✅, `journal-frontend/.env.example` ✅, `.github/workflows/nightly-batch.yml`, `infra/neo4j/docker-compose.yml`, `infra/neo4j/Caddyfile`, `app/graph_rebuild.py`.

## Reuse from the existing codebase

- `app.db.connect()` — keep the context-manager shape, just swap the driver. Callers don't change.
- `app/extractions.py::store_extractions` — already day-keyed and deletes prior rows; phase 2 just adds `user_id` to the WHERE clause and INSERT column list.
- `app/batch.py::catch_up_parses(7)` — the new admin webhook uses this same logic, just looped per user.
- `app/routers/admin.py::POST /api/admin/parse-day/{day}` — already idempotent. The new `/api/admin/run-batch` is a multi-user, multi-day wrapper around the same body. Don't duplicate; refactor `parse_day` to accept `user_id` and reuse.
- `app/graph_batch.py::write_day` + `app/graph_maintenance.py::run_maintenance` — already invoked from `parse_day`. Phase 2 just threads `user_id` through their signatures.

## Verification (end-to-end)

**After each phase:**

- Phase 0 ✅: `pip install -r requirements.txt && python main.py` boots; `npm run build && npm run lint` clean; `pytest -q` green (modulo 4 pre-existing goal-test failures from live-DB pollution — Phase 1 conftest fixes them).
- Phase 1: pytest green against local Supabase Postgres; manual smoke (chat → admin parse → dashboard) works against Postgres exactly like SQLite did.
- Phase 2: with `DEV_USER_ID=<uuid-a>`, app works as before. Manually insert rows for `<uuid-b>` via `psql`; confirm `/api/dashboard` never returns them. Neo4j inspector: `MATCH (d:Day) RETURN d.user_id, count(d)` shows correct counts per user; `MATCH (e:Event) WHERE e.user_id IS NULL RETURN count(e)` returns 0.
- Phase 3: Google login round-trip works; two browser sessions with different Google accounts see independent dashboards.
- Phase 4: log in from a phone browser to the Vercel URL, send a message, see a bot reply within a few seconds. Manually fire `curl -X POST -H 'X-Webhook-Secret: …' https://…/api/admin/run-batch` — confirm new `parse_log` rows and Neo4j `Day` nodes per user. Wait one cron cycle; next morning's dashboard reflects yesterday.

## Disaster recovery: Neo4j is rebuildable, not backed up

Postgres is the source of truth; Neo4j is a derived view of it. Every node in Neo4j is produced by `graph_batch.write_day()` from extraction-table rows in Postgres, plus `graph_maintenance.run_maintenance()` running dedup over those nodes. There is no code path that writes to Neo4j without going through Postgres first.

This means **we don't take Neo4j backups**. Disaster recovery for the VPS is "provision a new one, run the rebuild script". Add this as a first-class operation:

- New file `app/graph_rebuild.py` exposing `python -m app.graph_rebuild --user <uuid>` (one user) and `--all` (everyone).
- The script: for each target user, delete their subgraph (`MATCH (n) WHERE n.user_id = $uid DETACH DELETE n`), then iterate every day with messages in Postgres for that user and call the existing `graph_batch.write_day(day, user_id)` + `graph_maintenance.run_maintenance(user_id)`. Zero new logic — pure composition of what's already there.
- Caveat to document: `graph_maintenance.run_maintenance` makes LLM-driven decisions (Levenshtein dedup, gpt-4o topic categorization). A rebuild produces a **semantically equivalent** graph, not a byte-identical one — node IDs and exact merge groupings can differ. For all current uses (morning brief aggregates, LangGraph generates fresh Cypher per query) this is fine. If a future feature ever relies on stable Neo4j node IDs as external references, revisit and add real backups.

## Deferred / explicitly out of scope

- **Postgres Row Level Security policies.** Design now (every query already filters by `user_id`), enable later as a one-PR additive change before going truly public.
- **Per-user timezones.** Currently the batch runs at 06:00 UTC for everyone. Add `users.tz` and iterate per-user when you have non-UTC users.
- **Rate limiting, abuse protection, email verification UI, account deletion UI.** All deferrable until public launch.
- **Data migration from the existing `journal.db`.** Fresh start, per user decision.
- **Per-user Neo4j databases.** Not possible on Neo4j Community Edition. If isolation guarantees ever need to be stronger than property-based filtering, switch to Aura Pro or Enterprise — no app changes required beyond the connection setup.
- **Neo4j backups.** Skipped intentionally — see "Disaster recovery" above. Graph is rebuildable from Postgres.
