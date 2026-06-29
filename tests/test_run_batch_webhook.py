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


def _full_pipeline_result(uid):
    """What process_user_due returns when a user's local morning has arrived and
    every stage runs."""
    return {
        "tz": "UTC",
        "parse": {"status": "succeeded"},
        "graph": {"status": "ok"},
        "maintenance": {"events_merged": 0},
        "morning_brief": {"status": "posted", "conversation_id": 42},
        "dashboard_summary": {"status": "refreshed", "summary": "stub summary"},
    }


@pytest.fixture
def webhook_enabled(monkeypatch):
    """Pin BATCH_WEBHOOK_SECRET and stub the per-user pipeline so the test is
    hermetic — no real OpenAI / Neo4j / Postgres mutation. The webhook now
    delegates each user to batch.process_user_due (timezone-aware due-check)."""
    prev = settings.batch_webhook_secret
    settings.batch_webhook_secret = SECRET

    def fake_get_user_ids():
        return [TEST_USER_ID, TEST_USER_ID_B]

    monkeypatch.setattr(admin_router, "get_all_user_ids_with_messages", fake_get_user_ids)
    monkeypatch.setattr(admin_router, "process_user_due",
                        lambda uid, now_utc: _full_pipeline_result(uid))

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
        assert per_user["dashboard_summary"]["status"] == "refreshed"


def test_200_with_no_users(webhook_enabled, monkeypatch):
    """Empty user set is a valid no-op — webhook returns 200 with 0 processed."""
    monkeypatch.setattr(admin_router, "get_all_user_ids_with_messages", lambda: [])
    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": SECRET})
    assert r.status_code == 200
    assert r.json()["users_processed"] == 0
    assert r.json()["results"] == {}


def test_per_user_pipeline_failure_does_not_break_others(webhook_enabled, monkeypatch):
    """If process_user_due raises for one user, the webhook still summarizes that
    user with status=failed and the OTHER users are processed normally."""
    def one_explodes(uid, now_utc):
        if uid == TEST_USER_ID:
            raise RuntimeError("boom")
        return _full_pipeline_result(uid)

    monkeypatch.setattr(admin_router, "process_user_due", one_explodes)

    with TestClient(app) as client:
        r = client.post("/api/admin/run-batch", headers={"X-Webhook-Secret": SECRET})
    assert r.status_code == 200
    results = r.json()["results"]
    assert results[str(TEST_USER_ID)]["status"] == "failed"
    assert "boom" in results[str(TEST_USER_ID)]["error"]
    assert results[str(TEST_USER_ID_B)]["parse"]["status"] == "succeeded"
