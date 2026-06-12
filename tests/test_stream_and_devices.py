"""Tests for the native-client backend additions:
  - POST /api/conversations/{id}/stream — plain-SSE streaming chat
  - POST /api/devices/register — push device-token registration

LLM/graph are mocked; Postgres is real (conftest TRUNCATEs between tests).
"""
from unittest.mock import patch

from app.db import connect
from tests.conftest import TEST_USER_ID, TEST_USER_ID_B

UID = str(TEST_USER_ID)


def _seed_conversation(user_id) -> int:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), "2026-06-11T10:00:00"),
        )
        return cur.fetchone()["id"]


def _client():
    from fastapi.testclient import TestClient
    from app.auth import get_current_user_id
    import main
    main.app.dependency_overrides[get_current_user_id] = lambda: TEST_USER_ID
    return TestClient(main.app)


def _clear_override():
    from app.auth import get_current_user_id
    import main
    main.app.dependency_overrides.pop(get_current_user_id, None)


# ── plain-SSE streaming ──────────────────────────────────────────────────────

def test_stream_emits_deltas_and_persists():
    conv = _seed_conversation(TEST_USER_ID)
    client = _client()
    try:
        with patch("app.routers.stream.run_graph_streaming",
                   return_value=iter([("final", {"intent": "journaling", "sqlite_context": ""})])), \
             patch("app.routers.stream.generate_bot_reply_stream",
                   return_value=iter(["Hi", " Jerry"])):
            body = b""
            with client.stream("POST", f"/api/conversations/{conv}/stream",
                               json={"content": "hello"}) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                for chunk in resp.iter_bytes():
                    body += chunk
    finally:
        _clear_override()

    text = body.decode()
    assert "event: delta" in text
    assert '"text": "Hi"' in text
    assert "event: done" in text

    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id", (conv,))
        rows = cur.fetchall()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "Hi Jerry"


def test_stream_emits_retrieval_events_for_analytical():
    conv = _seed_conversation(TEST_USER_ID)
    client = _client()
    try:
        with patch("app.routers.stream.run_graph_streaming",
                   return_value=iter(["retrieval_start", "retrieval_end",
                                      ("final", {"intent": "analytical", "sqlite_context": "FACTS: slept 7h"})])), \
             patch("app.routers.stream.generate_bot_reply_stream",
                   return_value=iter(["You", " slept well"])):
            body = b""
            with client.stream("POST", f"/api/conversations/{conv}/stream",
                               json={"content": "how did i sleep?"}) as resp:
                for chunk in resp.iter_bytes():
                    body += chunk
    finally:
        _clear_override()

    text = body.decode()
    assert 'event: retrieval' in text
    assert '"phase": "start"' in text
    assert '"phase": "end"' in text


def test_stream_404_for_other_users_conversation():
    other = _seed_conversation(TEST_USER_ID_B)
    client = _client()
    try:
        resp = client.post(f"/api/conversations/{other}/stream", json={"content": "hi"})
    finally:
        _clear_override()
    assert resp.status_code == 404


def test_stream_400_on_empty_message():
    conv = _seed_conversation(TEST_USER_ID)
    client = _client()
    try:
        resp = client.post(f"/api/conversations/{conv}/stream", json={"content": "   "})
    finally:
        _clear_override()
    assert resp.status_code == 400


# ── device-token registration ────────────────────────────────────────────────

def test_register_device_token_upserts():
    client = _client()
    try:
        r1 = client.post("/api/devices/register", json={"token": "ExpoTok[abc]", "platform": "ios"})
        assert r1.status_code == 200 and r1.json()["status"] == "registered"
        # Re-register same token updates, doesn't duplicate.
        r2 = client.post("/api/devices/register", json={"token": "ExpoTok[abc]", "platform": "ios"})
        assert r2.json()["status"] == "registered"
    finally:
        _clear_override()

    with connect() as conn:
        rows = conn.execute(
            "SELECT token, platform FROM device_tokens WHERE user_id = %s", (UID,)
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["token"] == "ExpoTok[abc]" and rows[0]["platform"] == "ios"


def test_register_empty_token_ignored():
    client = _client()
    try:
        r = client.post("/api/devices/register", json={"token": "  "})
        assert r.json()["status"] == "ignored"
    finally:
        _clear_override()
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM device_tokens WHERE user_id = %s", (UID,)).fetchone()["n"]
    assert n == 0
