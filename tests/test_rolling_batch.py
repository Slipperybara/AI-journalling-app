"""Timezone-aware rolling batch (app.batch.process_user_due).

Each user's brief is generated when THEIR local morning arrives, bucketed in
their own timezone. These tests drive the clock with an injected now_utc and
stub the heavy stages (parse / graph / brief), so they need only Postgres —
no Neo4j or OpenAI.
"""
from datetime import datetime, timezone

import app.batch as batch
import app.dashboard_summary as dashboard_summary
import app.morning_brief as morning_brief
from app.db import connect
from app.notifications_prefs import upsert_prefs
from app.time_buckets import bucket_for
from tests.conftest import TEST_USER_ID


def _stub_stages(monkeypatch):
    """Replace the costly stages; return a dict of call-tracking lists."""
    calls = {"parse": [], "graph": [], "maint": [], "brief": [], "dash": []}
    monkeypatch.setattr(batch, "parse_day",
                        lambda day, uid, tz=None: calls["parse"].append((day, tz)) or {"status": "succeeded", "day": day})
    monkeypatch.setattr(batch.graph_batch, "write_day",
                        lambda day, uid: calls["graph"].append(day) or {"status": "ok"})
    monkeypatch.setattr(batch.graph_maintenance, "run",
                        lambda uid, tz=None: calls["maint"].append(tz) or {"events_merged": 0})
    monkeypatch.setattr(morning_brief, "post_morning_brief",
                        lambda day, uid: calls["brief"].append(day) or {"status": "posted", "conversation_id": 1})
    monkeypatch.setattr(dashboard_summary, "refresh_dashboard_summary",
                        lambda uid, tz=None: calls["dash"].append(tz) or {"status": "refreshed"})
    return calls


def _seed_parse_log(day, status="succeeded"):
    with connect() as conn:
        conn.execute(
            "INSERT INTO parse_log (user_id, day, status, parsed_at) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (user_id, day) DO UPDATE SET status=excluded.status",
            (str(TEST_USER_ID), day, status, datetime.now(timezone.utc).isoformat()),
        )


def _seed_brief_log(day, status="posted"):
    with connect() as conn:
        conn.execute(
            "INSERT INTO morning_brief_log (user_id, day, posted_at, conversation_id, status) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (user_id, day) DO UPDATE SET status=excluded.status",
            (str(TEST_USER_ID), day, datetime.now(timezone.utc).isoformat(), 7, status),
        )


def test_skips_before_local_day_boundary(monkeypatch):
    calls = _stub_stages(monkeypatch)
    # 20:00 UTC = 04:00 next day in Singapore (+8) — before the 06:00 boundary.
    now = datetime(2026, 6, 28, 20, 0, tzinfo=timezone.utc)
    upsert_prefs(TEST_USER_ID, enabled=True, hour=8, minute=0, tz="Asia/Singapore")

    res = batch.process_user_due(TEST_USER_ID, now)
    assert res["status"] == "before_local_boundary"
    assert calls["parse"] == [] and calls["brief"] == []


def test_generates_when_local_morning_arrives(monkeypatch):
    calls = _stub_stages(monkeypatch)
    # 12:00 UTC = 08:00 America/New_York (EDT, -4) — past the boundary, no brief yet.
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    tz = "America/New_York"
    upsert_prefs(TEST_USER_ID, enabled=True, hour=8, minute=0, tz=tz)
    yest = (bucket_for(now, tz)).fromordinal(bucket_for(now, tz).toordinal() - 1).isoformat()

    res = batch.process_user_due(TEST_USER_ID, now)
    assert res["parse"]["status"] == "succeeded"
    # Parsed the user's LOCAL yesterday, in their tz; graph + brief ran.
    assert calls["parse"] == [(yest, tz)]
    assert calls["graph"] == [yest]
    assert calls["maint"] == [tz]
    assert calls["brief"] and calls["dash"] == [tz]


def test_already_done_is_noop(monkeypatch):
    calls = _stub_stages(monkeypatch)
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    tz = "America/New_York"
    upsert_prefs(TEST_USER_ID, enabled=True, hour=8, minute=0, tz=tz)
    today = bucket_for(now, tz).isoformat()
    _seed_brief_log(today, status="posted")

    res = batch.process_user_due(TEST_USER_ID, now)
    assert res["status"] == "already_done"
    assert calls["parse"] == [] and calls["brief"] == []


def test_graph_skipped_when_day_already_parsed(monkeypatch):
    """If yesterday is already parsed, don't re-parse or re-run the graph
    pipeline (keeps the gpt-4o categorize cost to once/day) — but still post
    the brief if it hasn't gone out."""
    calls = _stub_stages(monkeypatch)
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)
    tz = "America/New_York"
    upsert_prefs(TEST_USER_ID, enabled=True, hour=8, minute=0, tz=tz)
    today = bucket_for(now, tz)
    yest = today.fromordinal(today.toordinal() - 1).isoformat()
    _seed_parse_log(yest, status="succeeded")

    res = batch.process_user_due(TEST_USER_ID, now)
    assert res["parse"]["status"] == "already_succeeded"
    assert calls["parse"] == []   # not re-parsed
    assert calls["graph"] == []   # graph pipeline skipped (no new day)
    assert calls["brief"]         # brief still posted
