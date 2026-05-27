"""APScheduler lifecycle for the daily batch parse.

Runs the batch once per day at `settings.day_boundary_hour` (default 06:00
local). The catch-up sweep is kicked off in a background thread at startup so
it doesn't block app startup if many days need reparsing.
"""
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .batch import catch_up_parses, run_scheduled_batch
from .core import settings


_scheduler = BackgroundScheduler()


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
    threading.Thread(target=catch_up_parses, daemon=True).start()


def stop() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
