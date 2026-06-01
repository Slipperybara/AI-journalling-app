# Take MindForge AI online: SQLite → Supabase Postgres, multi-tenant, hosted

## Status

| Phase | Description | Status |
|---|---|---|
| **0** | Dependency & config hygiene | ✅ **Complete** (2026-05-31) |
| **1** | Postgres migration (still single-user) | ✅ **Complete** (2026-05-31) |
| **2** | Multi-tenant data model (Postgres + Neo4j) | ✅ **Complete** (2026-05-31) |
| **3** | Supabase Google OAuth | ✅ **Complete** (2026-05-31, JWKS asymmetric mode) |
| **4** | Deploy (Supabase + Hetzner + Render + Vercel) | ✅ **Code complete** (2026-06-01) — pending user-driven provisioning |

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

### Phase 2 — Multi-tenant data model (Postgres + Neo4j)  ✅ Complete

Every persisted thing now belongs to a user. App still uses a hardcoded `DEV_USER_ID` env var; real auth comes in phase 3.

**Postgres:**

- [x] Local dev uses plain Postgres (no Supabase CLI yet). `user_id UUID NOT NULL` on every domain table, **no FK constraint** — in production the same column will reference Supabase's `auth.users(id)` via a migration applied at provisioning time. Documented in `app/db.py`.
- [x] `user_id UUID NOT NULL` added to: `conversations`, `messages`, `parse_log`, `morning_brief_log`, `emotional_analysis`, `health_metrics`, `productivity_metrics`, `events`, `event_topics`, `event_goal_contributions`, `goals`.
- [x] Composite indexes on `(user_id, day)` for every day-keyed table + `(user_id, status)` on goals + `(user_id, started_at)` on conversations + `(user_id, created_at)` on messages.
- [x] `goals` primary key is now `(user_id, name)`.
- [x] `parse_log` and `morning_brief_log` primary keys are now `(user_id, day)`; `ON CONFLICT (user_id, day) DO UPDATE` in `app/batch.py` and `app/morning_brief.py`.
- [x] All inserts include `user_id`; all reads filter `WHERE user_id = %s`. Touched: `app/extractions.py`, `app/goals.py`, `app/batch.py`, `app/day_messages.py`, `app/morning_brief.py`, `app/bot.py`, `app/parser.py`, every `app/routers/*.py`.

**Neo4j (`app/graph_schema.py`, `app/graph_batch.py`, `app/graph_maintenance.py`, `app/morning_brief.py`, `app/langgraph_flow.py`):**

Isolation strategy: **property-based scoping with composite indexes, applied to EVERY node label including reference nodes.** Every node — domain (`Day`, `EmotionState`, `HealthState`, `Event`, `Topic`, `Goal`) and reference (`EmotionQuadrant`, `SleepQuality`, `ExerciseType`, `DietQuality`, `Category`) — carries a `user_id` UUID property. No `(:User)` root node, no `[:OWNS]` edges; the property is the single source of isolation truth.

**Why per-user reference nodes (instead of global shared ones):** if reference nodes were global, a query like `MATCH (es:EmotionState {user_id: $uid})-[:IN_QUADRANT]->(q:EmotionQuadrant)<-[:IN_QUADRANT]-(other:EmotionState) RETURN other` would traverse *through* the shared quadrant node and silently return another user's EmotionStates unless every pattern variable is also filtered. That discipline is impossible to enforce on LLM-generated Cypher in `app/langgraph_flow.py`. Duplicating reference nodes per user costs ~17 nodes per user (4 quadrants + 4 sleep + 5 exercise + 4 diet) which is trivial, and makes leakage structurally impossible.

- [x] Composite indexes on `(user_id, key)` added in `app/graph_db.py::init_graph` for every label: Day/EmotionState/EmotionQuadrant/HealthState/SleepQuality/ExerciseType/DietQuality/Event/Topic/Category/Goal.
- [x] **Per-user reference-node seeding:** `app/graph_db.py::seed_reference_nodes_for_user(user_id)` — idempotent MERGE keyed on `(user_id, label)`. Called automatically from `graph_batch.write_day(day, user_id)` on first write. `Category` nodes created dynamically by `graph_maintenance.py` also include `user_id` in the MERGE key.
- [x] **Every `MERGE` includes `user_id` in the matching key.** Audited every `MERGE` and `CREATE` in `app/graph_batch.py`, `app/graph_maintenance.py`, `app/goals.py`, `app/graph_db.py`. Two users with same-named events/goals/topics now produce distinct nodes (verified via the cross-user isolation test).
- [x] Every read query takes `$user_id` and filters every label pattern: `MATCH (es:EmotionState {user_id: $user_id})-[:IN_QUADRANT]->(q:EmotionQuadrant {user_id: $user_id})`.
- [x] Refactored: `app/graph_batch.py::write_day`, `app/graph_maintenance.py::run`, `app/morning_brief.py::_fetch_goal_momentum`, all of `app/langgraph_flow.py`.
- [x] **LangGraph guardrail in `app/graph_schema.py::validate_user_id_scoping`:** regex-based validator that rejects any Cypher missing `user_id` on any user-scoped label pattern. Called by `app/langgraph_flow.py::_db_executor_node` before each query execution; errors flow into the existing self-correction loop. `ONTOLOGY_SCHEMA` now explicitly tells the LLM about the scoping rule.

**App scaffolding:**

- [x] `dev_user_id: str = "00000000-0000-0000-0000-000000000001"` in `app/core.py::Settings`.
- [x] `app/auth.py::get_current_user_id` — dev shim that returns `UUID(settings.dev_user_id)`. Phase 3 will replace the body with JWT verification; route signatures stay the same.
- [x] `user_id: UUID = Depends(get_current_user_id)` on every route in `app/routers/{conversations,messages,dashboard,goals,admin}.py`.
- [x] `app/batch.py::run_scheduled_batch` iterates over every distinct `user_id` from `messages` (via `app/day_messages.py::get_all_user_ids_with_messages`) — auth.users isn't required for local dev, the set is data-derived.

**Tests:**

- [x] `tests/conftest.py` exposes `TEST_USER_ID` and `TEST_USER_ID_B` constants. Pins `settings.dev_user_id = TEST_USER_ID` for the session.
- [x] Existing test files (`test_goals_lifecycle.py`, `test_morning_brief.py`, `test_bot_goal_tools.py`, `test_graph_write_pipeline.py`) updated to pass `TEST_USER_ID` through every call.
- [x] New `tests/test_user_isolation.py` — 6 cross-user isolation tests: same-named goals stay separate, `list_goals` doesn't leak, Neo4j `Goal`/`Day` nodes keyed by user_id, `mark_removed` one user doesn't affect the other, `validate_user_id_scoping` rejects unscoped queries.

**Verification (executed 2026-05-31):**
- `pytest -q`: **51 passed** (45 Phase 1 + 6 new isolation tests).
- Manual two-user smoke: started backend as default `DEV_USER_ID` (user A), created goal "Ship Phase 2"; restarted with `DEV_USER_ID=00000000-0000-0000-0000-000000000002` (user B), list-goals returned `[]`, list-conversations returned `[]`, added own "Ship Phase 2" — succeeded (no collision with A). `psql` confirmed two rows in `goals`, distinct user_ids.
- Side benefit: **the Phase 1 slash-goal bug is fixed** — the goals.py rewrite for multi-tenant cleared whatever was broken.

### Phase 3 — Supabase Google OAuth  ✅ Code complete

Real auth replaces the `DEV_USER_ID` hardcode. **Dev fallback preserved**: when `SUPABASE_JWT_SECRET` is empty, the dev shim still works — local dev never breaks while Supabase is being provisioned. Flipping the env var on switches the whole app to real auth in-place.

**Supabase setup (manual, user does this):**

- [ ] Provision Supabase project at supabase.com (Free tier).
- [ ] In Authentication → Providers, enable Google. Paste a Google OAuth client ID + secret (see step below).
- [ ] In Google Cloud Console, create OAuth 2.0 client (Web application). Authorized redirect URI: `https://<project>.supabase.co/auth/v1/callback`.
- [ ] Copy `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWT_SECRET` from Supabase → Settings → API into `.env` (backend) and `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` into `journal-frontend/.env`.

**Frontend (`journal-frontend/`):** ✅

- [x] `npm i @supabase/supabase-js` — installed.
- [x] `src/supabase.js` — `createClient(VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY)`; logs a warning when env vars are missing.
- [x] `App.jsx::LoginScreen` — single "Sign in with Google" button calling `supabase.auth.signInWithOAuth({ provider: 'google', options: { redirectTo: window.location.origin } })`.
- [x] Auth gating in `App()` — `useEffect` subscribes to `supabase.auth.onAuthStateChange`; early-return `<LoginScreen />` when `SUPABASE_CONFIGURED && !session`; falls through to the existing UI when authenticated. When Supabase is not configured (`VITE_SUPABASE_URL` empty), the gate is bypassed so the dev shim keeps working.
- [x] `apiFetch(url, opts)` helper — grabs `supabase.auth.getSession()` and attaches `Authorization: Bearer <token>` when configured. Internal alias `_rawFetch` to the global so the helper isn't recursive.
- [x] Every consumer `fetch(` call (16 sites across chat, dashboard, goals, inspector, eval) swapped to `apiFetch(`.
- [x] Sidebar shows the signed-in email + a "Sign out" button when Supabase is configured.

**Backend:** ✅

- [x] `app/auth.py::get_current_user_id` — three-mode verifier:
  - **Asymmetric (JWKS, default for current Supabase projects on publishable + secret keys).** When `settings.supabase_url` is set, fetches `<project>/auth/v1/.well-known/jwks.json` once at first request and caches it; verifies ES256/RS256 with `aud="authenticated"`.
  - **Symmetric HS256.** When only `settings.supabase_jwt_secret` is set (no `supabase_url`), verifies against the shared secret — legacy mode for older Supabase projects.
  - **Dev fallback.** When neither is set, returns `settings.dev_user_id`.
- [x] All paths raise 401 on missing/expired/tampered/wrong-aud/non-UUID-sub tokens. JWKS path additionally rejects tokens signed by a key not in the project's JWKS.
- [x] `app/core.py::Settings` — added `supabase_url`, `supabase_jwt_secret` (both default to empty string so dev keeps working).
- [x] `requirements.txt` pins `httpx>=0.28.0` (used by the JWKS fetch).
- [x] Routes don't change — the `Depends(get_current_user_id)` plumbing from Phase 2 is untouched.

**Tests:** ✅

- [x] `tests/test_auth.py` — 14 tests. HS256 path (9): dev fallback, valid Bearer, missing/non-Bearer/expired/tampered/wrong-aud/missing-sub/non-UUID-sub. JWKS path (5): valid ES256 token accepted, expired/wrong-aud rejected, token signed by foreign key rejected, JWKS path takes precedence over HS256 when both configured. Test fixtures generate fresh P-256 keypairs and mock `_fetch_jwks` so no real HTTP call is made.

**Verification (executed 2026-05-31):**
- `pytest -q`: **65 passed** (51 Phase 2 + 14 auth tests covering HS256 + JWKS + dev fallback).
- `python -c "import main"` boots cleanly with neither `SUPABASE_URL` nor `SUPABASE_JWT_SECRET` set — dev fallback returns `dev_user_id`.
- `npm run build` + `npm run lint`: clean. Bundle is now ~416 KB (vs 215 KB pre-Phase 3) due to the Supabase SDK + websocket runtime.

**Manual login round-trip — deferred until user provisions Supabase**: open `npm run dev`, see the Google login screen, click, log in, see own (empty) dashboard. Second Google account in an incognito window sees an independent empty dashboard.

### Phase 4 — Deploy  ✅ Code complete (provisioning is the user's manual step)

**Code shipped (2026-06-01):**

- [x] `app/core.py::Settings` — added `batch_webhook_secret` (empty default → webhook refuses with 503), `run_inline_scheduler` (default `True` for local dev, set to `False` on Render).
- [x] `app/scheduler.py::start` — early-returns when `run_inline_scheduler` is false. Print confirms the gate at boot.
- [x] `app/routers/admin.py::run_batch_webhook` — `POST /api/admin/run-batch`. No user auth; HMAC-verifies `X-Webhook-Secret` with `hmac.compare_digest`. For every user from `get_all_user_ids_with_messages()`, runs the per-user pipeline (parse → graph write → maintenance → morning brief), capturing per-stage failures so one user's failure doesn't break the others. Returns a `{user_id: {parse, graph, maintenance, morning_brief}}` summary that the GitHub Actions log captures.
- [x] `.github/workflows/nightly-batch.yml` — cron `0 6 * * *` UTC + `workflow_dispatch` for manual runs. POSTs `RENDER_URL/api/admin/run-batch` with the shared secret. Fails the workflow if HTTP status ≠ 200.
- [x] `infra/neo4j/docker-compose.yml` — Neo4j Community 5.26 (+ APOC plugin) + Caddy reverse proxy. Heap/pagecache tuned for a 4 GB CX22. Only Caddy ports 80/443 exposed externally; 7687/7474 internal-only.
- [x] `infra/neo4j/Caddyfile` — TLS termination + Bolt-over-WebSocket reverse proxy with binary-safe streaming (`flush_interval -1`). Auto-LE cert.
- [x] `infra/neo4j/README.md` — provisioning runbook (Hetzner CX22 OR Oracle Cloud Free Tier ARM box).
- [x] `app/graph_rebuild.py` — disaster recovery. `python -m app.graph_rebuild --user <uuid>` or `--all`. Drops user's subgraph, re-seeds reference nodes, replays every succeeded day, runs maintenance.
- [x] `tests/test_run_batch_webhook.py` — 6 tests: 503 when secret unset, 401 for missing/wrong secret, 200 with correct secret + pipeline summary, 200 with empty user list, per-user failure isolation.

**Full suite: 71 passed.**

**Manual provisioning playbook — the user does this end-to-end:**

1. **Supabase Postgres** (already provisioned in Phase 3). Get the connection string from Supabase → Database → Connection string → "Session pooler" (the `aws-…-pooler.supabase.com:5432` form). Schema is `CREATE TABLE IF NOT EXISTS` everywhere, so the first connect creates it via `init_db()` on app boot.

2. **Neo4j VPS** — see `infra/neo4j/README.md` for the full runbook. TL;DR:
   - Spin up Hetzner CX22 (~€5/mo) or Oracle Cloud Free Tier (free).
   - Point a subdomain (e.g. `neo4j.yourdomain.com`) A record at the VPS.
   - Open ports 22 / 80 / 443 in the firewall.
   - `scp -r infra/neo4j root@<vps>:~/mindforge-neo4j/`
   - Create `.env` next to the compose file with `NEO4J_HOSTNAME=…` and a strong `NEO4J_PASSWORD=$(openssl rand -hex 32)`.
   - `docker compose up -d`. Wait for Caddy's LE cert.
   - Note the URI `neo4j+s://neo4j.yourdomain.com` and the password.

3. **Render web service** for the backend:
   - New Web Service, point at the GitHub repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Environment variables:
     - `OPENAI_API_KEY` = your key
     - `DATABASE_URL` = Supabase session pooler string
     - `NEO4J_URI` = `neo4j+s://neo4j.yourdomain.com`
     - `NEO4J_USER` = `neo4j`
     - `NEO4J_PASSWORD` = the VPS one from step 2
     - `SUPABASE_URL` = your Supabase project URL
     - `BATCH_WEBHOOK_SECRET` = `$(openssl rand -hex 32)` (note it; reused below)
     - `RUN_INLINE_SCHEDULER` = `false`
     - `CORS_ORIGINS` = `https://<your-vercel-url>.vercel.app` (fill after step 4)
   - Deploy. Note the Render service URL (e.g. `https://mindforge-api.onrender.com`).

4. **Vercel project** for `journal-frontend/`:
   - Import the GitHub repo; set the root directory to `journal-frontend`.
   - Framework preset: Vite (auto-detected).
   - Environment variables:
     - `VITE_API_URL` = the Render URL from step 3
     - `VITE_SUPABASE_URL` = your Supabase project URL
     - `VITE_SUPABASE_ANON_KEY` = your Supabase publishable key
   - Deploy. Note the Vercel URL.
   - Go back to Render and set `CORS_ORIGINS` to the Vercel URL; redeploy.

5. **GitHub repo secrets** for the cron:
   - `Settings → Secrets and variables → Actions → New repository secret`
   - `RENDER_URL` = the Render service URL from step 3
   - `BATCH_WEBHOOK_SECRET` = same string set on Render in step 3
   - Workflow runs nightly at 06:00 UTC automatically. Trigger manually via the Actions tab `Run workflow` button to smoke-test.

6. **First-deploy verification**:
   - Open the Vercel URL → Google login screen → log in.
   - Send a chat message; check the Render service logs for no errors.
   - Manually fire the workflow from GitHub Actions; check the run log for `users_processed: 1` and per-user `morning_brief: posted`.
   - `psql "$DATABASE_URL" -c "SELECT day, status FROM morning_brief_log;"` shows today's row.
   - Use `cypher-shell` from your laptop against `neo4j+s://neo4j.yourdomain.com` to confirm Day/Event/Topic nodes were created.

**If you ever lose the Neo4j VPS:**
```
python -m app.graph_rebuild --all
```
(Run from the Render shell or any machine with the production env vars set.)

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
