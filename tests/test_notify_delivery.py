"""Morning-brief delivery: fires at the user's local time, carries the stored
push body, dedups per brief-day, and respects enabled + staleness."""
from datetime import datetime, timezone

from app import notify_delivery
from app.db import connect
from app.notifications_prefs import get_prefs, upsert_prefs
from app.time_buckets import bucket_for
from tests.conftest import TEST_USER_ID

# A fixed afternoon UTC moment so "08:00 local (UTC)" is already due.
NOW = datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc)


def _seed_posted_brief(day: str, push_body: str = "Yesterday, you were feeling calm — your reflection's ready."):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO morning_brief_log (user_id, day, posted_at, conversation_id, status, push_body)
            VALUES (%s, %s, %s, %s, 'posted', %s)
            ON CONFLICT (user_id, day) DO UPDATE SET status='posted', push_body=excluded.push_body
            """,
            (str(TEST_USER_ID), day, NOW.isoformat(), 123, push_body),
        )


def _capture_pushes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notify_delivery.push, "send_push_to_user",
        lambda uid, title, body, data=None: calls.append({"uid": uid, "body": body, "data": data}) or {"sent": 1},
    )
    return calls


def test_due_brief_is_sent_once_with_stored_body(monkeypatch):
    day = bucket_for(NOW).isoformat()
    _seed_posted_brief(day)
    upsert_prefs(TEST_USER_ID, enabled=True, hour=8, minute=0, tz="UTC")
    calls = _capture_pushes(monkeypatch)

    first = notify_delivery.send_due_briefs(now_utc=NOW)
    assert first["sent"] == 1
    assert calls[0]["body"].startswith("Yesterday, you were feeling")
    assert calls[0]["data"]["type"] == "morning_brief"
    # dedup: a second sweep for the same brief-day sends nothing
    second = notify_delivery.send_due_briefs(now_utc=NOW)
    assert second["sent"] == 0
    assert len(calls) == 1
    assert get_prefs(TEST_USER_ID)["last_pushed_day"] == day


def test_not_sent_before_local_time(monkeypatch):
    day = bucket_for(NOW).isoformat()
    _seed_posted_brief(day)
    # 23:30 local — NOW (18:00 UTC) is before it, so not due yet
    upsert_prefs(TEST_USER_ID, enabled=True, hour=23, minute=30, tz="UTC")
    calls = _capture_pushes(monkeypatch)
    assert notify_delivery.send_due_briefs(now_utc=NOW)["sent"] == 0
    assert calls == []


def test_disabled_user_is_skipped(monkeypatch):
    day = bucket_for(NOW).isoformat()
    _seed_posted_brief(day)
    upsert_prefs(TEST_USER_ID, enabled=False, hour=8, minute=0, tz="UTC")
    calls = _capture_pushes(monkeypatch)
    assert notify_delivery.send_due_briefs(now_utc=NOW)["sent"] == 0
    assert calls == []


def test_time_floor_is_enforced():
    # 5:00 AM is before the 06:30 local floor → clamped up.
    p = upsert_prefs(TEST_USER_ID, enabled=True, hour=5, minute=0, tz="UTC")
    assert (p["hour"], p["minute"]) == (6, 30)
