"""Nightly encrypted backup with retention, then a copy off the server. Run from the worker or cron."""

from django.core.management.base import BaseCommand, CommandError

from apps.ops import backup


class Command(BaseCommand):
    help = "Dump the database, encrypt it, keep 30 daily and 12 monthly copies, copy to the cloud."

    def handle(self, *args, **options):
        try:
            path = backup.make_backup()
        except backup.BackupError as exc:
            raise CommandError(str(exc)) from exc
        removed = backup.prune()
        self.stdout.write(f"Backup written: {path} ({path.stat().st_size} bytes). Pruned {len(removed)} old file(s).")
        try:
            if backup.copy_offsite(path):
                self.stdout.write("Copied to the cloud.")
        except backup.BackupError as exc:
            raise CommandError(f"The local backup is safe, but {exc}") from exc
