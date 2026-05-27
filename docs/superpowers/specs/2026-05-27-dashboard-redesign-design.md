# Dashboard Redesign — Design Spec
**Date:** 2026-05-27

## Problem

The current dashboard is read-only and poorly organised for daily use. Todos are a flat unfiltered list with no interactivity; the four metric panels are raw data tables rather than a weekly performance summary. The user cannot act on todos from the dashboard or see trends at a glance.

## Intended Outcome

A two-section dashboard:
1. **Interactive Todo Panel** — day-navigable, fully interactive (add, complete, delete), with automatic carryover of unfilled todos at 06:00.
2. **Weekly Performance Summary** — 6 sparkline cards showing 7-day trends across all tracked dimensions.

---

## Section 1 — Data Model

### `todos` table — three new columns

| Column | Type | Constraint | Notes |
|--------|------|-----------|-------|
| `created_at` | TEXT | NOT NULL (migration: DEFAULT current_timestamp) | Set by parser at extraction time and by manual add endpoint |
| `fulfilled_at` | TEXT | NULL | Set to ISO timestamp when marked complete; cleared on uncomplete |
| `source_day` | TEXT | NULL | Set only on carried-over rows; equals the originating day-bucket |

`is_completed` (INTEGER DEFAULT 0) and `due_date` (TEXT) are unchanged.

Migration: three `ALTER TABLE todos ADD COLUMN` statements in `app/db.py::init_db()`, safe and additive.

---

## Section 2 — Backend

### New router: `app/routers/todos.py`

| Method | Path | Body / Params | Behaviour |
|--------|------|--------------|-----------|
| GET | `/api/todos/{day}` | — | All todos for day-bucket `day`, ordered by `id ASC` |
| POST | `/api/todos` | `{task_description, day, due_date?}` | Insert with `created_at=now()`, `is_completed=0` |
| PATCH | `/api/todos/{id}/complete` | — | `is_completed=1`, `fulfilled_at=now()` |
| PATCH | `/api/todos/{id}/uncomplete` | — | `is_completed=0`, `fulfilled_at=NULL` |
| DELETE | `/api/todos/{id}` | — | Hard delete |

Registered in `main.py`.

### Carryover: `app/batch.py::carryover_unfilled_todos(from_day, to_day)`

```sql
INSERT INTO todos (day, task_description, is_completed, due_date, created_at, source_day)
SELECT ?, task_description, 0, due_date, ?, ?
FROM todos
WHERE day = ? AND is_completed = 0
```

Called from `run_scheduled_batch()` **after** `parse_day(yesterday)`. Carries yesterday's unfilled todos into today's bucket as fresh rows (`source_day = yesterday`). Safe against re-parse: carried rows have `day = today`, so `_delete_existing_rows(yesterday)` never touches them.

Guard: skip carryover if any rows with `source_day = from_day` already exist in `to_day` (idempotent re-runs of the scheduled batch won't double-carry).

### `app/extractions.py::store_extractions`

Add `created_at = datetime.now().isoformat()` to the todos INSERT. This timestamps each parser-extracted todo.

### `GET /api/dashboard` — todos section change

Replace the flat `ORDER BY id DESC LIMIT 25` query with a grouped fetch of the **last 7 day-buckets** that have todos, returning a dict keyed by day. The frontend uses this to pre-populate all days without extra round-trips.

Response shape change:
```json
"todos": {
  "2026-05-27": [...],
  "2026-05-26": [...],
  ...
}
```

---

## Section 3 — Frontend (`journal-frontend/src/App.jsx`)

### `DashboardView` — new layout

```
┌──────────────────────────────────────────────────────┐
│  [<]  Today · May 27  [>]            3 done / 5 total│
│  ☑  Finished task                     fulfilled 14:32│
│  ☐  Pending task                                     │
│  ☐  Another task                      due 2026-05-28 │
│  [+ Add a task…                                   ↵] │
└──────────────────────────────────────────────────────┘

┌──────────┐ ┌──────────┐ ┌──────────┐
│ Emotional│ │  Sleep   │ │ Exercise │
│ sparkline│ │  dots    │ │  bars    │
│ avg +0.3 │ │ 4/7 days │ │ 3/7 days │
└──────────┘ └──────────┘ └──────────┘
┌──────────┐ ┌──────────┐ ┌──────────┐
│   Diet   │ │Deep Work │ │  Events  │
│  dots    │ │  bars    │ │  bars    │
│ 2/7 days │ │ 8.5h tot │ │ 5 total  │
└──────────┘ └──────────┘ └──────────┘
```

### `TodoPanel` component

**State:** `selectedDay` (ISO string, default = today's bucket), `todosByDay` (dict from dashboard response), `addInput` (string).

**Day navigation:** Left arrow decrements `selectedDay` by 1 day; right arrow increments, disabled when `selectedDay === todayBucket`. Days with no todos still navigable (show empty state).

**Todo row:** clickable checkbox (`PATCH /complete` or `/uncomplete`), task text (line-through when done), `fulfilled_at` time if set, `due_date` if set, `×` delete button (visible on row hover, calls `DELETE /api/todos/{id}`). Optimistic UI: update local state immediately, roll back on API error.

**Add input:** text input pinned to bottom of panel, `Enter` submits `POST /api/todos` for `selectedDay`. Clears on success.

**Data loading:** on mount and on `view === 'dashboard'`, fetch `GET /api/dashboard` which now returns `todos` as a dict-by-day. Individual PATCH/DELETE/POST calls update the local `todosByDay` state slice for `selectedDay`.

### `WeeklySummary` component

Six cards in a `grid grid-cols-3 gap-4`. Each card:
- Title + accent dot
- Inline SVG sparkline (100×32px viewBox, no external library):
  - **Emotional:** polyline across 7 days plotting `valence` (–1 to +1 mapped to 0–32)
  - **Sleep:** 7 filled circles, sized by quality (Poor=4px, Fair=6px, Good=8px, Excellent=10px); grey if no data
  - **Exercise:** 7 rect bars, filled if exercise recorded that day
  - **Diet:** same dot pattern as sleep, mapped to diet quality
  - **Deep Work:** 7 rect bars scaled to hours (max = highest day in window)
  - **Events:** 7 rect bars scaled to event count
- One-line summary stat below the sparkline

Data source: `emotional`, `health`, `productivity`, `events` arrays already in the dashboard response. Build a 7-day date scaffold client-side; fill in data points where a matching `day` exists, leave null for missing days.

### Removed components

`EmotionalPanel`, `HealthPanel`, `ProductivityPanel`, `EventsPanel`, `TodosStrip` — all replaced. `PanelShell`, `EmptyMsg`, `Bar`, `KV` helpers may be removed or repurposed.

---

## Critical Files

| File | Change |
|------|--------|
| `app/db.py` | Add 3 `ALTER TABLE todos ADD COLUMN` migrations |
| `app/extractions.py` | Add `created_at` to todos INSERT |
| `app/batch.py` | Add `carryover_unfilled_todos`; call from `run_scheduled_batch` |
| `app/routers/todos.py` | **New** — 5 endpoints |
| `app/routers/dashboard.py` | Change todos query to grouped-by-day, last 7 days |
| `main.py` | Register `todos.router` |
| `journal-frontend/src/App.jsx` | Replace `DashboardView` and all panel components with `TodoPanel` + `WeeklySummary` |

Unchanged: `app/parser.py`, `app/models.py`, `app/bot.py`, `app/scheduler.py`, `app/day_messages.py`.

---

## Verification

1. **Carryover:** Send a message with a todo, parse the day, mark it unfilled, restart backend past 06:00 (or call `run_scheduled_batch` manually) — confirm the todo appears in the next day's bucket with `source_day` set.
2. **Todo CRUD:** From dashboard, add a todo for today → appears immediately. Check checkbox → `fulfilled_at` shows. Uncheck → clears. Delete → gone. Verify DB state with `sqlite3 journal.db`.
3. **Day navigation:** Click `<` three times — see three prior days. Right arrow disabled at today.
4. **Sparklines:** Confirm 7-day scaffold renders, empty days show neutral/zero, data days show values.
5. **Quality gates:** `npm run build && npm run lint` clean. Backend imports clean. `GET /api/dashboard` returns new todos shape without breaking the Inspect tab.
