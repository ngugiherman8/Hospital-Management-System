"""Run the background jobs forever. This is the one process the hospital server needs besides the website."""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.ops import worker


class Command(BaseCommand):
    help = "Background worker: sync, health checks, nightly backup, nightly bed-day charges."

    def handle(self, *args, **options):
        last = {}
        self.stdout.write("Worker started.")
        while True:
            now = timezone.localtime()
            for name in worker.due_jobs(now, last):
                last[name] = now  # set first: a failing job is retried at its next turn, not in a tight loop
                if not worker.run_job(name):
                    self.stderr.write(f"Job {name} failed; see the log.")
            time.sleep(5)
