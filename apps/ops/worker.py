"""The background worker: sync to the cloud, health checks, nightly backup, and nightly bed-day charges."""

import datetime
import logging

from django.conf import settings
from django.core.management import call_command

log = logging.getLogger(__name__)

# name -> (how often, or the time of day it runs)
SCHEDULE = {
    "sync": datetime.timedelta(seconds=10),
    "checks": datetime.timedelta(minutes=5),
    "bed_nights": datetime.time(0, 10),
    "backup": datetime.time(2, 0),
}


def _due(name, now, last):
    rule = SCHEDULE[name]
    if isinstance(rule, datetime.timedelta):
        return last is None or now - last >= rule
    todays = datetime.datetime.combine(now.date(), rule, tzinfo=now.tzinfo)
    return now >= todays and (last is None or last < todays)


def due_jobs(now, last_run):
    """Which jobs should run now, given when each last ran (a dict of name -> datetime)."""
    jobs = [n for n in SCHEDULE if _due(n, now, last_run.get(n))]
    if not (settings.SYNC_CLOUD_URL and settings.SYNC_TOKEN) or settings.HMS_MODE != "local":
        jobs = [j for j in jobs if j != "sync"]
    if settings.HMS_MODE != "local":
        jobs = [j for j in jobs if j not in ("bed_nights", "backup")]
    return jobs


def run_job(name):
    """Run one job. A failing job is logged and never stops the worker."""
    try:
        if name == "sync":
            from apps.sync.sender import run_once

            result = run_once()
            while result["sent"] and not result["error"]:
                result = run_once()
        elif name == "checks":
            from apps.ops.alerts import process

            process()
        elif name == "bed_nights":
            call_command("post_bed_nights")
        elif name == "backup":
            call_command("backup_db")
        return True
    except Exception:  # noqa: BLE001
        log.exception("Worker job %s failed", name)
        return False
