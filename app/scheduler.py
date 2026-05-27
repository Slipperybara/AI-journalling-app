"""APScheduler lifecycle for the daily batch parse.

Runs the batch once per day at `settings.day_boundary_hour` (default 06:00
local). On startup, kicks off a background thread that first runs the
one-shot backfill (if `init_db` migrated the extraction tables) and then the
normal 7-day catch-up sweep. Both run off the request thread so app startup
isn't blocked.
"""
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db
from .batch import backfill_all_message_days, catch_up_parses, run_scheduled_batch
from .core import settings


_scheduler = BackgroundScheduler()


def _startup_parses() -> None:
    if db.migration_ran:
        count = backfill_all_message_days()
        print(f"[scheduler] backfill complete: {count} day(s)")
    catch_up_parses()


def start() -> None:
    if _scheduler.running:
        return
    _scheduler.add_job(
        run_scheduled_batch,
        CronTrigger(hour=settings.day_boundary_hour, minute=0),
        id="daily_batch_parse",
        replace_existing=True,
    )
    _scheduler.start()
    threading.Thread(target=_startup_parses, daemon=True).start()


def stop() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
