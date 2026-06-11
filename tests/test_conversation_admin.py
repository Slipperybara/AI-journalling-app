"""Rename + soft-delete (archive) of conversations.

Archiving must hide the conversation from the listing but keep its messages,
so the nightly parser and knowledge graph keep referencing them.
"""
from tests.conftest import TEST_USER_ID
from app.db import connect


def _seed_conversation(user_id, started_at="2026-06-11T10:00:00"):
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO conversations (user_id, started_at) VALUES (%s, %s) RETURNING id",
            (str(user_id), started_at),
        )
        cid = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO messages (user_id, conversation_id, role, content, created_at)"
            " VALUES (%s, %s, 'user', 'hello', %s)",
            (str(user_id), cid, started_at),
        )
        return cid


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


def test_rename_conversation_sets_title():
    client = _client()
    try:
        cid = _seed_conversation(TEST_USER_ID)
        r = client.patch(f"/api/conversations/{cid}", json={"title": "Morning Pages"})
        assert r.status_code == 200
        listing = client.get("/api/conversations").json()
        row = next(c for c in listing if c["id"] == cid)
        assert row["title"] == "Morning Pages"
    finally:
        _clear_override()


def test_archive_hides_conversation_but_keeps_messages():
    client = _client()
    try:
        cid = _seed_conversation(TEST_USER_ID)
        r = client.delete(f"/api/conversations/{cid}")
        assert r.status_code == 200
        listing = client.get("/api/conversations").json()
        assert all(c["id"] != cid for c in listing)
        # Messages remain in the DB (still referenced by the parser/graph).
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id = %s", (cid,))
            assert cur.fetchone()["n"] == 1
    finally:
        _clear_override()
