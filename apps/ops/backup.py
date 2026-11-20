"""Encrypted database backups, restore, and a record-count check. Needs pg_dump, pg_restore and openssl."""

import datetime
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import connection

from .checks import BACKUP_GLOB


class BackupError(Exception):
    pass


def _pg_env(db=None):
    cfg = settings.DATABASES["default"]
    env = dict(os.environ, PGPASSWORD=str(cfg.get("PASSWORD") or ""))
    args = ["-h", str(cfg.get("HOST") or "localhost"), "-p", str(cfg.get("PORT") or 5432), "-U", str(cfg["USER"])]
    return env, args, db or cfg["NAME"]


def _key_file():
    key = settings.BACKUP_KEY_FILE
    if not key or not Path(key).is_file():
        raise BackupError("BACKUP_KEY_FILE is not set or does not exist. Backups are always encrypted.")
    return key


def _run(cmd, env=None, stdin=None, stdout=None):
    result = subprocess.run(cmd, env=env, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE)  # noqa: S603
    if result.returncode:
        raise BackupError(f"{cmd[0]} failed: {result.stderr.decode(errors='replace').strip()[:300]}")


def make_backup(now=None):
    """Dump, encrypt, and write the file. The old backups are only pruned after this succeeded."""
    now = now or datetime.datetime.now()
    folder = Path(settings.BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"hms-{now:%Y%m%d-%H%M%S}.dump.enc"
    partial = final.with_suffix(".part")
    env, conn, name = _pg_env()
    key = _key_file()
    try:
        dump = subprocess.Popen(  # noqa: S603
            ["pg_dump", *conn, "-Fc", name], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        with open(partial, "wb") as out:
            enc = subprocess.run(  # noqa: S603
                ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"file:{key}"],
                stdin=dump.stdout, stdout=out, stderr=subprocess.PIPE,
            )  # fmt: skip
        dump.stdout.close()
        dump_err = dump.stderr.read().decode(errors="replace")
        if dump.wait() or enc.returncode:
            raise BackupError(f"Backup failed: {dump_err.strip()[:200]} {enc.stderr.decode(errors='replace')[:100]}")
        if partial.stat().st_size == 0:
            raise BackupError("Backup file is empty.")
        partial.rename(final)
    except (OSError, BackupError) as exc:
        partial.unlink(missing_ok=True)  # never leave half a backup; the older ones stay
        raise BackupError(str(exc)) from exc
    return final


def prune(now=None, daily=30, monthly=12):
    """Keep the newest `daily` backups plus the first backup of each of the last `monthly` months."""
    files = sorted(Path(settings.BACKUP_DIR).glob(BACKUP_GLOB))
    keep = set(files[-daily:])
    first_of_month = {}
    for f in files:
        first_of_month.setdefault(f.name[4:10], f)  # hms-YYYYMM...
    for month in sorted(first_of_month)[-monthly:]:
        keep.add(first_of_month[month])
    removed = [f for f in files if f not in keep]
    for f in removed:
        f.unlink()
    return removed


def copy_offsite(path):
    """Run BACKUP_COPY_COMMAND, e.g. `rclone copy {file} remote:hms-backups`. Does nothing when not set."""
    template = settings.BACKUP_COPY_COMMAND
    if not template:
        return False
    result = subprocess.run(template.format(file=str(path)), shell=True, capture_output=True)  # noqa: S602
    if result.returncode:
        raise BackupError(f"Copy to the cloud failed: {result.stderr.decode(errors='replace')[:200]}")
    return True


def restore(file, target_db, create=True):
    """Decrypt a backup and restore it into `target_db`, which is created empty first."""
    key = _key_file()
    env, conn, live = _pg_env()
    if target_db == live:
        raise BackupError("Refusing to restore over the live database.")
    if create:
        _run(["createdb", *conn, target_db], env=env)
    dec = subprocess.Popen(  # noqa: S603
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{key}", "-in", str(file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    restored = subprocess.run(  # noqa: S603
        ["pg_restore", *conn, "--no-owner", "-d", target_db], stdin=dec.stdout, env=env, stderr=subprocess.PIPE
    )
    dec.stdout.close()
    if dec.wait():
        raise BackupError("Could not decrypt the backup (wrong key or damaged file).")
    if restored.returncode:
        raise BackupError(f"pg_restore failed: {restored.stderr.decode(errors='replace')[:300]}")


def table_counts(db_name):
    env, conn, _ = _pg_env()
    tables = [t for t in connection.introspection.table_names() if not t.startswith("django_session")]
    counts = {}
    for table in sorted(tables):
        out = subprocess.run(  # noqa: S603
            ["psql", *conn, "-d", db_name, "-At", "-c", f'SELECT count(*) FROM "{table}"'],
            env=env, capture_output=True,
        )  # fmt: skip
        counts[table] = int(out.stdout.strip() or -1) if out.returncode == 0 else -1
    return counts


def compare_counts(restored_db, strict=False):
    """Compare every table in the restored copy with the live data.

    Live data may have grown since the backup, so by default the restored copy may have fewer rows but never more.
    `strict` requires equal counts (use it on a copy taken from the same moment).
    """
    live = table_counts(settings.DATABASES["default"]["NAME"])
    copy = table_counts(restored_db)
    rows, ok = [], True
    for table in live:
        a, b = copy.get(table, -1), live[table]
        good = a == b if strict else 0 <= a <= b
        ok &= good
        rows.append((table, a, b, good))
    return ok, rows
