"""Health checks. Each returns (ok, message). Thresholds come from settings so a drill can trip them."""

import shutil
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.sync.sender import queue_status

BACKUP_GLOB = "hms-*.dump.enc"


def check_database():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 (any failure means the database is not usable)
        return False, f"Database not reachable: {type(exc).__name__}"
    return True, "Database is up"


def check_disk():
    paths = {str(p) for p in (settings.BACKUP_DIR, settings.MEDIA_ROOT, "/") if p and Path(p).exists()}
    worst = None
    for path in paths:
        usage = shutil.disk_usage(path)
        free = 100 * usage.free / usage.total
        if worst is None or free < worst[0]:
            worst = (free, path)
    if worst is None:
        return False, "No disk to check"
    if worst[0] < settings.HEALTH_DISK_MIN_FREE_PERCENT:
        return False, f"Only {worst[0]:.0f}% free on {worst[1]} (limit {settings.HEALTH_DISK_MIN_FREE_PERCENT}%)"
    return True, f"{worst[0]:.0f}% free"


def latest_backup():
    folder = Path(settings.BACKUP_DIR)
    files = sorted(folder.glob(BACKUP_GLOB), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    return files[-1] if files else None


def check_backup():
    if settings.HMS_MODE == "training":
        return True, "Not used on a training copy"
    newest = latest_backup()
    if newest is None:
        return False, "No backup found"
    age_hours = (timezone.now().timestamp() - newest.stat().st_mtime) / 3600
    if age_hours > settings.HEALTH_BACKUP_MAX_HOURS:
        return False, f"Last backup is {age_hours:.0f} hours old (limit {settings.HEALTH_BACKUP_MAX_HOURS})"
    return True, f"Last backup {age_hours:.0f} hours ago"


def check_sync():
    if settings.HMS_MODE != "local" or not (settings.SYNC_CLOUD_URL and settings.SYNC_TOKEN):
        return True, "Sync is not switched on"
    q = queue_status()
    if q["unsent"] > settings.HEALTH_SYNC_MAX_ROWS:
        return False, f"{q['unsent']} changes waiting to reach the cloud (limit {settings.HEALTH_SYNC_MAX_ROWS})"
    if q["oldest_age_seconds"] > settings.HEALTH_SYNC_MAX_AGE_MINUTES * 60:
        return False, f"Oldest waiting change is {q['oldest_age_seconds'] // 60} minutes old"
    return True, f"{q['unsent']} waiting"


CHECKS = {
    "database": check_database,
    "disk": check_disk,
    "backup": check_backup,
    "sync": check_sync,
}


def run_all():
    return {name: fn() for name, fn in CHECKS.items()}
