"""Personalized first-greeting endpoint (POST /api/conversations/welcome).

A brand-new user's first conversation is seeded with a warm greeting grounded in
their onboarding profile. The endpoint is idempotent — once a conversation exists
it never double-greets.
"""
from tests.conftest import TEST_USER_ID
from app.db import connect
from app.profile import upsert_profile


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


def _messages(conv_id):
    with connect() as conn:
        return conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = %s ORDER BY id",
            (conv_id,),
        ).fetchall()


def test_welcome_no_profile_uses_fallback_and_creates_conversation():
    """With no onboarding profile, no LLM call happens — a deterministic warm
    fallback is posted as the first assistant message."""
    client = _client()
    try:
        r = client.post("/api/conversations/welcome")
        assert r.status_code == 200
        body = r.json()
        assert body["created"] is True
        msgs = _messages(body["id"])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert "JAI" in msgs[0]["content"]
    finally:
        _clear_override()


def test_welcome_is_idempotent():
    """A second call (or a call when a conversation already exists) returns the
    existing conversation and posts no new greeting."""
    client = _client()
    try:
        first = client.post("/api/conversations/welcome").json()
        second = client.post("/api/conversations/welcome").json()
        assert second["created"] is False
        assert second["id"] == first["id"]
        # No duplicate conversation, no second greeting.
        with connect() as conn:
            n_convs = conn.execute(
                "SELECT COUNT(*) AS n FROM conversations WHERE user_id = %s",
                (str(TEST_USER_ID),),
            ).fetchone()["n"]
        assert n_convs == 1
        assert len(_messages(first["id"])) == 1
    finally:
        _clear_override()


def test_welcome_uses_profile_for_personalized_greeting(monkeypatch):
    """With a profile present, the greeting is generated from it (LLM stubbed)."""
    import app.intro_greeting as ig

    class _FakeCompletions:
        def create(self, *args, **kwargs):
            class _Msg:
                content = "Hi Maya — I'm JAI. You mentioned work has been heavy."
            class _Choice:
                message = _Msg()
            class _Resp:
                choices = [_Choice()]
            return _Resp()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ig, "client", _FakeClient())
    upsert_profile(TEST_USER_ID, {"name": "Maya", "emotional": "Running low", "issues": ["Work"]})

    client = _client()
    try:
        body = client.post("/api/conversations/welcome").json()
        assert body["created"] is True
        msgs = _messages(body["id"])
        assert "Maya" in msgs[0]["content"]
    finally:
        _clear_override()
