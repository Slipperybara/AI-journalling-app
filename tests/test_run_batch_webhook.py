"""Phase 4: tests for POST /api/admin/run-batch.

HMAC-protected webhook hit by the GitHub Actions cron. No user auth (the
cron has no Supabase session) — caller must present the shared
X-Webhook-Secret header.
"""
import pytest
from fastapi.testclient import TestClient

from app.core import settings
from app.routers import admin as admin_router
from main import app
from tests.conftest import TEST_USER_ID, TEST_USER_ID_B


SECRET = "test-batch-webhook-secret"


@pytest.fixture
def webhook_enabled(monkeypatch):
    """Pin BATCH_WEBHOOK_SECRET and replace every pipeline call with a stub
    so the test is hermetic — no real OpenAI / Neo4j / Postgres mutation."""
    prev = settings.batch_webhook_secret
    settings.batch_webhook_secret = SECRET

    def fake_get_user_ids():
        return [TEST_USER_ID, TEST_USER_ID_B]

    monkeypatch.setattr(admin_router, "get_all_user_ids_with_messages", fake_get_user_ids)
    monkeypatch.setattr(admin_router, "parse_day",
                        lambda day, uid: {"status": "succeeded", "day": day, "user_id": str(uid), "messages": 3})
    monkeypatch.setattr(admin_router.graph_batch, "write_day",
                        lambda day, uid: {"status": "ok", "day": day, "user_id": str(uid), "events": 1})
    monkeypatch.setattr(admin_router.graph_maintenance, "run",
                        lambda uid: {"events_merged": 0, "topics_merged": 0, "goals_merged": 0})
    monkeypatch.setattr(admin_router.morning_brief, "post_morning_brief",
                        lambda day, uid: {"status": "posted", "day": day, "conversation_id": 42})

    yield
    settings.batch_webhook_secret = prev


def test_503_when_secret_not_configured():
    """Misconfigured server (empty secret) refuses every request."""
    prev = settings.batch_webhook_secret
    settings.batch_webhook_secret = ""
    try:
        with TestClient(app) as client:
            r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": "anything"})
        assert r.status_code == 503
        assert "not configured" in r.json()["detail"]
    finally:
        settings.batch_webhook_secret = prev


def test_401_when_secret_missing(webhook_enabled):
    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch")
    assert r.status_code == 401


def test_401_when_secret_wrong(webhook_enabled):
    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": "wrong-secret"})
    assert r.status_code == 401


def test_200_with_correct_secret_runs_pipeline(webhook_enabled):
    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": SECRET})
    assert r.status_code == 200
    body = r.json()
    assert body["users_processed"] == 2
    assert set(body["results"].keys()) == {str(TEST_USER_ID), str(TEST_USER_ID_B)}
    # Every user gets all four phases tracked.
    for per_user in body["results"].values():
        assert per_user["parse"]["status"] == "succeeded"
        assert per_user["graph"]["status"] == "ok"
        assert per_user["morning_brief"]["status"] == "posted"


def test_200_with_no_users(webhook_enabled, monkeypatch):
    """Empty user set is a valid no-op — webhook returns 200 with 0 processed."""
    monkeypatch.setattr(admin_router, "get_all_user_ids_with_messages", lambda: [])
    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["users_processed"] == 0
    assert r.json()["results"] == {}


def test_per_user_pipeline_failure_does_not_break_others(webhook_enabled, monkeypatch):
    """If parse_day raises for one user, the response still summarizes that
    user with status=failed and the OTHER users are processed normally."""
    def parse_day_one_explodes(day, uid):
        if uid == TEST_USER_ID:
            raise RuntimeError("boom")
        return {"status": "succeeded", "day": day, "user_id": str(uid), "messages": 1}

    monkeypatch.setattr(admin_router, "parse_day", parse_day_one_explodes)

    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": SECRET})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[str(TEST_USER_ID)]["parse"]["status"] == "failed"
    assert "boom" in results[str(TEST_USER_ID)]["parse"]["error"]
    assert results[str(TEST_USER_ID_B)]["parse"]["status"] == "succeeded"
