# AG-UI Token Streaming — Design

**Date:** 2026-06-11
**Status:** Approved design, pending spec review → implementation plan
**Owner:** Jerry

## Problem

The live chat path does not stream. Today:

1. `POST /api/conversations/{conv_id}/messages` persists the user message and enqueues a fire-and-forget `BackgroundTask` (`process_message_background`) that runs the LangGraph machine and writes the assistant reply to Postgres.
2. The frontend shows a typing dot and **polls `GET /messages` every 1.5s for up to 45s** (`App.jsx:326`) until a new assistant row appears, then drops the full reply in at once.

For the analytical / GraphRAG path (router → cypher → execute → evaluate → synthesize, several gpt-4o calls), that dot can sit blank for tens of seconds. There is no token-by-token feedback anywhere.

## Goal

Replace the poll loop with **live token streaming** over the **AG-UI protocol**, using the official SDKs (`ag-ui-protocol` on the backend, `@ag-ui/client` on the frontend). Scope is deliberately **token streaming only** — the assistant reply appears progressively. The analytical path keeps doing its multi-step work silently behind the existing spinner, then streams its final reply token-by-token, identical to the journaling path. No per-step "agent thinking" UI in this scope (that would be a later, larger change; the wire protocol leaves room for it).

### Non-goals

- Surfacing GraphRAG intermediate steps (routing/querying/synthesizing) as user-visible events.
- Streaming tool-call arguments to the UI.
- Any change to the nightly batch, morning brief, dashboard, or goals slash-commands.

## Key structural fact

Both chat paths converge on `generate_bot_reply()`:
- `_bot_node` (journaling) — `langgraph_flow.py:71` → `generate_bot_reply(conv, uid)` then `store_assistant_message(...)`.
- `_synthesizer_node` (analytical) — `langgraph_flow.py:138` → `synthesize_response(...)` then `generate_bot_reply(conv, uid, graph_synthesis=facts)` then `store_assistant_message(...)`.

That convergence point is the single seam where token streaming plugs in. The refactor moves reply generation *out* of the graph's terminal nodes so it can be streamed by the endpoint.

## Approach (chosen)

**Dedicated AG-UI run endpoint, replacing the poll loop entirely.** A new `POST /api/conversations/{conv_id}/agui` returns a `StreamingResponse` of AG-UI events. The LangGraph machine runs only to produce *routing + synthesis facts*; the final reply is produced by a new streaming variant of `generate_bot_reply` whose deltas the endpoint wraps in `TEXT_MESSAGE_CONTENT` events. This matches how `HttpAgent` + `EventEncoder` are designed to interoperate (verified against current AG-UI docs).

Rejected alternatives:
- **Convert `POST /messages` into a stream** — its body shape (`{content}`) does not match `RunAgentInput` that `HttpAgent` POSTs; fights the SDK.
- **Two endpoints (POST to start + SSE GET)** — extra moving parts; only needed when you can't stream from a POST. We can.

The old `POST /messages` + polling path is **removed cleanly**: the frontend poll loop is deleted and chat goes only through the AG-UI stream. `GET /messages` stays (still needed to load history on conversation switch). The `POST /messages` route may be deleted or left unused/deprecated; the chat path no longer calls it.

## Components & data flow

### 1. Backend — new streaming endpoint

New router (e.g. `app/routers/agui.py`), wired in `main.py`.

- `POST /api/conversations/{conv_id}/agui`
- Auth: `Depends(get_current_user_id)` — Bearer JWT in the `Authorization` header. `HttpAgent` sets custom headers via `fetch` (not `EventSource`), so this works.
- Validate conversation ownership (same `SELECT id FROM conversations WHERE id=%s AND user_id=%s` guard as the current POST).
- Body: AG-UI `RunAgentInput`. The new user message text is `input.messages[-1].content`.
- Returns `StreamingResponse(event_generator, media_type=encoder.get_content_type())`.

`event_generator` is a **sync** generator (Starlette iterates sync generators in a threadpool, so `psycopg` pooled connections and the sync LangGraph machine stay on a worker thread — honoring the "no async for bot reply / LangGraph is sync" rule in CLAUDE.md):

1. Persist the user message (`role='user'`) **first**, so `fetch_chat_history` / `assemble_bot_context` see it (preserves today's bucket ordering, matching the current behavior where `POST /messages` stored the user row before the background task ran).
2. `yield encoder.encode(RunStartedEvent(thread_id, run_id))`.
3. Run the LangGraph pipeline to get `intent` and synthesis `facts` (None for journaling). This is the silent "thinking" phase.
4. `yield TextMessageStartEvent(message_id, role="assistant")`.
5. `for delta in generate_bot_reply_stream(conv_id, uid, graph_synthesis=facts): accumulate; yield TextMessageContentEvent(message_id, delta)`.
6. `yield TextMessageEndEvent(message_id)`.
7. `store_assistant_message(conv_id, full_text, uid)`.
8. `yield RunFinishedEvent(thread_id, run_id)`.

Error handling: wrap steps 3–7 in try/except. On exception, `yield RunErrorEvent(message=...)`, then persist the existing apology fallback (`"Hmm, I had trouble responding just now…"`) so the conversation isn't left empty, then finish the stream.

Imports (verified against current SDK):
```python
from ag_ui.core import (
    RunAgentInput, EventType,
    RunStartedEvent, RunFinishedEvent, RunErrorEvent,
    TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent,
)
from ag_ui.encoder import EventEncoder
```

### 2. Backend — graph refactor (`langgraph_flow.py`, `bot.py`)

- Terminal nodes stop generating and persisting the reply:
  - `_synthesizer_node` writes only the synthesis facts into state (`sqlite_context`); it no longer calls `generate_bot_reply` / `store_assistant_message`.
  - Journaling routes through without an LLM reply call inside the graph.
- Reply generation moves out of the graph into the endpoint (streaming) and into the non-stream `process_message` wrapper (for back-compat/tests).
- New `generate_bot_reply_stream(conversation_id, user_id, graph_synthesis=None) -> Iterator[str]` in `bot.py`:
  - Shares the prompt/context assembly with `generate_bot_reply` via a single extracted helper (e.g. `_build_reply_messages(...)`) so the two cannot drift.
  - Runs the goal-tool dispatch rounds **non-streamed** (tool-call rounds carry no user-facing text), exactly as today (`tool_choice="auto"`, capped at 2 rounds).
  - Streams only the final text-producing call with `stream=True`, yielding `delta.content` chunks. Detection: if a round's early chunks carry `delta.tool_calls`, treat it as a tool round (accumulate args, dispatch, loop, do not yield); the first round that produces `delta.content` is the text round and is streamed. The common journaling case (no tools) streams from the first chunk.
- `process_message` (non-stream entrypoint) keeps working: after `_graph.invoke`, it calls `generate_bot_reply(...)` using `state["intent"]` / `state["sqlite_context"]` and `store_assistant_message(...)`. This preserves any tests and callers that exercise the old path.

### 3. Frontend (`App.jsx`)

- Add deps: `@ag-ui/client`, `@ag-ui/core`.
- `sendMessage`:
  - Keep the optimistic user bubble and the lazy conversation creation.
  - Delete the `setInterval` poll block (`App.jsx:323–343`) and the `pollRef` machinery.
  - Build `const agent = new HttpAgent({ url: \`${API}/api/conversations/${convId}/agui\`, headers: { Authorization: \`Bearer ${token}\` } })`, where `token` is the current Supabase access token (read the same way `apiFetch` reads it today).
  - Set `agent.messages = [{ id, role: 'user', content: text }]` and `agent.runAgent().subscribe({ next, error, complete })`.
  - On `TEXT_MESSAGE_START`: create a live assistant bubble (empty). On `TEXT_MESSAGE_CONTENT`: append `event.delta` to that bubble's content (React state update). On `RUN_FINISHED` / `complete`: finalize, `setIsWaiting(false)`, `fetchConversations()`. On `error`: surface a friendly inline message and `setIsWaiting(false)`.
  - `isWaiting` (spinner) is true from send until the first `TEXT_MESSAGE_CONTENT`; after that the growing text is the feedback.
- `loadMessages` stays for loading history on conversation switch; the optimistic + streamed bubbles reconcile with persisted rows on the next `loadMessages`.
- Goal slash-commands (`/goal …`) remain fully client-side and never touch the agui endpoint.

### 4. Connection keepalive

The analytical path may be silent ~20–40s before the first token. To keep intermediaries from dropping an idle SSE connection, emit a lightweight heartbeat during the thinking phase — an SSE comment line (`: ping\n\n`) or a benign event every ~10s. This is a keepalive only; it carries no per-step UI and does not expand scope.

## Testing

- **Backend (pytest, mocked LLM/graph per existing patterns):**
  - POST `/agui` for a **journaling** message asserts the encoded SSE sequence `RUN_STARTED → TEXT_MESSAGE_START → TEXT_MESSAGE_CONTENT(>=1) → TEXT_MESSAGE_END → RUN_FINISHED`, and that exactly one assistant row is persisted with the concatenated text.
  - POST `/agui` for an **analytical** message asserts the same sequence after the (mocked) GraphRAG pipeline, with synthesis facts threaded into the reply call.
  - Error path: a forced exception mid-stream emits `RUN_ERROR` and persists the apology fallback row.
- **Frontend:** manual live verification via `npm run dev` — send a journaling message and an analytical question, confirm tokens appear progressively and the persisted reply matches on reload.

## Risks & mitigations

- **Streaming + tool calls interleaving** — mitigated by streaming only the final text round and keeping tool rounds non-streamed; the detection rule handles the rare goal-tool case.
- **Idle-connection drops on the analytical path** — mitigated by the ~10s heartbeat.
- **Render free tier / proxy SSE behavior** — `StreamingResponse` with `text/event-stream` is standard; the heartbeat covers idle gaps. Verify on the live Render URL after deploy.
- **Context drift between streamed and non-streamed reply builders** — mitigated by sharing one `_build_reply_messages` helper.

## Out of scope / future

- Scope 2 ("agentic transparency"): emit `TOOL_CALL_*` / custom step events so the UI can show "searching your graph", etc. The wire protocol and endpoint shape chosen here extend to that without a rewrite.
- Scope 3: adopt `@ag-ui/langgraph` / full SDK-managed state sync.
