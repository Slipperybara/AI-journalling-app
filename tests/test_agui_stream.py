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
    from app.auth import get_current_user_id
    import main
    conv = _seed_conversation(TEST_USER_ID)
    main.app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    try:
        with patch("app.routers.agui.run_graph", return_value={"intent": "journaling", "sqlite_context": ""}), \
             patch("app.routers.agui.generate_bot_reply_stream", return_value=iter(["Hi", " Jerry"])):
            client = TestClient(main.app)
            body = b""
            with client.stream("POST", f"/api/conversations/{conv}/agui", json=_agui_payload("hello")) as resp:
                assert resp.status_code == 200
                for chunk in resp.iter_bytes():
                    body += chunk
    finally:
        main.app.dependency_overrides.pop(get_current_user_id, None)
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
