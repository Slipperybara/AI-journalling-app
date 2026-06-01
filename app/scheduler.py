"""APScheduler lifecycle for the daily batch parse.

Gated behind `settings.run_inline_scheduler` (Phase 4). When true (local dev
default), APScheduler runs in-process: a 06:00 local cron + a startup
catch-up sweep. When false (Render production), neither fires — the batch
is driven externally by a GitHub Actions cron hitting
`POST /api/admin/run-batch`.
"""
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .batch import catch_up_parses, run_scheduled_batch
from .core import settings


_scheduler = BackgroundScheduler()


def _startup_parses() -> None:
    catch_up_parses()


def start() -> None:
    if not settings.run_inline_scheduler:
        print("[scheduler] RUN_INLINE_SCHEDULER=false — in-process cron + catch-up disabled")
        return
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
