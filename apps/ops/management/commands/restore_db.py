"""Restore a backup into a NEW database and check it against the live data. Never touches the live database."""

from django.core.management.base import BaseCommand, CommandError

from apps.ops import backup
from apps.ops.checks import latest_backup


class Command(BaseCommand):
    help = "Restore a backup into --into (a new database), then run the record-count check."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Backup file (default: the newest one).")
        parser.add_argument("--into", required=True, help="Name of the new database to restore into.")
        parser.add_argument("--strict", action="store_true", help="Counts must match exactly.")
        parser.add_argument("--no-check", action="store_true")

    def handle(self, *args, **o):
        file = o["file"] or latest_backup()
        if not file:
            raise CommandError("No backup found.")
        try:
            backup.restore(file, o["into"])
            self.stdout.write(f"Restored {file} into database {o['into']}.")
            if o["no_check"]:
                return
            ok, rows = backup.compare_counts(o["into"], strict=o["strict"])
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc
        for table, restored, live, good in rows:
            if not good or o["verbosity"] > 1:
                self.stdout.write(f"{'ok  ' if good else 'FAIL'} {table}: restored {restored}, live {live}")
        self.stdout.write(f"{len(rows)} tables compared. {'PASS' if ok else 'FAIL'}")
        if not ok:
            raise SystemExit(1)
