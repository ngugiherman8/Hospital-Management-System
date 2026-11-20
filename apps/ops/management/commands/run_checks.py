"""Run the health checks and alert IT about any that fail. Run every few minutes (the worker does)."""

from django.core.management.base import BaseCommand

from apps.ops.alerts import process
from apps.ops.checks import run_all


class Command(BaseCommand):
    help = "Run health checks; email or text IT when one fails. Exit code 1 when anything fails."

    def handle(self, *args, **options):
        results = run_all()
        failing = process(results)
        for name, (ok, message) in results.items():
            self.stdout.write(f"{'OK  ' if ok else 'FAIL'} {name}: {message}")
        if failing:
            raise SystemExit(1)
