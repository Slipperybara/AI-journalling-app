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
    StepStartedEvent,
    StepFinishedEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
)
from ag_ui.encoder import EventEncoder

from ..auth import get_current_user_id
from ..bot import generate_bot_reply_stream, store_assistant_message
from ..db import connect
from ..langgraph_flow import run_graph_streaming


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
            # Run the graph, surfacing the retrieval phase as STEP events so the
            # UI can react (e.g. tint cool while searching the knowledge graph).
            state = {}
            for item in run_graph_streaming(conv_id, user_text, user_id):
                if item == "retrieval_start":
                    yield encoder.encode(StepStartedEvent(
                        type=EventType.STEP_STARTED, step_name="retrieval"))
                elif item == "retrieval_end":
                    yield encoder.encode(StepFinishedEvent(
                        type=EventType.STEP_FINISHED, step_name="retrieval"))
                elif isinstance(item, tuple) and item[0] == "final":
                    state = item[1]
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
