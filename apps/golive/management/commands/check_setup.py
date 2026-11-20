from django.core.management.base import BaseCommand, CommandError

from apps.golive import readiness


class Command(BaseCommand):
    help = "Check that the system is set up for real use. Exits with an error if anything is missing."

    def handle(self, *args, **options):
        results = readiness.run()
        for ok, label, detail in results:
            self.stdout.write(f"[{'ok' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
        if not readiness.ready(results):
            raise CommandError("Set-up is not complete.")
        self.stdout.write(self.style.SUCCESS("Set-up looks complete."))
