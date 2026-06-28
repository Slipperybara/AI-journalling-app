"""Delivers the morning-brief push at each user's chosen local time.

Generation and delivery are decoupled: the nightly batch generates the brief
and stores its `push_body`; a frequent cron (every ~15 min) calls
`send_due_briefs`, which pushes the latest fresh brief to each user once their
local clock has reached their chosen time. Dedup is per brief-`day` via
`notification_prefs.last_pushed_day`, so re-running the cron never double-sends.
"""
import traceback
from datetime import datetime, timedelta, timezone
from uuid import UUID

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9
    ZoneInfo = None  # type: ignore

from . import push
from .db import connect
from .notifications_prefs import mark_pushed
from .time_buckets import bucket_for


def _local_now(now_utc: datetime, tz: str) -> datetime:
    if ZoneInfo is None:
        return now_utc
    try:
        return now_utc.astimezone(ZoneInfo(tz))
    except Exception:
        return now_utc.astimezone(ZoneInfo("UTC"))


def _latest_fresh_brief(user_id: UUID, min_day: str) -> dict | None:
    """The most recent posted brief with a push body, no older than `min_day`.
    The floor stops a long-dormant user from getting an ancient brief pushed if
    they only just enabled notifications."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT day, push_body, conversation_id FROM morning_brief_log
            WHERE user_id = %s AND status = 'posted'
              AND push_body IS NOT NULL AND day >= %s
            ORDER BY day DESC LIMIT 1
            """,
            (str(user_id), min_day),
        ).fetchone()
    return dict(row) if row else None


def send_due_briefs(now_utc: datetime | None = None) -> dict:
    """Send any morning briefs that are now due. Returns a small summary."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    with connect() as conn:
        prefs = conn.execute(
            "SELECT user_id, hour, minute, tz, last_pushed_day "
            "FROM notification_prefs WHERE enabled = TRUE"
        ).fetchall()

    sent = 0
    considered = 0
    for p in prefs:
        considered += 1
        try:
            uid = p["user_id"]
            local_now = _local_now(now_utc, p["tz"])
            scheduled = local_now.replace(
                hour=int(p["hour"]), minute=int(p["minute"]), second=0, microsecond=0
            )
            if local_now < scheduled:
                continue  # their chosen time hasn't arrived yet today

            # Only push briefs from the user's local today/yesterday — never a
            # stale one. Computed per-user in their timezone.
            min_day = (bucket_for(now_utc, p["tz"]) - timedelta(days=1)).isoformat()
            brief = _latest_fresh_brief(uid, min_day)
            if not brief:
                continue
            if p["last_pushed_day"] == brief["day"]:
                continue  # already pushed this brief

            body = (brief["push_body"] or "").strip() or "Your reflection from yesterday is ready."
            push.send_push_to_user(
                uid,
                title="Good morning",
                body=body,
                data={"type": "morning_brief", "conversation_id": brief["conversation_id"]},
            )
            mark_pushed(uid, brief["day"])
            sent += 1
        except Exception:  # pragma: no cover - one user must not break the sweep
            traceback.print_exc()

    return {"considered": considered, "sent": sent}
