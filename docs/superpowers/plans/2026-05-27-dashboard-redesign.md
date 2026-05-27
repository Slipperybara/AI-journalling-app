# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the read-only dashboard with a two-section layout: an interactive day-navigable todo panel (with CRUD and 06:00 carryover) and a 6-dimension weekly sparkline summary.

**Architecture:** Backend gains three new columns on `todos`, a new `todos` CRUD router, carryover logic in the batch scheduler, and a grouped-by-day todos response from the dashboard endpoint. Frontend replaces all existing dashboard panel components with `TodoPanel` (day navigation + optimistic CRUD) and `WeeklySummary` (inline SVG sparklines, no external library).

**Tech Stack:** Python/FastAPI/SQLite (backend), React 19/Tailwind v4/Vite (frontend). No new dependencies.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `app/db.py` | Modify | Add 3 `ALTER TABLE todos ADD COLUMN` migrations |
| `app/extractions.py` | Modify | Add `created_at` to todos INSERT |
| `app/batch.py` | Modify | Add `carryover_unfilled_todos`; call from `run_scheduled_batch` |
| `app/routers/todos.py` | **Create** | 5 CRUD endpoints for todos |
| `app/routers/dashboard.py` | Modify | Return todos as `{day: [...]}` dict, last 7 days |
| `main.py` | Modify | Register `todos.router` |
| `journal-frontend/src/App.jsx` | Modify | Replace DashboardView with TodoPanel + WeeklySummary |

---

## Task 1: DB Migration — Add 3 columns to todos

**Files:** Modify `app/db.py`

- [ ] **Step 1: Add migrations inside `init_db()`**

After the `CREATE TABLE IF NOT EXISTS parse_log` block (end of `init_db`), add:

```python
        # Todos v2 migration: add audit/carryover columns
        for col, definition in [
            ("created_at", "TEXT"),
            ("fulfilled_at", "TEXT"),
            ("source_day", "TEXT"),
        ]:
            cursor.execute("PRAGMA table_info(todos)")
            if not any(r[1] == col for r in cursor.fetchall()):
                cursor.execute(f"ALTER TABLE todos ADD COLUMN {col} {definition}")
```

- [ ] **Step 2: Verify migration runs clean**

```bash
cd "/Users/jerryyou/Downloads/AI Journalling App"
/opt/anaconda3/envs/AIJournal/bin/python -c "from app.db import init_db; init_db(); print('ok')"
sqlite3 journal.db "PRAGMA table_info(todos)"
```

Expected output includes rows for `created_at`, `fulfilled_at`, `source_day`.

- [ ] **Step 3: Commit**

```bash
git add app/db.py
git commit -m "feat: add created_at, fulfilled_at, source_day to todos table"
```

---

## Task 2: Update extractions.py — stamp created_at on parser-extracted todos

**Files:** Modify `app/extractions.py`

- [ ] **Step 1: Add datetime import and created_at to INSERT**

At top of file, add `from datetime import datetime` to the existing imports. Then update the todos INSERT:

```python
from datetime import datetime

# ... (keep existing imports) ...

def store_extractions(parsed: JournalParserResponse, day: str) -> None:
    with connect() as conn:
        cursor = conn.cursor()
        # ... (keep all existing inserts) ...

        for t in parsed.todos:
            cursor.execute("""
                INSERT INTO todos (day, task_description, due_date, created_at)
                VALUES (?, ?, ?, ?)
            """, (day, t.task, t.due_date, datetime.now().isoformat()))
```

- [ ] **Step 2: Verify by re-parsing a day and checking DB**

```bash
curl -s -X POST http://127.0.0.1:8000/api/admin/parse-day/2026-05-27 | python3 -m json.tool
sqlite3 "/Users/jerryyou/Downloads/AI Journalling App/journal.db" "SELECT id, task_description, created_at FROM todos WHERE day='2026-05-27'"
```

Expected: `created_at` is populated with an ISO timestamp.

- [ ] **Step 3: Commit**

```bash
git add app/extractions.py
git commit -m "feat: stamp created_at on parser-extracted todos"
```

---

## Task 3: Add carryover logic to batch.py

**Files:** Modify `app/batch.py`

- [ ] **Step 1: Add `carryover_unfilled_todos` function**

Add after `_mark_parse_log`:

```python
def carryover_unfilled_todos(from_day: str, to_day: str) -> int:
    """Copy unfilled todos from from_day into to_day as new rows.
    Idempotent: skips if any rows with source_day=from_day already exist in to_day."""
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM todos WHERE day = ? AND source_day = ?",
            (to_day, from_day),
        )
        if cursor.fetchone()[0] > 0:
            return 0
        cursor.execute("""
            INSERT INTO todos (day, task_description, is_completed, due_date, created_at, source_day)
            SELECT ?, task_description, 0, due_date, ?, ?
            FROM todos
            WHERE day = ? AND is_completed = 0
        """, (to_day, datetime.now().isoformat(), from_day, from_day))
        return cursor.rowcount
```

- [ ] **Step 2: Call carryover from `run_scheduled_batch`**

Update `run_scheduled_batch`:

```python
def run_scheduled_batch() -> None:
    """Cron entrypoint. Parses yesterday's bucket then carries over unfilled todos."""
    yesterday = (current_bucket() - timedelta(days=1)).isoformat()
    today = current_bucket().isoformat()
    try:
        parse_day(yesterday)
        print(f"[batch] scheduled parse complete for {yesterday}")
    except Exception:
        print(f"[batch] scheduled parse failed for {yesterday}")
        traceback.print_exc()
    try:
        n = carryover_unfilled_todos(yesterday, today)
        if n:
            print(f"[batch] carried over {n} unfilled todo(s) from {yesterday} to {today}")
    except Exception:
        print(f"[batch] carryover failed")
        traceback.print_exc()
```

- [ ] **Step 3: Verify carryover works (manual test)**

```bash
# Confirm there are unfilled todos in 2026-05-27
sqlite3 "/Users/jerryyou/Downloads/AI Journalling App/journal.db" \
  "SELECT id, task_description, is_completed FROM todos WHERE day='2026-05-27'"

# Call carryover directly
/opt/anaconda3/envs/AIJournal/bin/python -c "
from app.batch import carryover_unfilled_todos
n = carryover_unfilled_todos('2026-05-27', '2026-05-28')
print(f'Carried over: {n}')
"

# Check result
sqlite3 "/Users/jerryyou/Downloads/AI Journalling App/journal.db" \
  "SELECT id, task_description, source_day FROM todos WHERE day='2026-05-28'"
```

Expected: rows appear in 2026-05-28 with `source_day = '2026-05-27'`.

- [ ] **Step 4: Verify idempotency**

Run carryover again — count should stay the same, no duplicates.

```bash
/opt/anaconda3/envs/AIJournal/bin/python -c "
from app.batch import carryover_unfilled_todos
n = carryover_unfilled_todos('2026-05-27', '2026-05-28')
print(f'Second run carried over: {n}')  # Expected: 0
"
```

- [ ] **Step 5: Commit**

```bash
git add app/batch.py
git commit -m "feat: add todo carryover at 06:00 batch boundary"
```

---

## Task 4: Create todos CRUD router

**Files:** Create `app/routers/todos.py`

- [ ] **Step 1: Write the router**

```python
"""CRUD endpoints for individual todos."""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import connect


router = APIRouter(prefix="/api/todos", tags=["todos"])


class TodoCreate(BaseModel):
    task_description: str
    day: str
    due_date: str | None = None


@router.get("/{day}")
async def get_todos_for_day(day: str):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, day, task_description, is_completed, due_date,
                   created_at, fulfilled_at, source_day
            FROM todos
            WHERE day = ?
            ORDER BY id ASC
        """, (day,))
        return [dict(r) for r in cursor.fetchall()]


@router.post("")
async def create_todo(body: TodoCreate):
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO todos (day, task_description, due_date, created_at, is_completed)
            VALUES (?, ?, ?, ?, 0)
        """, (body.day, body.task_description, body.due_date, now))
        todo_id = cursor.lastrowid
    return {
        "id": todo_id,
        "day": body.day,
        "task_description": body.task_description,
        "due_date": body.due_date,
        "is_completed": 0,
        "created_at": now,
        "fulfilled_at": None,
        "source_day": None,
    }


@router.patch("/{todo_id}/complete")
async def complete_todo(todo_id: int):
    now = datetime.now().isoformat()
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE todos SET is_completed = 1, fulfilled_at = ? WHERE id = ?",
            (now, todo_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"id": todo_id, "is_completed": 1, "fulfilled_at": now}


@router.patch("/{todo_id}/uncomplete")
async def uncomplete_todo(todo_id: int):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE todos SET is_completed = 0, fulfilled_at = NULL WHERE id = ?",
            (todo_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"id": todo_id, "is_completed": 0, "fulfilled_at": None}


@router.delete("/{todo_id}")
async def delete_todo(todo_id: int):
    with connect() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": todo_id}
```

- [ ] **Step 2: Register in main.py**

Add to `main.py` imports and `include_router`:

```python
from app.routers import admin, conversations, dashboard, messages, todos

# ...
app.include_router(todos.router)
```

- [ ] **Step 3: Restart and verify endpoints**

```bash
# Restart backend (kill existing and start fresh)
pkill -f "uvicorn main:app" 2>/dev/null; sleep 0.5
cd "/Users/jerryyou/Downloads/AI Journalling App"
/opt/anaconda3/envs/AIJournal/bin/python main.py &
sleep 2

# GET todos for a day
curl -s http://127.0.0.1:8000/api/todos/2026-05-27 | python3 -m json.tool

# POST a new todo
curl -s -X POST http://127.0.0.1:8000/api/todos \
  -H "Content-Type: application/json" \
  -d '{"task_description":"Test task","day":"2026-05-27"}' | python3 -m json.tool

# Note the returned id, then PATCH complete
curl -s -X PATCH http://127.0.0.1:8000/api/todos/999/complete | python3 -m json.tool

# DELETE
curl -s -X DELETE http://127.0.0.1:8000/api/todos/999 | python3 -m json.tool
```

Replace 999 with the actual id from the POST response.

- [ ] **Step 4: Commit**

```bash
git add app/routers/todos.py main.py
git commit -m "feat: add todos CRUD router (GET/POST/PATCH/DELETE)"
```

---

## Task 5: Update dashboard.py — grouped todos

**Files:** Modify `app/routers/dashboard.py`

- [ ] **Step 1: Replace todos query with grouped-by-day dict**

Replace the todos section (lines 77–83) in `get_dashboard`:

```python
        cursor.execute("""
            SELECT id, day, task_description, is_completed, due_date,
                   created_at, fulfilled_at, source_day
            FROM todos
            WHERE day >= ?
            ORDER BY day DESC, id ASC
        """, (seven_back,))
        todos: dict[str, list] = {}
        for r in cursor.fetchall():
            d = dict(r)
            todos.setdefault(d["day"], []).append(d)
```

- [ ] **Step 2: Verify response shape**

```bash
curl -s http://127.0.0.1:8000/api/dashboard | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('todos type:', type(d['todos']).__name__)
print('todo days:', list(d['todos'].keys()))
"
```

Expected: `todos type: dict`, keys are ISO date strings.

- [ ] **Step 3: Commit**

```bash
git add app/routers/dashboard.py
git commit -m "feat: return todos grouped by day in dashboard response"
```

---

## Task 6: Frontend — TodoPanel component

**Files:** Modify `journal-frontend/src/App.jsx`

- [ ] **Step 1: Add isoAddDays helper near the top (after existing helpers)**

Add after `bucketKey`:

```js
const isoAddDays = (iso, n) => {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};
```

- [ ] **Step 2: Update App initial dashboard state**

Change:
```js
const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], todos: [] });
```
To:
```js
const [dashboard, setDashboard] = useState({ emotional: [], health: [], productivity: [], events: [], todos: {} });
```

- [ ] **Step 3: Replace DashboardView call with new props**

The `DashboardView` call in `App` JSX is unchanged (`<DashboardView data={dashboard} />`). Only its internals change.

- [ ] **Step 4: Replace `DashboardView` component**

Find and replace the entire `DashboardView` function and all its child panel components (`PanelShell`, `EmptyMsg`, `Bar`, `KV`, `EmotionalPanel`, `HealthPanel`, `ProductivityPanel`, `EventsPanel`, `TodosStrip`) with the new implementation below. Place it before the `InspectView` function.

```jsx
function DashboardView({ data }) {
  const { emotional, health, productivity, events, todos: initialTodos } = data;
  return (
    <div className="flex-1 overflow-y-auto px-8 py-7 space-y-8">
      <div className="max-w-5xl mx-auto space-y-8">
        <TodoPanel initialTodos={initialTodos} />
        <WeeklySummary emotional={emotional} health={health} productivity={productivity} events={events} />
      </div>
    </div>
  );
}

function TodoPanel({ initialTodos }) {
  const todayBucket = bucketKey(new Date());
  const [selectedDay, setSelectedDay] = useState(todayBucket);
  const [todosByDay, setTodosByDay] = useState(initialTodos || {});
  const [addInput, setAddInput] = useState('');
  const [hoveredId, setHoveredId] = useState(null);

  // When parent reloads dashboard, sync initial todos
  useEffect(() => { setTodosByDay(initialTodos || {}); }, [initialTodos]);

  // Fetch a day not yet in local state
  useEffect(() => {
    if (todosByDay[selectedDay] !== undefined) return;
    (async () => {
      const res = await fetch(`${API}/api/todos/${selectedDay}`);
      if (!res.ok) return;
      const rows = await res.json();
      setTodosByDay(prev => ({ ...prev, [selectedDay]: rows }));
    })();
  }, [selectedDay, todosByDay]);

  const todosForDay = todosByDay[selectedDay] ?? [];
  const doneCount = todosForDay.filter(t => t.is_completed).length;

  const navigate = (delta) => setSelectedDay(prev => isoAddDays(prev, delta));

  const toggle = async (todo) => {
    const endpoint = todo.is_completed ? 'uncomplete' : 'complete';
    const optimistic = { ...todo, is_completed: todo.is_completed ? 0 : 1, fulfilled_at: todo.is_completed ? null : new Date().toISOString() };
    setTodosByDay(prev => ({
      ...prev,
      [selectedDay]: prev[selectedDay].map(t => t.id === todo.id ? optimistic : t),
    }));
    const res = await fetch(`${API}/api/todos/${todo.id}/${endpoint}`, { method: 'PATCH' });
    if (!res.ok) {
      setTodosByDay(prev => ({
        ...prev,
        [selectedDay]: prev[selectedDay].map(t => t.id === todo.id ? todo : t),
      }));
    }
  };

  const deleteTodo = async (todo) => {
    setTodosByDay(prev => ({
      ...prev,
      [selectedDay]: prev[selectedDay].filter(t => t.id !== todo.id),
    }));
    const res = await fetch(`${API}/api/todos/${todo.id}`, { method: 'DELETE' });
    if (!res.ok) {
      setTodosByDay(prev => ({
        ...prev,
        [selectedDay]: [...(prev[selectedDay] || []), todo],
      }));
    }
  };

  const addTodo = async (e) => {
    e.preventDefault();
    const text = addInput.trim();
    if (!text) return;
    setAddInput('');
    const res = await fetch(`${API}/api/todos`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_description: text, day: selectedDay }),
    });
    if (!res.ok) return;
    const created = await res.json();
    setTodosByDay(prev => ({
      ...prev,
      [selectedDay]: [...(prev[selectedDay] || []), created],
    }));
  };

  return (
    <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-100 text-slate-500 transition-colors text-sm"
          >
            ‹
          </button>
          <h2 className="text-sm font-semibold text-slate-900">
            {dayLabel(selectedDay)}
            <span className="ml-2 text-slate-400 font-normal text-xs">{selectedDay}</span>
          </h2>
          <button
            onClick={() => navigate(1)}
            disabled={selectedDay >= todayBucket}
            className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-slate-100 disabled:opacity-30 disabled:cursor-not-allowed text-slate-500 transition-colors text-sm"
          >
            ›
          </button>
        </div>
        <span className="text-xs text-slate-400">
          {doneCount} / {todosForDay.length} done
        </span>
      </div>

      <div className="space-y-1 min-h-[60px]">
        {todosForDay.length === 0 && (
          <p className="text-xs text-slate-400 py-3 text-center">No tasks for this day.</p>
        )}
        {todosForDay.map(todo => (
          <div
            key={todo.id}
            className="flex items-start gap-3 px-2 py-1.5 rounded-md hover:bg-slate-50 transition-colors group"
            onMouseEnter={() => setHoveredId(todo.id)}
            onMouseLeave={() => setHoveredId(null)}
          >
            <button
              onClick={() => toggle(todo)}
              className={`mt-0.5 w-4 h-4 rounded border flex-shrink-0 flex items-center justify-center transition-colors ${
                todo.is_completed
                  ? 'bg-indigo-600 border-indigo-600 text-white'
                  : 'border-slate-300 hover:border-indigo-400'
              }`}
            >
              {todo.is_completed && <span className="text-[10px] leading-none">✓</span>}
            </button>
            <div className="flex-1 min-w-0">
              <p className={`text-sm ${todo.is_completed ? 'line-through text-slate-400' : 'text-slate-700'}`}>
                {todo.task_description}
              </p>
              <div className="flex gap-3 mt-0.5">
                {todo.fulfilled_at && (
                  <span className="text-[10px] text-emerald-600">
                    done {new Date(todo.fulfilled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
                {todo.due_date && !todo.fulfilled_at && (
                  <span className="text-[10px] text-rose-500">due {todo.due_date}</span>
                )}
                {todo.source_day && (
                  <span className="text-[10px] text-slate-400">carried from {todo.source_day}</span>
                )}
              </div>
            </div>
            {hoveredId === todo.id && (
              <button
                onClick={() => deleteTodo(todo)}
                className="text-slate-300 hover:text-rose-500 text-sm transition-colors flex-shrink-0"
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>

      <form onSubmit={addTodo} className="flex items-center gap-2 border-t border-slate-100 pt-3">
        <input
          type="text"
          value={addInput}
          onChange={e => setAddInput(e.target.value)}
          placeholder="+ Add a task…"
          className="flex-1 text-sm text-slate-700 placeholder-slate-400 bg-transparent outline-none"
        />
        <button
          type="submit"
          disabled={!addInput.trim()}
          className="text-xs px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white rounded-md transition-colors"
        >
          Add
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 5: Lint check**

```bash
cd "/Users/jerryyou/Downloads/AI Journalling App/journal-frontend" && npm run lint 2>&1
```

Fix any reported errors before continuing.

- [ ] **Step 6: Commit**

```bash
git add journal-frontend/src/App.jsx
git commit -m "feat: add interactive TodoPanel with day navigation and CRUD"
```

---

## Task 7: Frontend — WeeklySummary with sparklines

**Files:** Modify `journal-frontend/src/App.jsx`

- [ ] **Step 1: Add sparkline SVG helpers** (place before `WeeklySummary`)

```jsx
const SLEEP_MAP = { 'Poor': 0, 'Fair': 0.33, 'Good': 0.67, 'Excellent': 1 };
const DIET_MAP = { 'Junk/Heavy': 0, 'Carbs Centered': 0.25, 'Meat and Vegetable centered': 0.6, 'Clean': 1 };

function SparkPolyline({ days, byDay, W = 100, H = 32, color = '#6366f1' }) {
  const pts = days.map((d, i) => {
    const v = byDay[d];
    if (v == null) return null;
    const x = (i / Math.max(days.length - 1, 1)) * W;
    const y = H - ((v + 1) / 2) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).filter(Boolean);
  return (
    <svg width={W} height={H} className="overflow-visible">
      <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="#f1f5f9" strokeWidth="1" />
      {pts.length >= 2 && (
        <polyline points={pts.join(' ')} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      )}
      {pts.length < 2 && pts.map((p, i) => {
        const [cx, cy] = p.split(',');
        return <circle key={i} cx={cx} cy={cy} r="2" fill={color} />;
      })}
    </svg>
  );
}

function SparkBars({ days, byDay, W = 100, H = 32, color = '#6366f1' }) {
  const vals = days.map(d => byDay[d] ?? null);
  const maxVal = Math.max(...vals.filter(v => v != null), 1);
  const bw = Math.max(1, W / days.length - 2);
  return (
    <svg width={W} height={H}>
      {vals.map((v, i) => {
        const barH = v != null ? Math.max(2, (v / maxVal) * H) : 0;
        return (
          <rect key={i} x={i * (W / days.length)} y={H - barH}
            width={bw} height={barH} fill={v != null ? color : '#e2e8f0'} rx="1" />
        );
      })}
    </svg>
  );
}

function SparkDots({ days, byDay, W = 100, H = 32, color = '#6366f1' }) {
  return (
    <svg width={W} height={H}>
      {days.map((d, i) => {
        const v = byDay[d];
        const cx = (i + 0.5) * (W / days.length);
        const cy = H / 2;
        const r = v != null ? 3 + v * 4 : 2.5;
        return <circle key={d} cx={cx} cy={cy} r={r} fill={v != null ? color : '#e2e8f0'} />;
      })}
    </svg>
  );
}
```

- [ ] **Step 2: Add `WeeklySummary` component**

```jsx
function WeeklySummary({ emotional, health, productivity, events }) {
  const last7 = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  });

  const emotByDay = Object.fromEntries(emotional.map(r => [r.day, r.valence]));
  const sleepByDay = Object.fromEntries(health.filter(r => r.sleep_quality).map(r => [r.day, SLEEP_MAP[r.sleep_quality] ?? null]));
  const exerciseByDay = Object.fromEntries(health.filter(r => r.exercise_type).map(r => [r.day, r.exercise_type !== 'None' ? 1 : 0]));
  const dietByDay = Object.fromEntries(health.filter(r => r.diet_quality).map(r => [r.day, DIET_MAP[r.diet_quality] ?? null]));
  const deepByDay = Object.fromEntries(productivity.filter(r => r.deep_work_hours != null).map(r => [r.day, r.deep_work_hours]));
  const eventCountByDay = {};
  events.forEach(e => { eventCountByDay[e.day] = (eventCountByDay[e.day] || 0) + 1; });

  const avgValence = emotional.length ? (emotional.reduce((s, r) => s + r.valence, 0) / emotional.length).toFixed(2) : '—';
  const sleepDays = health.filter(r => r.sleep_quality).length;
  const exerciseDays = health.filter(r => r.exercise_type && r.exercise_type !== 'None').length;
  const dietDays = health.filter(r => r.diet_quality).length;
  const totalDeep = productivity.reduce((s, r) => s + (r.deep_work_hours || 0), 0).toFixed(1);
  const totalEvents = events.length;

  const cards = [
    { title: 'Emotional', color: '#6366f1', stat: `avg ${avgValence > 0 ? '+' : ''}${avgValence}`, sparkline: <SparkPolyline days={last7} byDay={emotByDay} color="#6366f1" /> },
    { title: 'Sleep', color: '#f43f5e', stat: `${sleepDays}/7 days`, sparkline: <SparkDots days={last7} byDay={sleepByDay} color="#f43f5e" /> },
    { title: 'Exercise', color: '#10b981', stat: `${exerciseDays}/7 days`, sparkline: <SparkBars days={last7} byDay={exerciseByDay} color="#10b981" /> },
    { title: 'Diet', color: '#f59e0b', stat: `${dietDays}/7 days`, sparkline: <SparkDots days={last7} byDay={dietByDay} color="#f59e0b" /> },
    { title: 'Deep Work', color: '#3b82f6', stat: `${totalDeep}h total`, sparkline: <SparkBars days={last7} byDay={deepByDay} color="#3b82f6" /> },
    { title: 'Events', color: '#8b5cf6', stat: `${totalEvents} total`, sparkline: <SparkBars days={last7} byDay={eventCountByDay} color="#8b5cf6" /> },
  ];

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-900">Past 7 Days</h2>
      <div className="grid grid-cols-3 gap-3">
        {cards.map(({ title, color, stat, sparkline }) => (
          <div key={title} className="bg-white border border-slate-200 rounded-xl p-4 space-y-2">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
              <span className="text-xs font-medium text-slate-700">{title}</span>
            </div>
            <div className="flex justify-center py-1">{sparkline}</div>
            <p className="text-[11px] text-slate-500 text-center">{stat}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Remove old unused components**

Delete these function definitions from App.jsx (they're fully replaced):
- `PanelShell`
- `EmptyMsg`  
- `Bar`
- `KV`
- `EmotionalPanel`
- `HealthPanel`
- `ProductivityPanel`
- `EventsPanel`
- `TodosStrip`

- [ ] **Step 4: Run lint and build**

```bash
cd "/Users/jerryyou/Downloads/AI Journalling App/journal-frontend"
npm run lint 2>&1
npm run build 2>&1
```

Both must pass with no errors.

- [ ] **Step 5: Commit**

```bash
git add journal-frontend/src/App.jsx
git commit -m "feat: add WeeklySummary with inline SVG sparklines, remove old panels"
```

---

## Task 8: End-to-End Verification

- [ ] **Step 1: Start backend and frontend**

```bash
# Backend (in one terminal)
cd "/Users/jerryyou/Downloads/AI Journalling App"
/opt/anaconda3/envs/AIJournal/bin/python main.py

# Frontend (in another terminal)
cd "/Users/jerryyou/Downloads/AI Journalling App/journal-frontend"
npm run dev
```

- [ ] **Step 2: Test Todo Panel**

Open `http://localhost:5173`. Click Dashboard tab.

- [ ] Today's todos appear
- [ ] Click `<` — moves to yesterday, loads its todos
- [ ] Click `>` from yesterday — returns to today
- [ ] `>` is disabled when on today
- [ ] Add a task via input field → appears immediately
- [ ] Click checkbox → strikethrough + "done HH:MM" label
- [ ] Hover over a row → `×` appears, click it → row disappears
- [ ] Uncheck a completed todo → strikethrough clears

- [ ] **Step 3: Test sparklines**

- [ ] 6 cards render in 3-column grid
- [ ] Each card has a sparkline SVG
- [ ] Cards with no data show grey dots/bars (not broken)
- [ ] Summary stat line renders below each sparkline

- [ ] **Step 4: Verify DB state**

```bash
sqlite3 "/Users/jerryyou/Downloads/AI Journalling App/journal.db" \
  "SELECT id, task_description, is_completed, fulfilled_at, created_at FROM todos ORDER BY id DESC LIMIT 10"
```

- [ ] **Step 5: Test carryover manually**

```bash
/opt/anaconda3/envs/AIJournal/bin/python -c "
from app.batch import carryover_unfilled_todos
from app.time_buckets import current_bucket
from datetime import timedelta
today = current_bucket().isoformat()
yesterday = (current_bucket() - timedelta(days=1)).isoformat()
n = carryover_unfilled_todos(yesterday, today)
print(f'Carried {n} todos from {yesterday} to {today}')
"
```

- [ ] **Step 6: Verify Inspect tab unaffected**

Click Inspect tab — day picker, transcript, and extractions should still work.

- [ ] **Step 7: Final quality gates**

```bash
cd "/Users/jerryyou/Downloads/AI Journalling App/journal-frontend"
npm run build 2>&1 | tail -5
npm run lint 2>&1
```

Both must be clean.

- [ ] **Step 8: Final commit**

```bash
cd "/Users/jerryyou/Downloads/AI Journalling App"
git add -A
git commit -m "feat: dashboard redesign — interactive todos + weekly sparklines"
```
