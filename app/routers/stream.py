"""Plain-SSE streaming chat endpoint for native clients (React Native).

The web app uses the AG-UI protocol (`app/routers/agui.py`). Native clients
consume a simpler `text/event-stream` with named events carrying JSON payloads,
which `react-native-sse` reads directly via addEventListener:

  event: retrieval   data: {"phase": "start"|"end"}
  event: delta       data: {"text": "<token>"}
  event: done        data: {}
  event: error       data: {"message": "..."}

It reuses the EXACT same pipeline as the AG-UI endpoint — persist the user
message, run the LangGraph machine, stream the reply token-by-token, persist
the assistant reply — so the two transports can't drift. Sync generator on
purpose: Starlette iterates it in a threadpool, keeping the pooled psycopg
connection and the sync LangGraph machine on a worker thread (matches the
repo's "no async for the bot reply / LangGraph is sync" rule).
"""
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import get_current_user_id
from ..bot import generate_bot_reply_stream, store_assistant_message
from ..db import connect
from ..langgraph_flow import run_graph_streaming
from ..models import MessageCreate


router = APIRouter(prefix="/api/conversations/{conv_id}/stream", tags=["stream"])

_APOLOGY = "Hmm, I had trouble responding just now. Want to try saying that again?"


def _sse(event: str, data: dict) -> str:
    # JSON-encode the payload onto a single data line so token text containing
    # newlines can't break the SSE framing.
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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
async def stream_run(
    conv_id: int,
    body: MessageCreate,
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

    user_text = (body.content or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    def event_stream():
        # User message first, so context assembly sees it (preserves ordering).
        _persist_user_message(conv_id, user_text, user_id)

        full: list[str] = []
        try:
            state = {}
            for item in run_graph_streaming(conv_id, user_text, user_id):
                if item == "retrieval_start":
                    yield _sse("retrieval", {"phase": "start"})
                elif item == "retrieval_end":
                    yield _sse("retrieval", {"phase": "end"})
                elif isinstance(item, tuple) and item[0] == "final":
                    state = item[1]
            facts = state.get("sqlite_context") or None

            for delta in generate_bot_reply_stream(conv_id, user_id, graph_synthesis=facts):
                if not delta:
                    continue
                full.append(delta)
                yield _sse("delta", {"text": delta})

            store_assistant_message(conv_id, "".join(full).strip() or _APOLOGY, user_id)
            yield _sse("done", {})
        except Exception as exc:  # noqa: BLE001 — stream a graceful error, persist apology.
            print(f"[stream] error for conv {conv_id}: {exc}")
            if not full:
                store_assistant_message(conv_id, _APOLOGY, user_id)
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
