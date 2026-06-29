"""Admin endpoints for manual batch triggering and parse inspection.

Phase 2: per-user endpoints take `user_id` via the auth dependency and
scope all reads/writes by it.

Phase 4: adds `/api/admin/run-batch` — HMAC-protected, no user auth — for
the external GitHub Actions cron. Iterates every user with data and runs
the full per-user pipeline (parse yesterday + reconcile graph + post
today's morning brief).
"""
import hmac
import traceback
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import analytics, morning_brief, notify_delivery
from ..auth import get_current_user_id
from ..batch import parse_day, process_user_due
from ..core import settings
from ..day_messages import get_all_user_ids_with_messages
from ..notifications_prefs import get_user_tz


router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/run-batch")
async def run_batch_webhook(
    x_webhook_secret: Optional[str] = Header(default=None, alias="X-Webhook-Secret"),
):
    """External cron entrypoint (GitHub Actions). HMAC-protected; no user auth.

    For every user with messages, parses yesterday's bucket into Postgres,
    projects into Neo4j, runs maintenance, posts today's morning brief.
    Idempotent — running it twice the same day re-uses already-parsed days
    and the existing morning brief (see the dedup table in the plan).

    Returns a per-user summary so the GitHub Action log captures what fired.
    """
    if not settings.batch_webhook_secret:
        # Misconfiguration safety: never accept any header when no secret is
        # configured. Avoids accidental exposure on a server that meant to
        # rotate the secret to empty.
        raise HTTPException(
            status_code=503,
            detail="batch webhook secret not configured",
        )
    if not x_webhook_secret or not hmac.compare_digest(
        x_webhook_secret, settings.batch_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    # Hourly cron: for each user, run the timezone-aware due-check. A user's
    # pipeline fires once their local morning arrives; other ticks no-op.
    now_utc = datetime.now(timezone.utc)
    user_ids = get_all_user_ids_with_messages()
    results: dict[str, dict] = {}

    for uid in user_ids:
        try:
            results[str(uid)] = process_user_due(uid, now_utc)
        except Exception as exc:
            traceback.print_exc()
            results[str(uid)] = {"status": "failed", "error": str(exc)}

    parse_successes = sum(
        1 for r in results.values() if r.get("parse", {}).get("status") == "succeeded"
    )
    briefs_posted = sum(
        1 for r in results.values() if r.get("morning_brief", {}).get("status") == "posted"
    )
    analytics.capture(
        "batch_pipeline",
        "nightly_batch_completed",
        {
            "users_processed": len(user_ids),
            "parse_succeeded": parse_successes,
            "morning_briefs_posted": briefs_posted,
        },
    )
    return {
        "ran_at": now_utc.isoformat(),
        "users_processed": len(user_ids),
        "results": results,
    }


@router.post("/send-due-notifications")
async def send_due_notifications_webhook(
    x_webhook_secret: Optional[str] = Header(default=None, alias="X-Webhook-Secret"),
):
    """Frequent cron entrypoint (every ~15 min). HMAC-protected; no user auth.

    Sends each user's morning-brief push once their local clock reaches their
    chosen time. Idempotent — dedup is per brief-day, so extra ticks are no-ops.
    """
    if not settings.batch_webhook_secret:
        raise HTTPException(status_code=503, detail="batch webhook secret not configured")
    if not x_webhook_secret or not hmac.compare_digest(
        x_webhook_secret, settings.batch_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    return notify_delivery.send_due_briefs()


@router.post("/parse-day/{day}")
async def trigger_parse_day(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Manually parse a single day-bucket for the current user. `day` is
    'YYYY-MM-DD'. Idempotent — existing rows for the (user, day) are replaced.
    `day` is interpreted in the user's local timezone."""
    try:
        return parse_day(day, user_id, get_user_tz(user_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse failed: {e}")


@router.post("/morning-brief/{day}")
async def trigger_morning_brief(day: str, user_id: UUID = Depends(get_current_user_id)):
    """Manually run the morning brief for one day-bucket for the current user.
    Idempotent via morning_brief_log — returns the existing conversation_id
    on re-trigger."""
    try:
        return morning_brief.post_morning_brief(day, user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Morning brief failed: {e}")


