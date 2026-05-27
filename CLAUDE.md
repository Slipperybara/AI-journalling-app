# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview & Critical Rules

**MindForge AI** is a personal journaling app. Users chat with a warm companion bot throughout their day. The bot reflects todos/events back conversationally, gently nudges about uncovered life dimensions (sleep, exercise, diet, deep work, etc.), and answers questions. A nightly batch parses the full day's chat into structured analytics (emotions, health, productivity, events, todos) at ~06:00 local. Today's structured data only appears in the dashboard the next morning.

**Core Behaviors:**
- Exhaust all reading/exploration before asking questions.
- Execute first, refine later — prefer making a working change over seeking clarification.
- **Before reporting any change as complete, always run `npm run build` (frontend) and `npm run lint` (frontend) to confirm no errors. Fix all errors before responding.**
- Live messages are NEVER parsed inline. The nightly batch at the 6 AM bucket boundary is the only writer to `emotional_analysis`, `health_metrics`, `productivity_metrics`, `events`, `todos`. Per-message background tasks make exactly one LLM call (the bot reply).

---

## Tech Stack Constraints

**Used:**
- Backend: Python 3, FastAPI, Uvicorn, SQLite (`sqlite3` stdlib), OpenAI SDK (`client.beta.chat.completions.parse`), Pydantic v2 + `pydantic-settings`, APScheduler.
- Frontend: React 19, Vite 8, Tailwind CSS v4 (via `@tailwindcss/vite` plugin, not PostCSS config), ESLint.

**Avoid:**
- Do not use `asyncio` or `async def` for the bot-reply background task — it runs as a FastAPI `BackgroundTask` (sync function).
- Do not introduce an ORM (SQLAlchemy, Tortoise, etc.) — all DB access uses raw `sqlite3` via the `app.db.connect()` context manager with `conn.row_factory = sqlite3.Row`.
- Do not use CommonJS (`require`) in the frontend — the project is `"type": "module"`.
- Do not use Tailwind v3 config patterns (`tailwind.config.js`, `@tailwind` directives) — v4 is configured entirely through the Vite plugin.
- Do not parse inline in the live message path. All structured extraction belongs to `app/batch.py`.

---

## Architecture & Directory Overview

```
/                        # Repo root
├── main.py              # FastAPI app, middleware, scheduler hooks, router includes
├── journal.db           # SQLite database (auto-created/migrated on first run)
├── .env                 # OPENAI_API_KEY (loaded by pydantic-settings)
├── app/                 # All backend domain logic
│   ├── core.py          # Settings (incl. day_boundary_hour), OpenAI client, DB_NAME
│   ├── db.py            # init_db() + day-keyed migrations + connect() context manager + loads helper
│   ├── time_buckets.py  # 6 AM day-bucketing: bucket_for, current_bucket, sqlite_bucket_modifier
│   ├── models.py        # Pydantic schemas: JournalParserResponse and nested, MessageCreate
│   ├── parser.py        # PARSER_SYSTEM_BATCH + parse_day_content (single LLM call per day)
│   ├── extractions.py   # store_extractions (day-keyed writes; caller deletes prior rows first)
│   ├── bot.py           # ASSISTANT_SYSTEM_TMPL, assemble_bot_context, generate_bot_reply, process_message_background
│   ├── batch.py         # parse_day, catch_up_parses, run_scheduled_batch
│   ├── scheduler.py     # APScheduler lifecycle (start at app boot, cron at 06:00, threaded catch-up)
│   └── routers/         # FastAPI routers, one file per domain
│       ├── conversations.py
│       ├── messages.py
│       ├── dashboard.py
│       └── admin.py     # POST /api/admin/parse-day/{day}
└── journal-frontend/    # React SPA
    ├── src/
    │   ├── App.jsx      # Entire frontend: chat view + dashboard view (single component)
    │   └── main.jsx     # React DOM entry point
    └── vite.config.js   # Vite + React + Tailwind v4 plugins
```

**DB Schema (all day-keyed via the `day TEXT` column):**
- `conversations` — id, started_at
- `messages` — id, conversation_id, role, content, created_at
- `emotional_analysis` — one row per day-bucket: valence, arousal, primary_quadrant, cognitive_labels/triggers/social_interactions (JSON strings), day
- `health_metrics` — one row per day-bucket if any health field was mentioned: sleep_quality, exercise_type, diet_quality, somatic_sensations, physical_performance, supplements, day
- `productivity_metrics` — one row per day-bucket if any productivity field was mentioned: deep_work_hours, shallow_work_hours, time_block_adherence, cognitive_load, friction_points, day
- `events` — many rows per day-bucket, day-keyed
- `todos` — many rows per day-bucket, day-keyed
- `parse_log` — (day PRIMARY KEY, status, parsed_at, error) — tracks batch idempotency

**Day boundary:** A "day" runs **06:00 → 06:00 local** (`settings.day_boundary_hour`, default 6). A conversation belongs to day N if its `started_at` falls in [06:00 day N, 06:00 day N+1). Conversations that span midnight stay with their start day. A fresh conversation started at 03:00 still buckets with the previous calendar day. The SQL idiom is `date(started_at, '-6 hours')`.

**Data Flow:**
1. `POST /api/conversations/{conv_id}/messages` → write the user message to `messages` → enqueue a `BackgroundTask`.
2. Background: ONE `gpt-4o-mini` call via `generate_bot_reply`. The bot's context is today's raw transcript + last 3 days of parsed rows + 7-day summary + open todos. The reply is written to `messages` as the assistant role. No structured tables are touched.
3. At 06:00 local: APScheduler fires `run_scheduled_batch` → `parse_day(yesterday)`. The batch concatenates the full day's user messages, single `gpt-4o-mini` call against `JournalParserResponse`, writes day-keyed rows.
4. On app startup: `catch_up_parses(7)` sweeps the last 7 buckets and parses any not marked `succeeded` in `parse_log`. Runs in a background thread so startup is not blocked.
5. `GET /api/dashboard` → reads day-keyed rows from the structured tables, last 7 days. Legacy per-message rows where `day IS NULL` are excluded.
6. `POST /api/admin/parse-day/{day}` → idempotent manual trigger for a single day-bucket.

**API base URL** is hardcoded in `App.jsx` as `http://127.0.0.1:8000` — no proxy configured in Vite.

---

## Coding Conventions

- **Pydantic models** define the OpenAI structured output schema — `Field(description=...)` strings are the LLM instructions, keep them precise.
- **Emotional quadrant values** must be exactly one of: `Peak Performance`, `High-Stress`, `Low-Energy`, `Recovery & Clarity` — string-matched in both the DB and frontend badge coloring (`getQuadrantBadgeColor`).
- **Frontend state** lives entirely in `App.jsx` — no external state library. Keep it that way for features of similar scope.
- **Tailwind classes** only — no inline styles except dynamic width calculations (e.g., progress bars using `style={{ width: ... }}`).
- 2-space indentation throughout; destructure imports.
- **Backend module discipline.** Add new domain logic under `app/` and a new router under `app/routers/` if it has its own endpoints. Don't grow `main.py` beyond its wiring role.
- **Always go through `app.db.connect()`** for DB work — it sets `row_factory` and commits on exit.

---

## Essential Commands & Workflows

**Backend:**
```bash
# Install dependencies
pip install fastapi uvicorn openai pydantic pydantic-settings apscheduler

# Run with auto-reload (from repo root)
python main.py
# or: uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Frontend (from `journal-frontend/`):**
```bash
npm install
npm run dev       # dev server (HMR)
npm run build     # production build
npm run lint      # ESLint check
npm run preview   # preview production build
```

**Workflow triggers:**
- Changing `JournalParserResponse` or any nested Pydantic model in `app/models.py` → verify field descriptions are clear enough to guide GPT-4o-mini, and confirm the frontend dashboard handles any new/renamed keys.
- Adding a new DB table or column → add `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE` in `app/db.py::init_db` so it self-migrates on startup.
- Changing the emotional quadrant strings → update both the Pydantic `Field(description=...)` and `getQuadrantBadgeColor` in `App.jsx` atomically.
- Changing `settings.day_boundary_hour` → both the live bot context and the batch use this value; no code changes needed, but expect existing `parse_log` entries to remain keyed to old buckets.
- Tweaking the bot's three-role behavior → edit `ASSISTANT_SYSTEM_TMPL` in `app/bot.py`. Test manually: ask the bot a question (should answer), mention a todo (should reflect), avoid mentioning sleep for several turns (should eventually nudge).
