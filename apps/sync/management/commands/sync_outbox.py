"""Send unsent changes to the cloud copy. Run as the background worker; it never blocks the web server."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.sync.sender import run_once


class Command(BaseCommand):
    help = "Send pending changes to the cloud (loops forever unless --once)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=10, help="Seconds between checks.")

    def handle(self, *args, **options):
        if not (settings.SYNC_CLOUD_URL and settings.SYNC_TOKEN):
            raise CommandError("Set SYNC_CLOUD_URL and SYNC_TOKEN first.")
        while True:
            result = run_once()
            while result["sent"] and not result["error"]:  # drain a backlog without waiting
                result = run_once()
            if result["error"] and result["error"] != "waiting to retry":
                self.stderr.write(f"Sync: {result['error']}")
            if options["once"]:
                return
            time.sleep(options["interval"])
