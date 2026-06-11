# AG-UI Token Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chat poll-loop with live token streaming over the AG-UI protocol, so the assistant reply renders progressively token-by-token.

**Architecture:** A new `POST /api/conversations/{conv_id}/agui` endpoint returns a `StreamingResponse` of AG-UI events (`RUN_STARTED → TEXT_MESSAGE_START → TEXT_MESSAGE_CONTENT* → TEXT_MESSAGE_END → RUN_FINISHED`). The LangGraph machine runs only to produce routing + synthesis facts; reply generation moves out of the graph into a new streaming generator `generate_bot_reply_stream`. The frontend swaps its `setInterval` poll for an `@ag-ui/client` `HttpAgent` subscription. Spec: `docs/superpowers/specs/2026-06-11-agui-token-streaming-design.md`.

**Tech Stack:** Python 3.12 / FastAPI / `ag-ui-protocol` (imports as `ag_ui`) / OpenAI v2 SDK streaming; React 19 / Vite / `@ag-ui/client` + `@ag-ui/core`.

---

## File Structure

- `requirements.txt` — add `ag-ui-protocol`.
- `app/bot.py` — extract `_build_reply_messages()` shared helper; add `generate_bot_reply_stream()`.
- `app/langgraph_flow.py` — gut reply-gen from terminal nodes; add `run_graph()`; update `process_message()`.
- `app/routers/agui.py` — **new** streaming endpoint.
- `main.py` — wire the new router.
- `tests/test_agui_stream.py` — **new** endpoint + streaming-generator tests.
- `journal-frontend/package.json` — add `@ag-ui/client`, `@ag-ui/core`.
- `journal-frontend/src/App.jsx` — rewrite `sendMessage` to stream; remove poll machinery.

---

## Task 1: Add the AG-UI backend dependency and confirm SDK surface

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt` (after the existing `openai==2.37.0` line grouping):

```
ag-ui-protocol
```

- [ ] **Step 2: Install it**

Run: `pip install ag-ui-protocol`
Expected: installs `ag_ui` (and its pydantic dep, already present).

- [ ] **Step 3: Confirm the exact class/field surface we depend on**

Run:

```bash
python -c "
from ag_ui.core import (RunAgentInput, EventType, RunStartedEvent, RunFinishedEvent,
    TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent)
from ag_ui.encoder import EventEncoder
import ag_ui.core as c
print('ERROR EVENT:', [n for n in dir(c) if 'Error' in n])
print('RunAgentInput required:', [k for k,v in RunAgentInput.model_json_schema().get('properties',{}).items()])
e = EventEncoder()
print(repr(e.encode(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id='m1', delta='hi'))))
"
```

Expected: prints the error-event class name (e.g. `RunErrorEvent`), the `RunAgentInput` property names, and an SSE-encoded line `data: {...}\n\n`. **Record the error-event class name** — it is used in Task 3's error path (the plan assumes `RunErrorEvent` with `EventType.RUN_ERROR` and a `message` field; if the printout differs, use the printed name/fields there).

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add ag-ui-protocol for token streaming"
```

---

## Task 2: Move reply generation out of the graph; add the streaming generator

This is the core backend refactor. Reply generation leaves the graph's terminal nodes so it can be streamed. Both the streaming endpoint and the non-stream `process_message` build replies from the graph's final state.

**Files:**
- Modify: `app/bot.py` (add `_build_reply_messages`, refactor `generate_bot_reply`, add `generate_bot_reply_stream`)
- Modify: `app/langgraph_flow.py` (`_bot_node`, `_synthesizer_node`, add `run_graph`, update `process_message`)
- Test: `tests/test_agui_stream.py`

- [ ] **Step 1: Write failing tests for the streaming generator and the graph refactor**

Create `tests/test_agui_stream.py`:

```python
"""Streaming reply generator + graph-refactor tests. LLM is mocked."""
from unittest.mock import MagicMock, patch
from uuid import UUID

from tests.conftest import TEST_USER_ID
from app.db import connect


def _content_chunk(text):
    delta = MagicMock()
    delta.content = text
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _seed_conversation(user_id: UUID) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), "2026-06-11T10:00:00"),
        )
        return cur.fetchone()["id"]


def test_generate_bot_reply_stream_yields_deltas():
    from app import bot
    conv = _seed_conversation(TEST_USER_ID)
    with patch("app.bot.client") as mock_client, \
         patch("app.bot._build_reply_messages", return_value=([{"role": "system", "content": "x"}], [])):
        mock_client.chat.completions.create.return_value = iter(
            [_content_chunk("Hello"), _content_chunk(" there")]
        )
        out = list(bot.generate_bot_reply_stream(conv, TEST_USER_ID))
    assert "".join(out) == "Hello there"
    # streaming call was made with stream=True
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs.get("stream") is True


def test_synthesizer_node_only_sets_facts_no_persist():
    from app.langgraph_flow import _synthesizer_node
    conv = _seed_conversation(TEST_USER_ID)
    with patch("app.langgraph_flow.synthesize_response", return_value="FACTS: slept 7h"):
        out = _synthesizer_node({
            "message": "how did i sleep",
            "conversation_id": conv,
            "user_id": str(TEST_USER_ID),
            "graph_result": [{"x": 1}],
            "query_error": "",
            "retry_count": 0,
        })
    assert out["sqlite_context"] == "FACTS: slept 7h"
    assert "final_response" not in out
    # nothing persisted by the node
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=%s", (conv,))
        assert cur.fetchone()["n"] == 0


def test_run_graph_returns_state_without_reply():
    from app import langgraph_flow
    conv = _seed_conversation(TEST_USER_ID)
    with patch("app.langgraph_flow._router_node", return_value={"intent": "journaling"}):
        state = langgraph_flow.run_graph(conv, "had a good day", TEST_USER_ID)
    assert state["intent"] == "journaling"
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=%s", (conv,))
        assert cur.fetchone()["n"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest tests/test_agui_stream.py -v`
Expected: FAIL — `generate_bot_reply_stream`, `run_graph` don't exist; `_synthesizer_node` still persists.

- [ ] **Step 3: Extract the shared reply-message builder in `app/bot.py`**

In `app/bot.py`, add this helper above `generate_bot_reply` (it lifts the context-assembly + system-prompt + history block verbatim from the current `generate_bot_reply` body, lines ~311–353):

```python
def _build_reply_messages(
    conversation_id: int, user_id: UUID, graph_synthesis: Optional[str] = None
) -> tuple[list, list]:
    """Assemble the (messages, tools) for a bot reply. Shared by the
    non-streaming and streaming reply generators so they can't drift."""
    ctx = assemble_bot_context(user_id)
    covered_display = (
        ", ".join(DIMENSION_DISPLAY[d] for d in ctx["covered_today"]) or "(none yet)"
    )
    uncovered_display = (
        ", ".join(DIMENSION_DISPLAY[d] for d in ctx["uncovered_today"]) or "(all six covered)"
    )

    graph_facts_block = ""
    if graph_synthesis and graph_synthesis.strip():
        graph_facts_block = (
            "\nGRAPH_DIGEST (pre-computed from the user's history graph in response to the "
            "user's most recent message — three labeled sections: FACTS, OBSERVATIONS, "
            "SUGGESTIONS).\n\n"
            "How to use it:\n"
            "  - Use FACTS to answer the user's question under your Q&A priority. Do not "
            "dump them verbatim or quote in bullets — extract what actually helps.\n"
            "  - OBSERVATIONS are your advisor lens, not user-facing prose. Lean on them "
            "to inform your tone and angle.\n"
            "  - SUGGESTIONS are CANDIDATES. If one is genuinely relevant and grounded in "
            "the user's question, weave it naturally into your reply — but only ONE, and "
            "only when it adds value. If suggestions feel generic or off-topic, omit them. "
            "Default to omitting.\n"
            "After answering, continue with LISTENER + INTERVIEWER as normal.\n\n"
            "DIGEST:\n"
            + graph_synthesis.strip()
            + "\n"
        )

    system = ASSISTANT_SYSTEM_TMPL.format(
        today_transcript=json.dumps(ctx["today_transcript"], indent=2),
        recent_days_json=json.dumps(ctx["recent_days"], indent=2),
        summary_json=json.dumps(ctx["summary_7day"], indent=2),
        covered_today=covered_display,
        uncovered_today=uncovered_display,
        graph_facts_block=graph_facts_block,
    )

    messages = [{"role": "system", "content": system}]
    for m in fetch_chat_history(conversation_id, user_id):
        messages.append({"role": m["role"], "content": m["content"]})

    return messages, _goal_tools()
```

- [ ] **Step 4: Refactor `generate_bot_reply` to use the helper**

Replace the body of `generate_bot_reply` (everything from `ctx = assemble_bot_context(...)` through the `tools = _goal_tools()` line, i.e. lines ~311–353) with a single call, leaving the tool-dispatch loop below it unchanged:

```python
    messages, tools = _build_reply_messages(conversation_id, user_id, graph_synthesis)

    # Multi-round tool dispatch. Cap at 2 rounds so the model can't loop ...
    for _ in range(2):
```

(The `for _ in range(2):` loop and the fallback call that follow stay exactly as they are.)

- [ ] **Step 5: Add `generate_bot_reply_stream` in `app/bot.py`**

Add after `generate_bot_reply`:

```python
def generate_bot_reply_stream(
    conversation_id: int, user_id: UUID, graph_synthesis: Optional[str] = None
):
    """Streaming twin of generate_bot_reply. Yields text deltas.

    Goal-tool rounds carry no user-facing text, so they run the same as the
    non-streaming path (accumulate tool-call args, dispatch, loop). Only the
    text-producing round is streamed token-by-token. The common journaling
    case (no tools) streams from the first chunk.
    """
    messages, tools = _build_reply_messages(conversation_id, user_id, graph_synthesis)

    for _ in range(2):
        stream = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=0.7,
            stream=True,
        )
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            for tc in (getattr(delta, "tool_calls", None) or []):
                acc = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    acc["id"] = tc.id
                if tc.function and tc.function.name:
                    acc["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    acc["arguments"] += tc.function.arguments
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                yield delta.content

        if not tool_acc:
            return  # text round complete — all deltas already yielded

        # Tool round: replay the assistant tool-call turn, dispatch, loop.
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": [
                {
                    "id": a["id"],
                    "type": "function",
                    "function": {"name": a["name"], "arguments": a["arguments"]},
                }
                for a in tool_acc.values()
            ],
        })
        for a in tool_acc.values():
            result = _dispatch_goal_tool(a["name"], a["arguments"], user_id)
            messages.append({
                "role": "tool",
                "tool_call_id": a["id"],
                "content": json.dumps(result, default=str),
            })

    # Fallback: force a final text reply (no tools), still streamed.
    stream = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
            yield chunk.choices[0].delta.content
```

- [ ] **Step 6: Gut reply-gen from the graph terminal nodes in `app/langgraph_flow.py`**

Replace `_bot_node` (lines ~71–75):

```python
def _bot_node(state: GraphState) -> dict:
    # Journaling needs no graph work beyond routing. The reply is generated
    # outside the graph (streamed by the endpoint, or by process_message).
    return {}
```

Replace `_synthesizer_node` (lines ~138–150):

```python
def _synthesizer_node(state: GraphState) -> dict:
    """Digest the graph result into facts. Reply generation happens outside
    the graph so it can be streamed."""
    failed = bool(state.get("query_error")) and state.get("retry_count", 0) >= 3
    facts = synthesize_response(
        user_message=state["message"],
        graph_result=state.get("graph_result", []),
        failed=failed,
    )
    return {"sqlite_context": facts}
```

- [ ] **Step 7: Add `run_graph` and update `process_message` in `app/langgraph_flow.py`**

Replace `process_message` (lines ~204–223) with:

```python
def run_graph(conversation_id: int, message_content: str, user_id: UUID) -> dict:
    """Run routing + retrieval WITHOUT generating the reply. Returns the final
    GraphState dict (carries `intent` and, for analytical, `sqlite_context`)."""
    initial_state: GraphState = {
        "message": message_content,
        "conversation_id": conversation_id,
        "user_id": str(user_id),
        "intent": "",
        "sqlite_context": "",
        "cypher_query": "",
        "graph_result": [],
        "query_error": "",
        "retry_count": 0,
        "eval_retry_count": 0,
        "eval_passed": False,
        "eval_hint": "",
        "search_history": [],
        "final_response": "",
    }
    return _graph.invoke(initial_state)


def process_message(conversation_id: int, message_content: str, user_id: UUID) -> str:
    """Non-streaming entrypoint (background-task / tests). Runs the graph then
    generates + persists the reply."""
    state = run_graph(conversation_id, message_content, user_id)
    facts = state.get("sqlite_context") or None
    reply = generate_bot_reply(conversation_id, user_id, graph_synthesis=facts)
    store_assistant_message(conversation_id, reply, user_id)
    return reply
```

- [ ] **Step 8: Run the new tests + the existing flow tests**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest tests/test_agui_stream.py tests/test_langgraph_flow.py -v`
Expected: PASS (all). If `test_langgraph_flow` had a test asserting `_synthesizer_node`/`_bot_node` persist or set `final_response`, update it to the new contract (facts-only / empty).

- [ ] **Step 9: Run the full backend suite to catch regressions**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest -q`
Expected: PASS. (`process_message` keeps its old observable behavior — graph runs, reply persisted — so `test_bot_goal_tools`, isolation, etc. stay green.)

- [ ] **Step 10: Commit**

```bash
git add app/bot.py app/langgraph_flow.py tests/test_agui_stream.py
git commit -m "refactor: move reply generation out of the graph; add streaming reply generator"
```

---

## Task 3: New AG-UI streaming endpoint

**Files:**
- Create: `app/routers/agui.py`
- Modify: `main.py`
- Test: `tests/test_agui_stream.py` (append)

- [ ] **Step 1: Write the failing endpoint test**

Append to `tests/test_agui_stream.py`:

```python
def _agui_payload(text: str) -> dict:
    # AG-UI wire format is camelCase (RunAgentInput aliases).
    return {
        "threadId": "t-1",
        "runId": "r-1",
        "state": {},
        "messages": [{"id": "m-1", "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def test_agui_endpoint_streams_events_and_persists():
    from fastapi.testclient import TestClient
    import main
    conv = _seed_conversation(TEST_USER_ID)
    with patch("app.routers.agui.run_graph", return_value={"intent": "journaling", "sqlite_context": ""}), \
         patch("app.routers.agui.generate_bot_reply_stream", return_value=iter(["Hi", " Jerry"])):
        client = TestClient(main.app)
        body = b""
        with client.stream("POST", f"/api/conversations/{conv}/agui", json=_agui_payload("hello")) as resp:
            assert resp.status_code == 200
            for chunk in resp.iter_bytes():
                body += chunk
    text = body.decode()
    assert "RUN_STARTED" in text
    assert "TEXT_MESSAGE_START" in text
    assert "TEXT_MESSAGE_CONTENT" in text
    assert "TEXT_MESSAGE_END" in text
    assert "RUN_FINISHED" in text
    # user + assistant rows persisted
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id", (conv,))
        rows = cur.fetchall()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "Hi Jerry"
```

- [ ] **Step 2: Run it to verify failure**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest tests/test_agui_stream.py::test_agui_endpoint_streams_events_and_persists -v`
Expected: FAIL — route 404 (router not wired yet).

- [ ] **Step 3: Create `app/routers/agui.py`**

> If Task 1 Step 3 printed a different error-event class than `RunErrorEvent`, substitute that name + its required field in the `except` block.

```python
"""AG-UI streaming chat endpoint.

Replaces the old POST /messages + client polling. The frontend's @ag-ui/client
HttpAgent POSTs a RunAgentInput here and receives an SSE stream of AG-UI events.
The LangGraph machine runs to produce routing + synthesis facts; the final reply
is streamed token-by-token via generate_bot_reply_stream.

Sync generator on purpose: Starlette iterates it in a threadpool, so the pooled
psycopg connection and the sync LangGraph machine stay on a worker thread
(matches the repo's "no async for the bot reply / LangGraph is sync" rule).
"""
import uuid
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ag_ui.core import (
    RunAgentInput,
    EventType,
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
)
from ag_ui.encoder import EventEncoder

from ..auth import get_current_user_id
from ..bot import generate_bot_reply_stream, store_assistant_message
from ..db import connect
from ..langgraph_flow import run_graph


router = APIRouter(prefix="/api/conversations/{conv_id}/agui", tags=["agui"])

_APOLOGY = "Hmm, I had trouble responding just now. Want to try saying that again?"


def _persist_user_message(conv_id: int, content: str, user_id: UUID) -> None:
    created_at = datetime.now().isoformat()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (user_id, conversation_id, role, content, created_at)"
            " VALUES (%s, %s, 'user', %s, %s)",
            (str(user_id), conv_id, content, created_at),
        )


@router.post("")
async def agui_run(
    conv_id: int,
    input_data: RunAgentInput,
    request: Request,
    user_id: UUID = Depends(get_current_user_id),
):
    # Ownership guard.
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM conversations WHERE id = %s AND user_id = %s",
            (conv_id, str(user_id)),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Conversation not found")

    user_text = (input_data.messages[-1].content or "").strip() if input_data.messages else ""
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    encoder = EventEncoder(accept=request.headers.get("accept"))
    thread_id = input_data.thread_id
    run_id = input_data.run_id

    def event_stream():
        yield encoder.encode(RunStartedEvent(
            type=EventType.RUN_STARTED, thread_id=thread_id, run_id=run_id))

        # User message first, so context assembly sees it (preserves ordering).
        _persist_user_message(conv_id, user_text, user_id)

        message_id = str(uuid.uuid4())
        full: list[str] = []
        try:
            state = run_graph(conv_id, user_text, user_id)
            facts = state.get("sqlite_context") or None

            yield encoder.encode(TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START, message_id=message_id, role="assistant"))
            for delta in generate_bot_reply_stream(conv_id, user_id, graph_synthesis=facts):
                if not delta:
                    continue
                full.append(delta)
                yield encoder.encode(TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT, message_id=message_id, delta=delta))
            yield encoder.encode(TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END, message_id=message_id))

            store_assistant_message(conv_id, "".join(full).strip() or _APOLOGY, user_id)
        except Exception as exc:  # noqa: BLE001 — stream a graceful error, persist apology.
            print(f"[agui] stream error for conv {conv_id}: {exc}")
            yield encoder.encode(RunErrorEvent(type=EventType.RUN_ERROR, message=str(exc)))
            if not full:
                store_assistant_message(conv_id, _APOLOGY, user_id)

        yield encoder.encode(RunFinishedEvent(
            type=EventType.RUN_FINISHED, thread_id=thread_id, run_id=run_id))

    return StreamingResponse(event_stream(), media_type=encoder.get_content_type())
```

- [ ] **Step 4: Wire the router in `main.py`**

Change the import line (`main.py:9`):

```python
from app.routers import admin, agui, conversations, dashboard, goals, messages
```

Add after `app.include_router(messages.router)` (`main.py:28`):

```python
app.include_router(agui.router)
```

- [ ] **Step 5: Run the endpoint test**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest tests/test_agui_stream.py::test_agui_endpoint_streams_events_and_persists -v`
Expected: PASS. If it 422s on the body, run the Task 1 Step 3 schema printout and align `_agui_payload` field names with `RunAgentInput`'s required properties.

- [ ] **Step 6: Run the full suite**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/agui.py main.py tests/test_agui_stream.py
git commit -m "feat: AG-UI streaming chat endpoint"
```

---

## Task 4: Frontend — stream via @ag-ui/client, remove polling

**Files:**
- Modify: `journal-frontend/package.json` (via npm)
- Modify: `journal-frontend/src/App.jsx`

- [ ] **Step 1: Install the client SDK**

Run: `cd journal-frontend && npm i @ag-ui/client @ag-ui/core`
Expected: both added to `dependencies`.

- [ ] **Step 2: Add a streaming import at the top of `App.jsx`**

After `import { supabase } from './supabase';` (line 2):

```javascript
import { HttpAgent } from '@ag-ui/client';
import { EventType } from '@ag-ui/core';
```

- [ ] **Step 3: Add a token helper near `apiFetch` (App.jsx ~line 22)**

```javascript
async function getAccessToken() {
  if (!SUPABASE_CONFIGURED) return null;
  const { data: { session } } = await supabase.auth.getSession();
  return session?.access_token ?? null;
}
```

- [ ] **Step 4: Replace the POST-then-poll block in `sendMessage` (App.jsx lines ~311–343)**

The `/agui` endpoint persists the user message itself, so the old `POST /messages` call must go (otherwise the user message is stored twice). Replace the **entire** `try { ... }` body — from `const res = await apiFetch(\`${API}/api/conversations/${convId}/messages\`, {` (line ~312) through the poll's closing `}, 1500);` (line ~343) — with the streaming subscription below. Keep the surrounding `try {` and the existing `} catch { ... }` (lines ~344–347) that removes the optimistic bubble on failure:

```javascript
      // Stream the assistant reply over AG-UI. The endpoint persists both the
      // user message and the assistant reply, so we don't POST /messages here;
      // the optimistic user bubble is reconciled by loadMessages on complete.
      const token = await getAccessToken();
      const agent = new HttpAgent({
        url: `${API}/api/conversations/${convId}/agui`,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      agent.messages = [{ id: `u-${Date.now()}`, role: 'user', content: text }];

      const streamId = `stream-${Date.now()}`;
      let acc = '';
      let started = false;

      agent.runAgent().subscribe({
        next: (event) => {
          if (event.type === EventType.TEXT_MESSAGE_CONTENT) {
            acc += event.delta || '';
            if (!started) {
              started = true;
              setIsWaiting(false); // first token: swap spinner for live text
              setMessages(prev => [
                ...prev,
                { id: streamId, role: 'assistant', content: acc, created_at: new Date().toISOString() },
              ]);
            } else {
              setMessages(prev => prev.map(m => m.id === streamId ? { ...m, content: acc } : m));
            }
          }
        },
        error: () => {
          setIsWaiting(false);
          if (!started) {
            setMessages(prev => [
              ...prev,
              { id: streamId, role: 'assistant', content: '(Something went wrong streaming the reply.)', created_at: new Date().toISOString() },
            ]);
          }
        },
        complete: () => {
          setIsWaiting(false);
          // Reconcile optimistic/streamed bubbles with persisted rows.
          loadMessages(convId);
          fetchConversations();
        },
      });
```

- [ ] **Step 5: Remove the now-unused poll machinery**

- Delete the `const pollRef = useRef(null);` line (App.jsx:121).
- Delete the cleanup effect `useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);` (App.jsx:208).

- [ ] **Step 6: Build + lint**

Run: `cd journal-frontend && npm run build && npm run lint`
Expected: build succeeds; lint clean (no `pollRef`/unused-var errors).

- [ ] **Step 7: Commit**

```bash
git add journal-frontend/package.json journal-frontend/package-lock.json journal-frontend/src/App.jsx
git commit -m "feat: stream chat replies via @ag-ui/client; remove polling"
```

---

## Task 5: Live verification

**Files:** none (manual run).

- [ ] **Step 1: Start the backend**

Run (background): `DATABASE_URL=postgresql://localhost:5432/mindforge_dev python main.py`
Expected: Uvicorn on `127.0.0.1:8000`; logs show `init_db` / graph indexes.

- [ ] **Step 2: Start the frontend**

Run (background): `cd journal-frontend && npm run dev`
Expected: Vite on `http://localhost:5173`.

- [ ] **Step 3: Observe streaming**

In the browser: send a journaling message ("slept badly, lots of coffee") and confirm the reply renders progressively, not all-at-once. Then send an analytical question ("what patterns do you see in my sleep this week?") and confirm the spinner holds during graph work, then the reply streams.

- [ ] **Step 4: Confirm persistence**

Reload the page; confirm both messages persisted and match what streamed.

---

## Task 6: Finish

- [ ] **Step 1: Full backend suite green**

Run: `DATABASE_URL=postgresql://localhost:5432/mindforge_dev pytest -q`
Expected: PASS.

- [ ] **Step 2: Final commit if anything outstanding**

```bash
git add -A && git commit -m "chore: AG-UI streaming finishing touches" || echo "nothing to commit"
```

---

## Notes / risks

- **Heartbeat (spec §"Connection keepalive"):** the analytical path can be silent ~20–40s before the first token. The current design relies on `RUN_STARTED` going out immediately and the run finishing within Render's streaming tolerance. If live testing on Render shows idle-connection drops, add a heartbeat: run `run_graph` in a `threading.Thread` and, while waiting, `yield ": ping\\n\\n"` (a raw SSE comment the AG-UI parser ignores) every ~10s. Kept out of the core tasks to avoid threading complexity unless verification shows it's needed.
- **Streaming + tool calls:** only the text round streams; goal-tool rounds run non-streamed. Mixed content+tool_calls in one round is rare for gpt-4o and tolerated (a short preamble could stream before a tool dispatch).
- **Old path:** backend `POST /messages` route is left in place but unused by the chat UI (the frontend now only streams). It can be deleted later; leaving it avoids touching unrelated tests.
