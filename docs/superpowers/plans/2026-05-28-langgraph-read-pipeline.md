# LangGraph Read Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single `generate_bot_reply()` path with a LangGraph router that sends journaling messages to the existing bot unchanged, and analytical questions through a Neo4j Cypher pipeline that retrieves graph data and synthesizes a conversational answer.

**Architecture:** Every user message flows through a LangGraph `StateGraph` (sync, invoked from the existing `BackgroundTask`). A router node classifies intent; journaling goes to the existing bot unchanged; analytical goes through context fetch → Cypher generation (gpt-4o) → execution → self-correction loop (max 3) → evaluation loop (max 2) → synthesis (gpt-4o-mini). The final response is written to SQLite `messages` table in both paths — the frontend polling loop requires no changes.

**Tech Stack:** `langgraph` Python package, `gpt-4o` for Cypher generation/correction/evaluation, `gpt-4o-mini` for routing and synthesis. All nodes are sync functions (compatible with FastAPI `BackgroundTask`). Depends on Plan 1 (Neo4j running, `app/graph_db.py` and `app/graph_schema.py` present).

**Prerequisite:** Plan 1 (`docs/superpowers/plans/2026-05-28-neo4j-write-pipeline.md`) must be complete before this plan is executed.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/agents/__init__.py` | Empty package marker |
| Create | `app/agents/cypher_agent.py` | `generate_cypher()`, `correct_cypher()`, `evaluate_result()` using gpt-4o |
| Create | `app/agents/synthesizer.py` | `synthesize_response()` using gpt-4o-mini |
| Create | `app/langgraph_flow.py` | `GraphState`, all node functions, `StateGraph` wiring, `process_message()` entrypoint |
| Modify | `app/bot.py` | Change `process_message_background(conversation_id, message_content)` — delegates to `langgraph_flow.process_message()` |
| Modify | `app/routers/messages.py` | Pass `msg.content` to `process_message_background` BackgroundTask |

---

## Task 1: Install LangGraph

- [ ] **Step 1: Install the package**

```bash
pip install langgraph
```

- [ ] **Step 2: Verify it imports**

```bash
python -c "from langgraph.graph import StateGraph, END; print('langgraph ok')"
```

Expected: `langgraph ok`

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore: install langgraph"
```

(If you have a `requirements.txt`, add `langgraph` to it and commit that file instead.)

---

## Task 2: Cypher Agent Module

**Files:**
- Create: `app/agents/__init__.py`
- Create: `app/agents/cypher_agent.py`

- [ ] **Step 1: Create `app/agents/__init__.py`**

```python
```

(Empty file — just marks the directory as a Python package.)

- [ ] **Step 2: Create `app/agents/cypher_agent.py`**

```python
"""Heavy DB agent: Cypher generation, self-correction, and result evaluation."""
import json

from ..core import client
from ..graph_schema import ONTOLOGY_SCHEMA


def generate_cypher(user_message: str, eval_hint: str = "") -> str:
    """Generate a Cypher query for the user's analytical question."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Neo4j Cypher expert. Generate a single Cypher query to answer "
                "the user's question about their personal journal history. "
                "Use ONLY the labels and relationship types defined in the schema below. "
                "Prefer MATCH patterns over raw property filters where possible. "
                "Return ONLY the raw Cypher query — no explanation, no markdown fences.\n\n"
                + ONTOLOGY_SCHEMA
            ),
        },
        {"role": "user", "content": user_message},
    ]
    if eval_hint:
        messages.append({
            "role": "user",
            "content": f"Previous query was incomplete. Broaden it: {eval_hint}",
        })

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def correct_cypher(query: str, error: str) -> str:
    """Rewrite a failing Cypher query given its error message."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Cypher expert. Fix the failing query. "
                    "Use ONLY labels and relationships defined in the schema below. "
                    "Return ONLY the corrected Cypher query — no explanation, no markdown fences.\n\n"
                    + ONTOLOGY_SCHEMA
                ),
            },
            {
                "role": "user",
                "content": f"Query:\n{query}\n\nError:\n{error}\n\nRewrite it correctly.",
            },
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def evaluate_result(user_message: str, graph_result: list) -> dict:
    """Check whether graph_result fully answers user_message.
    Returns {"satisfied": bool, "hint": str}."""
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are evaluating whether a Neo4j query result fully answers a user's question. "
                    "Respond with JSON only: "
                    "{\"satisfied\": true | false, \"hint\": \"<optional: what a better query would retrieve>\"}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"User question: {user_message}\n\n"
                    f"Query result (first 20 records):\n{json.dumps(graph_result[:20], indent=2)}\n\n"
                    "Does this result fully answer the question?"
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {"satisfied": True, "hint": ""}
```

- [ ] **Step 3: Verify the module imports**

```bash
python -c "from app.agents.cypher_agent import generate_cypher, correct_cypher, evaluate_result; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 4: Commit**

```bash
git add app/agents/__init__.py app/agents/cypher_agent.py
git commit -m "feat: cypher agent — generate, correct, evaluate using gpt-4o"
```

---

## Task 3: Synthesizer Module

**Files:**
- Create: `app/agents/synthesizer.py`

- [ ] **Step 1: Create `app/agents/synthesizer.py`**

```python
"""Lightweight synthesizer: converts raw graph results into a conversational reply."""
import json

from ..core import client


def synthesize_response(
    user_message: str,
    graph_result: list,
    sqlite_context: str,
    failed: bool = False,
) -> str:
    """Format a natural language answer from graph data.
    If failed=True, returns a graceful apology without an LLM call."""
    if failed:
        return (
            "I couldn't retrieve that from your history right now — "
            "the query ran into trouble. Try rephrasing or being more specific."
        )

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are MindForge, a warm personal journaling companion. "
                    "Answer the user's question using the graph data provided. "
                    "Be concise (2-4 sentences), conversational, and specific — cite actual values "
                    "from the data where relevant. "
                    "If the graph data is empty or returns no records, say so honestly. "
                    "Do not use bullets, headings, or markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question: {user_message}\n\n"
                    f"Graph data:\n{json.dumps(graph_result[:30], indent=2)}\n\n"
                    f"Today's context:\n{sqlite_context}"
                ),
            },
        ],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()
```

- [ ] **Step 2: Verify the module imports**

```bash
python -c "from app.agents.synthesizer import synthesize_response; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 3: Commit**

```bash
git add app/agents/synthesizer.py
git commit -m "feat: synthesizer agent — format graph results into conversational response"
```

---

## Task 4: LangGraph Flow

**Files:**
- Create: `app/langgraph_flow.py`

This is the core of Plan 2. Defines `GraphState`, all node functions, the graph wiring, and the `process_message()` entrypoint called from `bot.py`.

- [ ] **Step 1: Create `app/langgraph_flow.py`**

```python
"""LangGraph orchestration: routes each user message to the journaling bot
or the analytical Cypher pipeline, then writes the final reply to SQLite."""
import json
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from .agents.cypher_agent import correct_cypher, evaluate_result, generate_cypher
from .agents.synthesizer import synthesize_response
from .bot import assemble_bot_context, generate_bot_reply, store_assistant_message
from .core import client
from .graph_db import graph_connect


class GraphState(TypedDict):
    message: str
    conversation_id: int
    intent: str               # "journaling" | "analytical"
    sqlite_context: str       # today's transcript + open todos
    cypher_query: str
    graph_result: Any         # list of dicts from Neo4j
    query_error: str          # empty string when no error
    retry_count: int          # Cypher self-correction attempts
    eval_retry_count: int     # evaluation broadening attempts
    eval_passed: bool
    eval_hint: str
    final_response: str


# ── Node functions ──────────────────────────────────────────────────────────

def _router_node(state: GraphState) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify the message as 'journaling' (user sharing info about their day) "
                    "or 'analytical' (user asking a question about patterns, history, or trends). "
                    "If the message contains ANY analytical intent, respond 'analytical'. "
                    "Respond with exactly one word: journaling or analytical."
                ),
            },
            {"role": "user", "content": state["message"]},
        ],
        temperature=0,
    )
    intent = response.choices[0].message.content.strip().lower()
    if intent not in ("journaling", "analytical"):
        intent = "journaling"
    return {"intent": intent}


def _bot_node(state: GraphState) -> dict:
    reply = generate_bot_reply(state["conversation_id"])
    store_assistant_message(state["conversation_id"], reply)
    return {"final_response": reply}


def _context_fetcher_node(state: GraphState) -> dict:
    ctx = assemble_bot_context()
    sqlite_context = (
        "Today's conversation:\n"
        + json.dumps(ctx["today_transcript"], indent=2)
        + "\n\nOpen todos:\n"
        + json.dumps(ctx["pending_todos"], indent=2)
    )
    return {"sqlite_context": sqlite_context}


def _cypher_agent_node(state: GraphState) -> dict:
    hint = state.get("eval_hint", "") if state.get("eval_retry_count", 0) > 0 else ""
    cypher = generate_cypher(state["message"], eval_hint=hint)
    return {"cypher_query": cypher, "query_error": ""}


def _db_executor_node(state: GraphState) -> dict:
    try:
        with graph_connect() as session:
            result = session.run(state["cypher_query"])
            records = [dict(r) for r in result]
        return {"graph_result": records, "query_error": ""}
    except Exception as exc:
        return {
            "graph_result": [],
            "query_error": str(exc),
            "retry_count": state.get("retry_count", 0) + 1,
        }


def _self_correct_node(state: GraphState) -> dict:
    fixed = correct_cypher(state["cypher_query"], state["query_error"])
    return {"cypher_query": fixed, "query_error": ""}


def _evaluator_node(state: GraphState) -> dict:
    verdict = evaluate_result(state["message"], state.get("graph_result", []))
    return {
        "eval_passed": verdict.get("satisfied", True),
        "eval_hint": verdict.get("hint", ""),
        "eval_retry_count": state.get("eval_retry_count", 0) + 1,
    }


def _synthesizer_node(state: GraphState) -> dict:
    failed = bool(state.get("query_error")) and state.get("retry_count", 0) >= 3
    reply = synthesize_response(
        user_message=state["message"],
        graph_result=state.get("graph_result", []),
        sqlite_context=state.get("sqlite_context", ""),
        failed=failed,
    )
    store_assistant_message(state["conversation_id"], reply)
    return {"final_response": reply}


# ── Conditional routing functions ───────────────────────────────────────────

def _route_after_router(state: GraphState) -> str:
    return "bot_node" if state["intent"] == "journaling" else "context_fetcher_node"


def _route_after_executor(state: GraphState) -> str:
    if state.get("query_error"):
        return "self_correct_node" if state.get("retry_count", 0) < 3 else "synthesizer_node"
    return "evaluator_node"


def _route_after_evaluator(state: GraphState) -> str:
    if state.get("eval_passed", True):
        return "synthesizer_node"
    if state.get("eval_retry_count", 0) < 2:
        return "cypher_agent_node"
    return "synthesizer_node"


# ── Graph assembly ───────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("router_node", _router_node)
    builder.add_node("bot_node", _bot_node)
    builder.add_node("context_fetcher_node", _context_fetcher_node)
    builder.add_node("cypher_agent_node", _cypher_agent_node)
    builder.add_node("db_executor_node", _db_executor_node)
    builder.add_node("self_correct_node", _self_correct_node)
    builder.add_node("evaluator_node", _evaluator_node)
    builder.add_node("synthesizer_node", _synthesizer_node)

    builder.set_entry_point("router_node")
    builder.add_conditional_edges("router_node", _route_after_router)

    builder.add_edge("bot_node", END)
    builder.add_edge("context_fetcher_node", "cypher_agent_node")
    builder.add_edge("cypher_agent_node", "db_executor_node")
    builder.add_conditional_edges("db_executor_node", _route_after_executor)
    builder.add_edge("self_correct_node", "db_executor_node")
    builder.add_conditional_edges("evaluator_node", _route_after_evaluator)
    builder.add_edge("synthesizer_node", END)

    return builder.compile()


_graph = _build_graph()


# ── Public entrypoint ────────────────────────────────────────────────────────

def process_message(conversation_id: int, message_content: str) -> None:
    """Entrypoint called from bot.process_message_background().
    Routes the message and writes the assistant reply to the messages table."""
    initial_state: GraphState = {
        "message": message_content,
        "conversation_id": conversation_id,
        "intent": "",
        "sqlite_context": "",
        "cypher_query": "",
        "graph_result": [],
        "query_error": "",
        "retry_count": 0,
        "eval_retry_count": 0,
        "eval_passed": False,
        "eval_hint": "",
        "final_response": "",
    }
    _graph.invoke(initial_state)
```

- [ ] **Step 2: Verify the graph compiles**

```bash
python -c "from app.langgraph_flow import _graph; print('graph nodes:', list(_graph.nodes))"
```

Expected output (order may vary):
```
graph nodes: ['router_node', 'bot_node', 'context_fetcher_node', 'cypher_agent_node', 'db_executor_node', 'self_correct_node', 'evaluator_node', 'synthesizer_node', '__start__']
```

- [ ] **Step 3: Commit**

```bash
git add app/langgraph_flow.py
git commit -m "feat: LangGraph StateGraph — router, bot path, analytical Cypher pipeline"
```

---

## Task 5: Wire Into `bot.py` and `messages.py`

**Files:**
- Modify: `app/bot.py`
- Modify: `app/routers/messages.py`

- [ ] **Step 1: Update `process_message_background` in `app/bot.py`**

Find `process_message_background` (lines 236–247) and replace it with:

```python
def process_message_background(conversation_id: int, message_content: str) -> None:
    """Background-task entrypoint after a user message. Routes through LangGraph:
    journaling → existing bot; analytical → Neo4j Cypher pipeline."""
    try:
        from .langgraph_flow import process_message
        process_message(conversation_id, message_content)
    except Exception as e:
        print(f"[BG] Reply error for conv {conversation_id}: {e}")
        store_assistant_message(
            conversation_id,
            "Hmm, I had trouble responding just now. Want to try saying that again?",
        )
```

- [ ] **Step 2: Update `messages.py` to pass message content**

Find line 44 in `app/routers/messages.py`:
```python
    background_tasks.add_task(process_message_background, conv_id)
```

Replace with:
```python
    background_tasks.add_task(process_message_background, conv_id, msg.content)
```

- [ ] **Step 3: Verify the import chain**

```bash
python -c "from app.bot import process_message_background; print('import ok')"
```

Expected: `import ok`

- [ ] **Step 4: Commit**

```bash
git add app/bot.py app/routers/messages.py
git commit -m "feat: wire LangGraph into process_message_background; pass message content from router"
```

---

## Task 6: End-to-End Smoke Tests

**Files:**
- Create: `tests/test_langgraph_flow.py`

These tests mock the LLM calls and Neo4j to verify routing logic and state transitions without network calls.

- [ ] **Step 1: Install pytest-mock if not already present**

```bash
pip install pytest-mock
```

- [ ] **Step 2: Create `tests/test_langgraph_flow.py`**

```python
"""Smoke tests for the LangGraph flow routing logic. Mocks LLM + Neo4j calls."""
from unittest.mock import MagicMock, patch


def _make_openai_response(content: str):
    """Build a minimal OpenAI response mock."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_router_classifies_journaling():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("journaling")
        result = _router_node({"message": "Had a great workout today"})
    assert result["intent"] == "journaling"


def test_router_classifies_analytical():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("analytical")
        result = _router_node({"message": "What drives my peak performance days?"})
    assert result["intent"] == "analytical"


def test_router_defaults_to_journaling_on_unexpected_output():
    from app.langgraph_flow import _router_node
    with patch("app.langgraph_flow.client") as mock_client:
        mock_client.chat.completions.create.return_value = _make_openai_response("UNKNOWN")
        result = _router_node({"message": "hey"})
    assert result["intent"] == "journaling"


def test_route_after_executor_success_goes_to_evaluator():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "", "retry_count": 0}
    assert _route_after_executor(state) == "evaluator_node"


def test_route_after_executor_error_below_max_goes_to_self_correct():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "Syntax error", "retry_count": 1}
    assert _route_after_executor(state) == "self_correct_node"


def test_route_after_executor_error_at_max_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_executor
    state = {"query_error": "Syntax error", "retry_count": 3}
    assert _route_after_executor(state) == "synthesizer_node"


def test_route_after_evaluator_satisfied_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_evaluator
    state = {"eval_passed": True, "eval_retry_count": 1}
    assert _route_after_evaluator(state) == "synthesizer_node"


def test_route_after_evaluator_not_satisfied_retries_cypher():
    from app.langgraph_flow import _route_after_evaluator
    state = {"eval_passed": False, "eval_retry_count": 1}
    assert _route_after_evaluator(state) == "cypher_agent_node"


def test_route_after_evaluator_max_retries_goes_to_synthesizer():
    from app.langgraph_flow import _route_after_evaluator
    state = {"eval_passed": False, "eval_retry_count": 2}
    assert _route_after_evaluator(state) == "synthesizer_node"


def test_synthesizer_returns_apology_on_failed_query():
    from app.agents.synthesizer import synthesize_response
    result = synthesize_response(
        user_message="anything",
        graph_result=[],
        sqlite_context="",
        failed=True,
    )
    assert "couldn't" in result.lower() or "trouble" in result.lower()


def test_db_executor_captures_neo4j_error():
    from app.langgraph_flow import _db_executor_node
    with patch("app.langgraph_flow.graph_connect") as mock_ctx:
        mock_ctx.return_value.__enter__.side_effect = Exception("Connection refused")
        result = _db_executor_node({
            "cypher_query": "MATCH (n) RETURN n",
            "retry_count": 0,
        })
    assert result["query_error"] != ""
    assert result["retry_count"] == 1
```

- [ ] **Step 3: Run the tests**

```bash
pytest tests/test_langgraph_flow.py -v
```

Expected:
```
tests/test_langgraph_flow.py::test_router_classifies_journaling PASSED
tests/test_langgraph_flow.py::test_router_classifies_analytical PASSED
tests/test_langgraph_flow.py::test_router_defaults_to_journaling_on_unexpected_output PASSED
tests/test_langgraph_flow.py::test_route_after_executor_success_goes_to_evaluator PASSED
tests/test_langgraph_flow.py::test_route_after_executor_error_below_max_goes_to_self_correct PASSED
tests/test_langgraph_flow.py::test_route_after_executor_error_at_max_goes_to_synthesizer PASSED
tests/test_langgraph_flow.py::test_route_after_evaluator_satisfied_goes_to_synthesizer PASSED
tests/test_langgraph_flow.py::test_route_after_evaluator_not_satisfied_retries_cypher PASSED
tests/test_langgraph_flow.py::test_route_after_evaluator_max_retries_goes_to_synthesizer PASSED
tests/test_langgraph_flow.py::test_synthesizer_returns_apology_on_failed_query PASSED
tests/test_langgraph_flow.py::test_db_executor_captures_neo4j_error PASSED
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_langgraph_flow.py
git commit -m "test: LangGraph routing logic and error path smoke tests"
```

---

## Task 7: Manual End-to-End Verification

The tests above cover routing logic with mocks. This task does a live check with the full stack running.

- [ ] **Step 1: Start the full stack**

Terminal 1 — Neo4j:
```bash
docker compose up -d
```

Terminal 2 — Backend:
```bash
python main.py
```

- [ ] **Step 2: Confirm journaling path is unchanged**

```bash
curl -s -X POST http://127.0.0.1:8000/api/conversations \
  -H "Content-Type: application/json" | python -m json.tool
```

Note the returned `id`. Then send a journaling message:
```bash
curl -s -X POST "http://127.0.0.1:8000/api/conversations/1/messages" \
  -H "Content-Type: application/json" \
  -d '{"content": "Had a good workout this morning, feeling motivated."}'
```

Wait 3–5 seconds, then fetch messages:
```bash
curl -s "http://127.0.0.1:8000/api/conversations/1/messages" | python -m json.tool
```

Expected: two messages — your user message and a bot reply that acknowledges the workout and asks a journaling dimension question (the existing bot behaviour, unchanged).

Check the backend terminal — you should see no `[batch] graph write` log (graph pipeline only runs in the nightly batch, not the journaling bot path).

- [ ] **Step 3: Confirm analytical path routes to Neo4j**

Send an analytical message (requires at least one day of data already in Neo4j from Plan 1):
```bash
curl -s -X POST "http://127.0.0.1:8000/api/conversations/1/messages" \
  -H "Content-Type: application/json" \
  -d '{"content": "What emotional state have I been in most this week?"}'
```

Wait 5–10 seconds (gpt-4o calls take longer), then fetch:
```bash
curl -s "http://127.0.0.1:8000/api/conversations/1/messages" | python -m json.tool
```

Expected: a reply that references actual emotional quadrant data from your history (not a generic journaling prompt).

Check the backend terminal — you should see no error logs. If Neo4j has no data yet (Plan 1 not run), the synthesizer will honestly say the graph returned empty results.

- [ ] **Step 4: Confirm graceful failure when Neo4j is down**

Stop Neo4j:
```bash
docker compose stop
```

Send another analytical question:
```bash
curl -s -X POST "http://127.0.0.1:8000/api/conversations/1/messages" \
  -H "Content-Type: application/json" \
  -d '{"content": "What topics have I been most focused on?"}'
```

Wait ~15 seconds (3 retry attempts exhaust), then fetch messages. Expected: a graceful apology message, NOT a 500 error or a silent hang. The journaling path should still work while Neo4j is down.

Restart Neo4j when done:
```bash
docker compose up -d
```

- [ ] **Step 5: Confirm dashboard is unaffected**

```bash
curl -s "http://127.0.0.1:8000/api/dashboard" | python -m json.tool | head -20
```

Expected: same dashboard response as before — no changes to the SQLite dashboard path.

- [ ] **Step 6: Build the frontend**

```bash
cd journal-frontend && npm run build && npm run lint
```

Expected: no errors. (The frontend requires no changes — it still polls the same messages endpoint.)

---

## Verification Checklist

Before declaring Plan 2 complete, confirm all of the following:

- [ ] All 11 unit tests in `tests/test_langgraph_flow.py` pass
- [ ] Journaling message → existing bot reply (no graph calls, same behaviour as before)
- [ ] Analytical message → gpt-4o Cypher query → Neo4j result → synthesized reply
- [ ] Neo4j down → graceful apology reply within ~15 seconds (not a hang or 500)
- [ ] `GET /api/dashboard` returns correctly (SQLite path unchanged)
- [ ] `npm run build && npm run lint` — no errors
